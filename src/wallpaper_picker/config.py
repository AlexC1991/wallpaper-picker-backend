"""Persistent state: ~/.config/wallpaper-picker/config.json.

Layout::

    {
      "version": 1,
      "enabled": true,
      "slots": {                       # one per output name, plus "span"
        "HDMI-A-1": {"wallpaper": "2584613496", "scaling": "fill", "fps": 30,
                     "mouse": true, "parallax": true, "particles": true},
        "span": {"wallpaper": null, "outputs": ["DP-1", "HDMI-A-1"], ...}
      },
      "properties": {"HDMI-A-1": {"<wallpaper id>": {"<key>": <value>}}},
      "options": {"pause_on_fullscreen": true, "pause_scope": "all",
                  "fix_color_defaults": true, "engine": null, "assets_dir": null,
                  "snapshots": false}
    }

When ``slots.span.wallpaper`` is set, the outputs it lists are driven by the span and their
own slots are empty.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import paths
from .monitors import Monitor

SPAN = "span"
SCALING_MODES = ("fill", "fit", "stretch", "default")
CLAMP_MODES = ("clamp", "border", "repeat")  # linux-wallpaperengine --clamp
FPS_CHOICES = (15, 24, 30, 60)
DEFAULT_FPS = 30


@dataclass
class Slot:
    wallpaper: str | None = None
    scaling: str = "fill"
    fps: int = DEFAULT_FPS
    mouse: bool = True
    parallax: bool = True
    particles: bool = True
    clamp: str = "clamp"  # engine --clamp: "clamp" | "border" | "repeat"
    outputs: list[str] = field(default_factory=list)  # span only

    @classmethod
    def from_dict(cls, d: Any) -> "Slot":
        if not isinstance(d, dict):
            return cls()
        s = cls()
        w = d.get("wallpaper")
        s.wallpaper = str(w) if w not in (None, "") else None
        sc = str(d.get("scaling") or "fill").lower()
        s.scaling = sc if sc in SCALING_MODES else "fill"
        try:
            s.fps = max(1, min(240, int(d.get("fps", DEFAULT_FPS))))
        except (TypeError, ValueError):
            s.fps = DEFAULT_FPS
        for k in ("mouse", "parallax", "particles"):
            if k in d:
                setattr(s, k, bool(d[k]))
        cl = str(d.get("clamp") or "clamp").lower()
        s.clamp = cl if cl in CLAMP_MODES else "clamp"
        outs = d.get("outputs")
        s.outputs = [str(o) for o in outs] if isinstance(outs, list) else []
        return s

    def settings(self) -> dict:
        return {k: getattr(self, k) for k in ("scaling", "fps", "mouse", "parallax", "particles", "clamp")}

    def apply_settings(self, settings: dict) -> None:
        clean = Slot.from_dict({**self.settings(), **settings})
        for k in ("scaling", "fps", "mouse", "parallax", "particles", "clamp"):
            setattr(self, k, getattr(clean, k))


@dataclass
class Options:
    pause_on_fullscreen: bool = True
    pause_scope: str = "all"  # "all" | "monitor"
    fix_color_defaults: bool = True
    engine: str | None = None
    assets_dir: str | None = None
    snapshots: bool = False  # engine saves a PNG of what it renders (see status "snapshot")

    # --- compatibility / resource use -------------------------------------------------
    # Pause while running on battery (Wallpaper Engine's "stop animating on battery").
    pause_on_battery: bool = False
    battery_threshold: int = 20      # also pause at or below this % if status is unreadable
    # Pause when any window is maximised, not only truly full-screen.
    # NB winbar reports `fullscreen`; maximised windows are a separate flag in its API.
    pause_on_maximised: bool = True
    # Extra app ids that should NOT pause the wallpaper while full-screen (games you play
    # over the top of it, videos, ...). Matched case-insensitively as a substring.
    pause_ignore_appids: list[str] = field(default_factory=list)
    # Startup behaviour: autostart the daemon at login, optionally after a delay.
    start_on_login: bool = True
    startup_delay: int = 0           # seconds, 0..300

    @classmethod
    def from_dict(cls, d: Any) -> "Options":
        o = cls()
        if isinstance(d, dict):
            o.pause_on_fullscreen = bool(d.get("pause_on_fullscreen", True))
            o.pause_scope = d.get("pause_scope") if d.get("pause_scope") in ("all", "monitor") else "all"
            o.fix_color_defaults = bool(d.get("fix_color_defaults", True))
            o.engine = d.get("engine") or None
            o.assets_dir = d.get("assets_dir") or None
            o.snapshots = bool(d.get("snapshots", False))
            o.pause_on_battery = bool(d.get("pause_on_battery", False))
            try:
                o.battery_threshold = max(0, min(100, int(d.get("battery_threshold", 20))))
            except (TypeError, ValueError):
                o.battery_threshold = 20
            o.pause_on_maximised = bool(d.get("pause_on_maximised", True))
            ign = d.get("pause_ignore_appids")
            o.pause_ignore_appids = [str(x) for x in ign if str(x).strip()] if isinstance(ign, list) else []
            o.start_on_login = bool(d.get("start_on_login", True))
            try:
                o.startup_delay = max(0, min(300, int(d.get("startup_delay", 0))))
            except (TypeError, ValueError):
                o.startup_delay = 0
        return o


@dataclass
class Config:
    enabled: bool = True
    slots: dict[str, Slot] = field(default_factory=dict)
    properties: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    options: Options = field(default_factory=Options)
    # Workshop browsing needs a free Steam Web API key (steamcommunity.com/dev/apikey).
    # It lives here, in the user's config file, and is never part of the app bundle.
    steam_api_key: str = ""
    # Steam account name for steamcmd; the app asks before the first in-app install.
    steam_account: str = ""

    # ------------------------------------------------------------------ (de)serialise
    @classmethod
    def from_dict(cls, d: Any) -> "Config":
        c = cls()
        if not isinstance(d, dict):
            return c
        c.enabled = bool(d.get("enabled", True))
        for k, v in (d.get("slots") or {}).items():
            c.slots[str(k)] = Slot.from_dict(v)
        props = d.get("properties") or {}
        if isinstance(props, dict):
            for slot, per_wp in props.items():
                if isinstance(per_wp, dict):
                    c.properties[str(slot)] = {
                        str(w): dict(v) for w, v in per_wp.items() if isinstance(v, dict)
                    }
        c.options = Options.from_dict(d.get("options"))
        key = d.get("steam_api_key")
        c.steam_api_key = str(key).strip() if key else ""
        acct = d.get("steam_account")
        c.steam_account = str(acct).strip() if acct else ""
        return c

    def to_dict(self) -> dict:
        slots = {}
        for k, s in self.slots.items():
            d = asdict(s)
            if k != SPAN:
                d.pop("outputs", None)
            slots[k] = d
        return {
            "version": 1,
            "enabled": self.enabled,
            "slots": slots,
            "properties": self.properties,
            "options": asdict(self.options),
            "steam_api_key": self.steam_api_key,
            "steam_account": self.steam_account,
        }

    # ------------------------------------------------------------------ queries
    def slot(self, key: str) -> Slot:
        if key not in self.slots:
            self.slots[key] = Slot()
        return self.slots[key]

    @property
    def span_active(self) -> bool:
        s = self.slots.get(SPAN)
        return bool(s and s.wallpaper and len(s.outputs) >= 2)

    def overrides(self, slot: str, wallpaper: str) -> dict[str, Any]:
        return dict(self.properties.get(slot, {}).get(wallpaper, {}))

    def set_overrides(self, slot: str, wallpaper: str, values: dict[str, Any]) -> None:
        per = self.properties.setdefault(slot, {})
        if values:
            per[wallpaper] = dict(values)
        else:
            per.pop(wallpaper, None)
            if not per:
                self.properties.pop(slot, None)

    def active_assignments(self, monitors: list[Monitor]) -> list[tuple[str, Slot, list[str]]]:
        """(slot key, slot, outputs) for every slot that should run on the present monitors."""
        present = [m.name for m in monitors if m.enabled]
        result: list[tuple[str, Slot, list[str]]] = []
        spanned: set[str] = set()
        if self.span_active:
            span = self.slots[SPAN]
            outs = [o for o in span.outputs if o in present]
            if len(outs) >= 2:
                result.append((SPAN, span, outs))
                spanned = set(outs)
            elif len(outs) == 1:
                # only one of the spanned monitors is left: show the wallpaper there
                result.append((SPAN, span, outs))
                spanned = set(outs)
        for name in present:
            if name in spanned:
                continue
            s = self.slots.get(name)
            if s and s.wallpaper:
                result.append((name, s, [name]))
        return result

    # ------------------------------------------------------------------ edits
    def assign(self, target_slots: list[str], wallpaper: str | None, settings: dict | None,
               monitors: list[Monitor]) -> None:
        """Put ``wallpaper`` on the given slot keys (output names, or [SPAN])."""
        present = [m.name for m in monitors if m.enabled]
        if target_slots == [SPAN]:
            span = self.slot(SPAN)
            span.wallpaper = wallpaper
            span.outputs = present
            if settings:
                span.apply_settings(settings)
            for name in present:
                if name in self.slots:
                    self.slots[name].wallpaper = None
            return
        if self.span_active:
            span = self.slots[SPAN]
            leaving = [o for o in span.outputs if o not in target_slots and o in present]
            for o in leaving:  # keep what those monitors were showing
                s = self.slot(o)
                s.wallpaper = span.wallpaper
                s.apply_settings(span.settings())
                ov = self.overrides(SPAN, span.wallpaper or "")
                if ov:
                    self.set_overrides(o, span.wallpaper or "", ov)
            span.wallpaper = None
        for key in target_slots:
            s = self.slot(key)
            s.wallpaper = wallpaper
            if settings:
                s.apply_settings(settings)

    def clear(self, key: str) -> None:
        if key in self.slots:
            self.slots[key].wallpaper = None


def resolve_target(target: str, monitors: list[Monitor]) -> list[str]:
    """Map left/right/both/span/<output>/monitorN to slot keys."""
    t = target.strip().lower().replace(" ", "")
    enabled = [m for m in monitors if m.enabled]
    if t in ("span", "spanned"):
        if len(enabled) < 2:
            raise ValueError("Span needs at least two monitors")
        return [SPAN]
    if t in ("both", "all", "each"):
        return [m.name for m in enabled]
    for m in enabled:
        if t == m.name.lower() or (m.position and t == m.position.lower().replace(" ", "")):
            return [m.name]
    if t in ("center",):
        return resolve_target("centre", monitors)
    raise ValueError(f"Unknown monitor {target!r}; choose from: "
                     + ", ".join([*(m.position.lower() or m.name for m in enabled), "both", "span"]))


def load(path: Path | None = None) -> Config:
    path = path or paths.config_file()
    try:
        return Config.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Config()


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
