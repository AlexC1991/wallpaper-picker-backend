"""Wallpaper discovery and project.json parsing.

Wallpapers come from:
  * Steam workshop items of Wallpaper Engine (app 431960) in every Steam library, and
  * Wallpaper Engine's own ``projects/defaultprojects`` and ``projects/myprojects``.
"""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

WE_APP_ID = "431960"

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".ogv"}
KNOWN_TYPES = ("video", "scene", "web")
PREVIEW_NAMES = ("preview.gif", "preview.jpg", "preview.png", "preview.jpeg", "preview.webp")


# --------------------------------------------------------------------------- Steam libraries

def parse_library_folders(text: str) -> list[str]:
    """Extract the ``"path"`` values from a Steam libraryfolders.vdf."""
    return [m.group(1).replace("\\\\", "\\") for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text)]


def steam_roots(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    return [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".steam/debian-installation",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / "snap/steam/common/.local/share/Steam",
    ]


def steam_libraries(home: Path | None = None) -> list[Path]:
    """All Steam library folders (deduplicated by real path, existing only)."""
    seen: set[str] = set()
    libs: list[Path] = []

    def add(p: Path) -> None:
        try:
            real = os.path.realpath(p)
        except OSError:
            return
        if real in seen or not os.path.isdir(os.path.join(real, "steamapps")):
            return
        seen.add(real)
        libs.append(Path(real))

    for root in steam_roots(home):
        add(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for p in parse_library_folders(text):
            add(Path(p))
    return libs


@dataclass(frozen=True)
class Source:
    path: Path
    kind: str  # "workshop" | "built-in" | "my projects"


def discover_sources(home: Path | None = None) -> list[Source]:
    sources: list[Source] = []
    for lib in steam_libraries(home):
        ws = lib / "steamapps" / "workshop" / "content" / WE_APP_ID
        if ws.is_dir():
            sources.append(Source(ws, "workshop"))
        we = lib / "steamapps" / "common" / "wallpaper_engine" / "projects"
        for sub, kind in (("defaultprojects", "built-in"), ("myprojects", "my projects")):
            if (we / sub).is_dir():
                sources.append(Source(we / sub, kind))
    return sources


def find_assets_dir(home: Path | None = None) -> Path | None:
    for lib in steam_libraries(home):
        a = lib / "steamapps" / "common" / "wallpaper_engine" / "assets"
        if a.is_dir():
            return a
    return None


# --------------------------------------------------------------------------- properties

_UI_LABELS = {
    "ui_browse_properties_scheme_color": "Scheme colour",
    "ui_browse_properties_accent_color": "Accent colour",
    "ui_browse_properties_alignment": "Alignment",
    "ui_browse_properties_rate": "Playback rate",
    "ui_browse_properties_audio_processing": "Audio processing",
}


def _heading_level(text: Any) -> int:
    """The <hN> level of a separator entry, or 0 if this is not a heading."""
    if isinstance(text, str):
        m = re.match(r"\s*<h([1-6])\b", text, re.I)
        if m:
            return int(m.group(1))
    return 0


def clean_label(text: Any, key: str = "") -> str:
    if not isinstance(text, str) or not text.strip():
        return key.replace("_", " ").strip().capitalize() or key
    if text in _UI_LABELS:
        return _UI_LABELS[text]
    if text.startswith("ui_"):
        t = re.sub(r"^ui_(browse_)?(properties_)?", "", text)
        return t.replace("_", " ").capitalize()
    t = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"[ \t]+", " ", t).strip()


def _num(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_color(value: Any) -> tuple[float, float, float]:
    """Parse a WE colour (``"r g b"`` floats 0..1, ``"r, g, b"``, 0..255 ints or ``#rrggbb``)."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        parts = [float(x) for x in value[:3]]
    else:
        s = str(value or "").strip()
        if s.startswith("#"):
            h = s[1:]
            if len(h) in (3, 4):
                h = "".join(c * 2 for c in h[:3])
            try:
                return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
            except ValueError:
                return (1.0, 1.0, 1.0)
        try:
            parts = [float(x) for x in s.replace(",", " ").split()[:3]]
        except ValueError:
            return (1.0, 1.0, 1.0)
        if len(parts) < 3:
            return (1.0, 1.0, 1.0)
    if any(p > 1.0 for p in parts):
        parts = [p / 255.0 for p in parts]
    return tuple(max(0.0, min(1.0, p)) for p in parts)  # type: ignore[return-value]


def format_color(rgb: Iterable[float]) -> str:
    """Always emit decimals: the engine treats dot-less colours as 0..255 integers."""
    return " ".join(f"{max(0.0, min(1.0, float(c))):.6f}" for c in list(rgb)[:3])


def color_needs_fix(raw: Any) -> bool:
    """True for WE float colours written without a decimal point (e.g. ``"1 1 1"``).

    Wallpaper Engine reads those as floats (white), but linux-wallpaperengine parses
    a dot-less colour as 0..255 integers and renders it nearly black.
    """
    if not isinstance(raw, str):
        return False
    s = raw.strip()
    if not s or s.startswith("#") or "." in s:
        return False
    try:
        parts = [float(x) for x in s.replace(",", " ").split()]
    except ValueError:
        return False
    return len(parts) in (3, 4) and all(0.0 <= p <= 1.0 for p in parts)


@dataclass
class Property:
    key: str
    type: str
    label: str
    default: Any = None
    order: float = 0
    min: float | None = None
    max: float | None = None
    step: float | None = None
    precision: int | None = None
    fraction: bool = False
    options: list[tuple[str, str]] = field(default_factory=list)
    condition: str = ""
    raw_default: Any = None
    heading: int = 0        # 0 = not a heading; 2/3/4 = <h2>/<h3>/<h4> group marker

    EDITABLE = ("bool", "slider", "color", "combo", "textinput")

    @property
    def is_heading(self) -> bool:
        """A group separator rather than something the user sets."""
        return self.heading > 0

    @property
    def editable(self) -> bool:
        return self.type in self.EDITABLE

    def normalize(self, value: Any) -> Any:
        """Coerce a stored/user value to this property's canonical python type."""
        t = self.type
        if t == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if t == "slider":
            v = _num(value, _num(self.default, 0.0))
            if self.min is not None:
                v = max(self.min, v)
            if self.max is not None:
                v = min(self.max, v)
            return v
        if t == "color":
            return format_color(parse_color(value))
        if t == "combo":
            return "" if value is None else _combo_str(value)
        if t == "textinput":
            return "" if value is None else str(value)
        return value

    def cli_value(self, value: Any) -> str:
        v = self.normalize(value)
        if self.type == "bool":
            return "true" if v else "false"
        if self.type == "slider":
            f = float(v)
            if f.is_integer():
                return str(int(f))
            return f"{f:.6f}".rstrip("0").rstrip(".")
        return str(v)


def _combo_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def parse_property(key: str, d: Any) -> Property | None:
    if not isinstance(d, dict):
        return None
    ptype = str(d.get("type") or "").lower()
    if not ptype:
        return None
    raw = d.get("value")
    prop = Property(
        key=key,
        type=ptype,
        label=clean_label(d.get("text"), key),
        order=_num(d.get("order"), _num(d.get("index"), 0.0)) or 0.0,
        min=_num(d.get("min")),
        max=_num(d.get("max")),
        step=_num(d.get("step")),
        precision=int(d["precision"]) if isinstance(d.get("precision"), (int, float)) else None,
        fraction=bool(d.get("fraction", False)),
        condition=str(d.get("condition") or ""),
        raw_default=raw,
        # Wallpaper Engine marks its own property groups with "text" entries holding
        # <h2>/<h3>/<h4>. The level says how deep the heading is, which is what makes
        # real collapsible categories possible in the panel.
        heading=_heading_level(d.get("text")),
    )
    if ptype == "combo":
        for opt in d.get("options") or []:
            if isinstance(opt, dict) and "value" in opt:
                prop.options.append((clean_label(opt.get("label"), _combo_str(opt["value"])), _combo_str(opt["value"])))
    if ptype == "slider":
        if prop.min is None:
            prop.min = 0.0
        if prop.max is None:
            prop.max = max(1.0, _num(raw, 1.0) or 1.0)
    prop.default = prop.normalize(raw) if prop.editable else raw
    return prop


def parse_properties(general: Any) -> list[Property]:
    props = (general or {}).get("properties") if isinstance(general, dict) else None
    if not isinstance(props, dict):
        return []
    out = [p for k, v in props.items() if (p := parse_property(k, v)) is not None]
    out.sort(key=lambda p: (p.order, p.key))
    return out


# --------------------------------------------------------------------------- conditions

_TOKEN = re.compile(r"\s*(?:(\d+\.\d*|\.\d+|\d+)|(\"[^\"]*\"|'[^']*')|(===|!==|==|!=|<=|>=|&&|\|\||[()!<>])|([A-Za-z_][\w.]*))")


def eval_condition(expr: str, values: dict[str, Any]) -> bool:
    """Evaluate a WE property ``condition`` (JS-like: ``a.value == true && !b.value``).

    Unknown syntax evaluates to True so the property stays visible.
    """
    expr = (expr or "").strip()
    if not expr:
        return True
    tokens: list[tuple[str, Any]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos:].strip() == "":
                break
            return True
        pos = m.end()
        num, string, op, ident = m.groups()
        if num is not None:
            tokens.append(("v", float(num)))
        elif string is not None:
            tokens.append(("v", string[1:-1]))
        elif op is not None:
            tokens.append(("op", op))
        elif ident is not None:
            if ident in ("true", "false"):
                tokens.append(("v", ident == "true"))
            else:
                name = ident[:-6] if ident.endswith(".value") else ident
                tokens.append(("v", values.get(name)))
    i = 0

    def peek() -> tuple[str, Any] | None:
        return tokens[i] if i < len(tokens) else None

    def take() -> tuple[str, Any]:
        nonlocal i
        t = tokens[i]
        i += 1
        return t

    def comparable(a: Any, b: Any) -> tuple[Any, Any]:
        if isinstance(a, bool) or isinstance(b, bool):
            def tb(x: Any) -> Any:
                if isinstance(x, str):
                    return x.lower() in ("1", "true")
                return bool(x) if isinstance(x, (int, float)) else x
            return tb(a), tb(b)
        fa, fb = _num(a), _num(b)
        if fa is not None and fb is not None:
            return fa, fb
        return str(a), str(b)

    def primary() -> Any:
        t = take()
        if t == ("op", "!"):
            return not truthy(primary())
        if t == ("op", "("):
            v = orexpr()
            if peek() == ("op", ")"):
                take()
            return v
        if t[0] == "v":
            return t[1]
        raise ValueError("bad token")

    def truthy(v: Any) -> bool:
        if isinstance(v, str):
            return v.lower() not in ("", "0", "false")
        return bool(v)

    def cmp() -> Any:
        a = primary()
        t = peek()
        if t and t[0] == "op" and t[1] in ("==", "===", "!=", "!==", "<", ">", "<=", ">="):
            take()
            b = primary()
            x, y = comparable(a, b)
            op = t[1]
            try:
                if op in ("==", "==="):
                    return x == y
                if op in ("!=", "!=="):
                    return x != y
                if op == "<":
                    return x < y
                if op == ">":
                    return x > y
                if op == "<=":
                    return x <= y
                return x >= y
            except TypeError:
                return False
        return a

    def andexpr() -> Any:
        v = truthy(cmp())
        while peek() == ("op", "&&"):
            take()
            v = truthy(cmp()) and v
        return v

    def orexpr() -> Any:
        v = andexpr()
        while peek() == ("op", "||"):
            take()
            v = andexpr() or v
        return v

    try:
        return truthy(orexpr())
    except (ValueError, IndexError):
        return True


# --------------------------------------------------------------------------- wallpapers

@dataclass
class Wallpaper:
    id: str
    path: Path
    source: str
    title: str
    type: str  # video | scene | web | unknown
    raw_type: str | None = None
    file: str | None = None
    preview: Path | None = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)
    dependency: str | None = None
    preset: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    mtime: float = 0.0

    @property
    def is_preset(self) -> bool:
        return bool(self.dependency) and not self.file

    @property
    def directly_loadable(self) -> bool:
        """Can the engine load this folder as-is? (needs a ``type`` it knows and a ``file``)."""
        return (self.raw_type or "") in KNOWN_TYPES and bool(self.file)

    @property
    def playable(self) -> bool:
        return self.type in KNOWN_TYPES and self.error is None

    def prop(self, key: str) -> Property | None:
        for p in self.properties:
            if p.key == key:
                return p
        return None

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        hay = " ".join([self.title, self.id, self.type, self.source, " ".join(self.tags)]).lower()
        return all(part in hay for part in q.split())


def infer_type(file: str | None) -> str | None:
    if not file:
        return None
    ext = os.path.splitext(file)[1].lower()
    if ext == ".json":
        return "scene"
    if ext in (".html", ".htm"):
        return "web"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def find_preview(folder: Path, declared: Any) -> Path | None:
    if isinstance(declared, str) and declared:
        p = folder / declared
        if p.is_file():
            return p
    for name in PREVIEW_NAMES:
        p = folder / name
        if p.is_file():
            return p
    try:
        for p in sorted(folder.iterdir()):
            if p.stem.lower() == "preview" and p.is_file():
                return p
    except OSError:
        pass
    return None


def load_project(folder: Path, source: str = "workshop") -> Wallpaper:
    """Parse ``folder/project.json`` defensively (BOM, missing fields, odd types)."""
    wid = folder.name
    pj = folder / "project.json"
    try:
        mtime = pj.stat().st_mtime
    except OSError:
        mtime = 0.0
    try:
        data = json.loads(pj.read_bytes().decode("utf-8-sig", errors="replace"))
        if not isinstance(data, dict):
            raise ValueError("project.json is not an object")
    except (OSError, ValueError) as e:
        return Wallpaper(id=wid, path=folder, source=source, title=wid, type="unknown",
                         preview=find_preview(folder, None), error=f"Unreadable project.json: {e}", mtime=mtime)

    raw_type = data.get("type")
    raw_type = str(raw_type).strip().lower() if isinstance(raw_type, str) and raw_type.strip() else None
    file = data.get("file") if isinstance(data.get("file"), str) and data.get("file") else None
    wtype = raw_type if raw_type in KNOWN_TYPES else (infer_type(file) or "unknown")
    title = data.get("title")
    title = clean_label(title) if isinstance(title, str) and title.strip() else wid
    tags = [str(t) for t in data.get("tags") or [] if isinstance(t, (str, int))]
    dep = data.get("dependency")
    dep = str(dep) if isinstance(dep, (str, int)) and str(dep) else None
    preset = data.get("preset") if isinstance(data.get("preset"), dict) else {}
    wp = Wallpaper(
        id=wid,
        path=folder,
        source=source,
        title=title,
        type=wtype,
        raw_type=raw_type,
        file=file,
        preview=find_preview(folder, data.get("preview")),
        description=clean_label(data.get("description")) if isinstance(data.get("description"), str) else "",
        tags=tags,
        properties=parse_properties(data.get("general")),
        dependency=dep,
        preset=preset,
        mtime=mtime,
    )
    if wp.type == "unknown" and not dep:
        if file and file.lower().endswith(".exe"):
            wp.type, wp.error = "application", "Windows application wallpaper (.exe) - can't run on Linux"
        elif data.get("type"):
            wp.error = f"Unsupported wallpaper type {data.get('type')!r}"
        else:
            wp.error = "No wallpaper type or file"
    return wp


def resolve_presets(wallpapers: list[Wallpaper]) -> None:
    """Presets (``dependency`` + ``preset``, no ``file``) render their dependency with preset values."""
    by_id = {w.id: w for w in wallpapers}
    for w in wallpapers:
        if not w.is_preset:
            continue
        base = by_id.get(w.dependency or "")
        if base is None:
            w.type = "unknown"
            w.error = f"Needs wallpaper {w.dependency}, which is not installed"
            continue
        if base.is_preset:
            w.type = "unknown"
            w.error = "Preset of a preset is not supported"
            continue
        w.type = base.type
        w.error = base.error
        props: list[Property] = []
        for bp in base.properties:
            p = Property(**{**bp.__dict__, "options": list(bp.options)})
            if bp.key in w.preset and p.editable:
                p.default = p.normalize(w.preset[bp.key])
                p.raw_default = w.preset[bp.key]
            props.append(p)
        w.properties = props


def scan(sources: list[Source] | None = None) -> list[Wallpaper]:
    sources = discover_sources() if sources is None else sources
    out: list[Wallpaper] = []
    seen: set[str] = set()
    for src in sources:
        try:
            entries = sorted(src.path.iterdir())
        except OSError:
            continue
        for folder in entries:
            if not (folder / "project.json").is_file():
                continue
            key = folder.name if src.kind == "workshop" else f"{src.kind}:{folder.name}"
            if key in seen:
                continue
            seen.add(key)
            wp = load_project(folder, src.kind)
            if src.kind != "workshop":
                wp.id = key.replace(" ", "-")
            out.append(wp)
    resolve_presets(out)
    return out


def fuzzy_score(text: str, query: str) -> int:
    """Rank a title against a query: higher is better, 0 means no match.

    Substring hits beat scattered subsequence hits, earlier hits beat later ones, and a
    word-boundary start gets a bonus -- so "fw tower" finds "[Triple FHD] Firewatch -
    The Tower" and "dfw" finds "Dark Winter" only when the letters really are in order.
    """
    text_l = text.lower()
    q = query.strip().lower()
    if not q:
        return 1
    terms = q.split()
    total = 0
    for term in terms:
        pos = text_l.find(term)
        if pos >= 0:
            score = 1000 - min(pos, 400)
            if pos == 0 or text_l[pos - 1] in " _-[(":
                score += 250
            total += score
            continue
        # subsequence fallback
        i = 0
        span = 0
        first = -1
        for ch in text_l:
            if i < len(term) and ch == term[i]:
                if first < 0:
                    first = len(text_l) - len(text_l)  # placeholder, set below
                i += 1
        if i == len(term):
            total += 120
        else:
            return 0        # every term must match something
    return total


SORTS = ("name", "newest", "properties", "type", "author")


def sort_wallpapers(items: list["Wallpaper"], how: str) -> list["Wallpaper"]:
    """Sort a library. Unknown keys fall back to name."""
    if how == "newest":
        return sorted(items, key=lambda w: w.mtime, reverse=True)
    if how == "properties":
        return sorted(items, key=lambda w: len(w.properties), reverse=True)
    if how == "type":
        return sorted(items, key=lambda w: (w.type or "", w.title.lower()))
    if how == "author":
        return sorted(items, key=lambda w: (w.source or "", w.title.lower()))
    return sorted(items, key=lambda w: w.title.lower())


def find_wallpaper(wallpapers: list[Wallpaper], query: str) -> Wallpaper | None:
    """Find by exact id, then exact title, then unique case-insensitive substring of title."""
    q = query.strip()
    for w in wallpapers:
        if w.id == q:
            return w
    ql = q.lower()
    exact = [w for w in wallpapers if w.title.lower() == ql]
    if exact:
        return exact[0]
    partial = [w for w in wallpapers if ql in w.title.lower()]
    return partial[0] if len(partial) == 1 else None
