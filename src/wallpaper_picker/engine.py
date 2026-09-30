"""Build linux-wallpaperengine command lines from the saved config (pure functions).

Property overrides (``--set-property``) and the fps/mouse/parallax/particles switches are
global to one engine process, so monitors are only grouped into one process when their
settings agree; otherwise each gets its own process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import SPAN, Config
from .monitors import Monitor
from .shim import engine_target
from .workshop import Wallpaper, color_needs_fix, format_color, parse_color

# Audio is never wanted: every engine launch carries these.
AUDIO_OFF = ("--silent", "--no-audio-processing")

# Wayland only: which wlr-layer-shell layer the wallpaper surface goes on.
#
# On COSMIC the desktop icons (cosmic-files-applet) are a layer-shell surface on the
# *Bottom* layer, and linux-wallpaperengine defaults to Bottom as well. Ordering within
# a single layer is undefined, so the wallpaper got painted over the icons on monitors
# where it was running. COSMIC renders no icons on its Background layer, so anchoring
# every wallpaper there makes it the bottom-most surface and the icons always win.
# Background is also the layer the protocol intends for wallpapers, so this is a no-op
# on compositors that place icons elsewhere.
WAYLAND_LAYER = "background"

# Only used by Wallpaper Engine's own UI; never worth a separate engine process.
IGNORED_PROPERTIES = {"schemecolor"}


@dataclass
class Unit:
    """One wallpaper on one output (or one span)."""
    slot: str
    outputs: list[str]
    wallpaper: str
    title: str
    wtype: str
    target: str
    scaling: str
    clamp: str
    fps: int
    mouse: bool
    parallax: bool
    particles: bool
    props: dict[str, str]
    defaults: dict[str, str]

    def switches(self) -> tuple:
        return (self.fps, self.mouse, self.parallax, self.particles)

    def value_of(self, key: str) -> str | None:
        return self.props.get(key, self.defaults.get(key))


@dataclass
class EngineSpec:
    key: str
    argv: list[str]
    units: list[Unit] = field(default_factory=list)

    @property
    def outputs(self) -> list[str]:
        return [o for u in self.units for o in u.outputs]

    def describe(self) -> list[dict]:
        return [{"slot": u.slot, "outputs": u.outputs, "wallpaper": u.wallpaper, "title": u.title}
                for u in self.units]


def property_args(wp: Wallpaper, overrides: dict, fix_colors: bool = True) -> dict[str, str]:
    """``--set-property`` values for one wallpaper: colour fixes, then the user's overrides."""
    out: dict[str, str] = {}
    if fix_colors:
        for p in wp.properties:
            if p.key in IGNORED_PROPERTIES:
                continue
            if p.type == "color" and color_needs_fix(p.raw_default):
                out[p.key] = format_color(parse_color(p.raw_default))
    for key, value in overrides.items():
        p = wp.prop(key)
        if p is None or not p.editable:
            continue
        out[key] = p.cli_value(value)
    return out


def property_defaults(wp: Wallpaper) -> dict[str, str]:
    """Values a wallpaper actually uses; videos ignore properties, so they never conflict."""
    if wp.type == "video":
        return {}
    return {p.key: p.cli_value(p.default) for p in wp.properties
            if p.editable and p.key not in IGNORED_PROPERTIES}


def compatible(a: Unit, b: Unit) -> bool:
    if a.switches() != b.switches():
        return False
    if a.clamp != b.clamp:
        return False
    for x, y in ((a, b), (b, a)):
        for k, v in x.props.items():
            if k in y.defaults and y.value_of(k) != v:
                return False
    return True


def build_units(cfg: Config, monitors: list[Monitor], library: dict[str, Wallpaper],
                target_fn: Callable[[Wallpaper, dict[str, Wallpaper]], str] | None = None,
                ) -> tuple[list[Unit], list[str]]:
    target_fn = target_fn or (lambda w, lib: engine_target(w, lib))
    units: list[Unit] = []
    problems: list[str] = []
    for slot_key, slot, outputs in cfg.active_assignments(monitors):
        wid = slot.wallpaper or ""
        wp = library.get(wid)
        where = "span" if slot_key == SPAN else slot_key
        if wp is None:
            problems.append(f"{where}: wallpaper {wid} is not installed")
            continue
        try:
            target = target_fn(wp, library)
        except (ValueError, OSError) as e:
            problems.append(f"{where}: {e}")
            continue
        units.append(Unit(
            slot=slot_key, outputs=list(outputs), wallpaper=wid, title=wp.title, wtype=wp.type, target=target,
            scaling=slot.scaling, clamp=slot.clamp, fps=slot.fps, mouse=slot.mouse, parallax=slot.parallax,
            particles=slot.particles,
            props=property_args(wp, cfg.overrides(slot_key, wid), cfg.options.fix_color_defaults),
            defaults=property_defaults(wp),
        ))
    return units, problems


def group_units(units: list[Unit], isolate: set[str] | None = None) -> list[list[Unit]]:
    """Share one engine process between compatible units.

    Web wallpapers (embedded Chromium) and slots in ``isolate`` (e.g. after a crash) always get
    their own process, so one failing wallpaper can't take the other monitors down with it.
    """
    isolate = isolate or set()
    groups: list[list[Unit]] = []
    for u in units:
        alone = u.wtype == "web" or u.slot in isolate
        for g in groups:
            if alone or any(o.wtype == "web" or o.slot in isolate for o in g):
                continue
            if all(compatible(u, other) for other in g):
                g.append(u)
                break
        else:
            groups.append([u])
    return groups


def build_argv(engine: str, units: list[Unit], assets_dir: str | None = None,
               snapshot: str | None = None) -> list[str]:
    first = units[0]
    argv = [
        engine, "--fps", str(first.fps), *AUDIO_OFF, "--no-fullscreen-pause",
        "--layer", WAYLAND_LAYER,
    ]
    if not first.mouse:
        argv.append("--disable-mouse")
    if not first.parallax:
        argv.append("--disable-parallax")
    if not first.particles:
        argv.append("--disable-particles")
    if assets_dir:
        argv += ["--assets-dir", assets_dir]
    if snapshot:
        argv += ["--screenshot", snapshot, "--screenshot-delay", str(max(30, first.fps * 4))]
    props: dict[str, str] = {}
    for u in units:
        if len(u.outputs) >= 2:
            argv += ["--screen-span", ",".join(u.outputs)]
        else:
            argv += ["--screen-root", u.outputs[0]]
        argv += ["--bg", u.target, "--scaling", u.scaling, "--clamp", u.clamp]
        props.update(u.props)
    for k in sorted(props):
        argv += ["--set-property", f"{k}={props[k]}"]
    return ensure_silent(argv)


def ensure_silent(argv: list[str]) -> list[str]:
    argv = list(argv)
    for flag in AUDIO_OFF:
        if flag not in argv:
            argv.insert(1, flag)
    if "--volume" in argv or "-v" in argv:
        raise ValueError("audio options are not allowed")
    return argv


def build_plan(cfg: Config, monitors: list[Monitor], library: dict[str, Wallpaper], engine: str,
               assets_dir: str | None = None,
               target_fn: Callable[[Wallpaper, dict[str, Wallpaper]], str] | None = None,
               snapshot_dir: str | None = None, isolate: set[str] | None = None,
               ) -> tuple[list[EngineSpec], list[str]]:
    if not cfg.enabled:
        return [], []
    units, problems = build_units(cfg, monitors, library, target_fn)
    specs = []
    for g in group_units(units, isolate):
        key = "+".join(o for u in g for o in u.outputs)
        snap = f"{snapshot_dir}/snapshot-{key}.png" if snapshot_dir else None
        specs.append(EngineSpec(key=key, argv=build_argv(engine, g, assets_dir, snap), units=g))
    return specs, problems


def resolve_assets_dir(cfg: Config) -> str | None:
    if cfg.options.assets_dir:
        return str(Path(cfg.options.assets_dir).expanduser())
    return None
