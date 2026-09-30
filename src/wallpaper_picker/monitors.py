"""Monitor discovery.

Several compositors are supported, tried in this order:

1. ``cosmic-randr list``      -- COSMIC (this project's origin)
2. ``wlr-randr --json``       -- wlroots compositors (sway, Hyprland, Wayfire)
3. ``xrandr --query``         -- X11, and XWayland under any compositor
4. ``/sys/class/drm``         -- names only; needs cosmic-randr/wlr-randr x positions

The order matters: the first tool that reports something wins, so a desktop with more
than one of these installed still gets the most precise geometry available. The /sys
fallback is deliberately last because it knows the output names but not where the outputs
sit relative to each other, which the Left/Right/Centre labelling needs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass
class Monitor:
    name: str
    make: str = ""
    model: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    refresh: float = 0.0
    scale: float = 1.0
    enabled: bool = True
    position: str = ""  # Left / Right / Centre / Monitor N (filled by label_positions)

    @property
    def pretty(self) -> str:
        make = self.make.split()[0] if self.make else ""
        model = self.model.strip()
        if make and model.lower().startswith(make.lower()):
            return model
        return " ".join(p for p in (make, model) if p) or self.name

    @property
    def label(self) -> str:
        pos = f"{self.position} · " if self.position else ""
        return f"{pos}{self.pretty} ({self.name})"

    def to_dict(self) -> dict:
        return asdict(self)


def parse_cosmic_randr(text: str) -> list[Monitor]:
    text = ANSI.sub("", text)
    mons: list[Monitor] = []
    cur: Monitor | None = None
    in_modes = False
    for line in text.splitlines():
        head = re.match(r"^(\S+)\s+\((enabled|disabled)\)", line)
        if head:
            cur = Monitor(name=head.group(1), enabled=head.group(2) == "enabled")
            mons.append(cur)
            in_modes = False
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("Modes:"):
            in_modes = True
            continue
        if in_modes:
            m = re.match(r"(\d+)x(\d+)\s*@\s*([\d.]+)\s*Hz.*\(current\)", s)
            if m:
                cur.width, cur.height, cur.refresh = int(m.group(1)), int(m.group(2)), float(m.group(3))
            continue
        kv = re.match(r"^([A-Za-z ]+):\s*(.*)$", s)
        if not kv:
            continue
        key, val = kv.group(1).strip().lower(), kv.group(2).strip()
        if key == "make":
            cur.make = val
        elif key == "model":
            cur.model = val
        elif key == "position":
            m = re.match(r"(-?\d+)\s*,\s*(-?\d+)", val)
            if m:
                cur.x, cur.y = int(m.group(1)), int(m.group(2))
        elif key == "scale":
            m = re.match(r"([\d.]+)\s*%", val)
            if m:
                cur.scale = float(m.group(1)) / 100.0
    return mons


def parse_wlr_randr(text: str) -> list[Monitor]:
    """Parse ``wlr-randr --json`` output (an array of output objects)."""
    import json

    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    mons: list[Monitor] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if not name:
            continue
        enabled = bool(entry.get("enabled", True))
        mode = entry.get("current_mode") or {}
        scale = entry.get("scale") or 1.0
        pos = entry.get("position") or {}
        # make/model arrive as separate fields on some versions and nested on others
        make = entry.get("make") or ""
        model = entry.get("model") or entry.get("description") or ""
        if not make and isinstance(model, str) and " " in model:
            make, _, model = model.partition(" ")
        mons.append(Monitor(
            name=name,
            make=str(make),
            model=str(model),
            x=int(pos.get("x", 0) or 0),
            y=int(pos.get("y", 0) or 0),
            width=int(mode.get("width", 0) or 0),
            height=int(mode.get("height", 0) or 0),
            refresh=float(mode.get("refresh", 0.0) or 0.0),
            scale=float(scale or 1.0),
            enabled=enabled and not bool(entry.get("disabled")),
        ))
    return mons


def parse_xrandr(text: str) -> list[Monitor]:
    """Parse ``xrandr --query`` output, which works on X11 and under XWayland."""
    mons: list[Monitor] = []
    for line in text.splitlines():
        # HDMI-A-1 connected primary 2133x1200+2133+0 (normal ...) 480mm x 270mm
        head = re.match(
            r"^(\S+)\s+(connected|disconnected)(?:\s+(primary))?\s*(.*)$", line)
        if not head:
            continue
        name, state, _primary, rest = head.groups()
        mon = Monitor(name=name, enabled=state == "connected")
        geom = re.search(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", rest)
        if geom:
            mon.width, mon.height = int(geom.group(1)), int(geom.group(2))
            mon.x, mon.y = int(geom.group(3)), int(geom.group(4))
        # a refresh rate in the trailing "480mm x 270mm" part is not present; the current
        # mode's rate follows on the next lines, marked with '*'
        mons.append(mon)

    # second pass: read the current mode's refresh from the mode lines
    current: Monitor | None = None
    by_name = {m.name: m for m in mons}
    for line in text.splitlines():
        head = re.match(r"^(\S+)\s+(connected|disconnected)", line)
        if head:
            current = by_name.get(head.group(1))
            continue
        if current is None:
            continue
        mode = re.match(r"^\s+(\d+)x(\d+)\s+([\d.]+)(\*)?", line)
        if mode and mode.group(4):          # '*' marks the active mode
            if not current.width:
                current.width, current.height = int(mode.group(1)), int(mode.group(2))
            current.refresh = float(mode.group(3))
    return mons


def label_positions(mons: list[Monitor]) -> list[Monitor]:
    """Sort enabled monitors left-to-right and name them Left/Centre/Right."""
    enabled = sorted((m for m in mons if m.enabled), key=lambda m: (m.x, m.y, m.name))
    names: list[str]
    if len(enabled) == 1:
        names = [""]
    elif len(enabled) == 2:
        names = ["Left", "Right"]
    elif len(enabled) == 3:
        names = ["Left", "Centre", "Right"]
    else:
        names = [f"Monitor {i + 1}" for i in range(len(enabled))]
    for m, n in zip(enabled, names):
        m.position = n
    return enabled


def _drm_fallback(sys_drm: Path = Path("/sys/class/drm")) -> list[Monitor]:
    mons = []
    try:
        for d in sorted(sys_drm.iterdir()):
            m = re.match(r"card\d+-(.+)$", d.name)
            if not m or m.group(1).startswith("Writeback"):
                continue
            try:
                if (d / "status").read_text().strip() == "connected":
                    mons.append(Monitor(name=m.group(1)))
            except OSError:
                pass
    except OSError:
        pass
    return mons


# Each entry: (executable, argv, parser). The first that yields monitors wins.
PROVIDERS = (
    ("cosmic-randr", ("list",), parse_cosmic_randr),
    ("wlr-randr", ("--json",), parse_wlr_randr),
    ("xrandr", ("--query",), parse_xrandr),
)


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def list_monitors() -> list[Monitor]:
    """Enabled monitors, left to right, with Left/Right labels."""
    for tool, args, parser in PROVIDERS:
        exe = shutil.which(tool)
        if not exe:
            continue
        mons = parser(_run([exe, *args]))
        if mons:
            return label_positions(mons)
    return label_positions(_drm_fallback())


def signature(mons: list[Monitor]) -> tuple:
    """What matters for re-applying: which outputs exist and their geometry/scale."""
    return tuple(sorted((m.name, m.x, m.y, m.width, m.height, round(m.scale, 3)) for m in mons if m.enabled))
