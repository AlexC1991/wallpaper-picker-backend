"""Power source detection, for the "pause on battery" compatibility option.

Wallpaper Engine on Windows lets you stop animating when the machine is on battery.
COSMIC/Linux exposes the same information through sysfs, so this module reads
``/sys/class/power_supply`` and reduces it to the few facts the daemon needs.

The parsing is pure; only :func:`read_power` touches the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path("/sys/class/power_supply")

# Battery supplies that are really peripherals (wireless mice, keyboards, headsets).
DEVICE_SCOPES = {"device", "peripheral"}

# status values that mean the battery is draining
DRAINING = {"discharging"}
# values that mean we are on wall power
PLUGGED = {"charging", "full", "not charging"}


@dataclass(frozen=True)
class PowerState:
    """What we could learn about power. Everything is optional by design."""

    has_battery: bool = False
    percent: int | None = None
    discharging: bool = False
    plugged: bool = False
    status: str = ""

    @property
    def on_battery(self) -> bool:
        """True only when we are sure the battery is draining."""
        return self.has_battery and self.discharging and not self.plugged

    def to_dict(self) -> dict:
        return {
            "has_battery": self.has_battery, "percent": self.percent,
            "discharging": self.discharging, "plugged": self.plugged,
            "status": self.status, "on_battery": self.on_battery,
        }


def parse_capacity(text: str) -> int | None:
    """``" 87\\n"`` -> ``87``; anything unparseable -> ``None``."""
    try:
        value = int(text.strip())
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


def classify(status: str) -> tuple[bool, bool]:
    """``(discharging, plugged)`` for a sysfs ``status`` value."""
    s = (status or "").strip().lower()
    if s in DRAINING:
        return True, False
    if s in PLUGGED:
        return False, True
    return False, False


def should_pause(state: PowerState, pause_on_battery: bool = False,
                 threshold: int = 20) -> bool:
    """Whether the wallpaper should be paused because of power.

    ``pause_on_battery`` pauses as soon as we are draining; ``threshold`` further pauses
    when the battery is at or below that percentage even if we cannot read the status.
    """
    if not pause_on_battery or not state.has_battery:
        return False
    if state.on_battery:
        return True
    if state.percent is not None and state.percent <= max(0, threshold) and not state.plugged:
        return True
    return False


def read_power(root: Path | None = None) -> PowerState:
    """Read every supply under ``root`` and merge it into one state."""
    root = root or DEFAULT_ROOT
    has_battery = False
    percent: int | None = None
    status = ""
    plugged = False
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return PowerState()

    for entry in entries:
        def _read(name: str) -> str:
            try:
                return (entry / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""

        kind = _read("type").strip().lower()
        if kind == "battery":
            cap = parse_capacity(_read("capacity"))
            # A peripheral's battery must never count as the machine's power source.
            # Kernels new enough to publish `scope` say so explicitly; older ones leave
            # it out, in which case a system battery is the one with a capacity.
            scope = _read("scope").strip().lower()
            if scope in DEVICE_SCOPES:
                continue
            if not scope and cap is None:
                continue
            has_battery = True
            if cap is not None:
                percent = cap
            # prefer a status from a battery we can actually read
            st = _read("status").strip()
            if st and not status:
                status = st
        elif kind in ("mains", "usb", "usb_c", "usb-c", "ac", "wireless"):
            if "1" in _read("online").strip():
                plugged = True

    discharging, plugged_from_status = classify(status)
    return PowerState(has_battery=has_battery, percent=percent,
                      discharging=discharging, plugged=plugged or plugged_from_status,
                      status=status)