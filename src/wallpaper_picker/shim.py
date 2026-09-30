"""Engine targets, and "shim" folders for wallpapers the engine can't load as they are.

linux-wallpaperengine needs ``type`` and ``file`` in project.json. Some Wallpaper Engine
projects omit ``type`` (older built-ins), and presets have no ``file`` at all: they point
at another wallpaper (``dependency``) and carry property values (``preset``). For those we
build a folder of symlinks under ~/.cache/wallpaper-picker/shims/<id>/ with a patched
project.json, and hand that folder's path to the engine.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from . import paths
from .workshop import WE_APP_ID, Wallpaper

ENGINE_WORKSHOP_DIRS = [
    ".local/share/Steam/steamapps/workshop/content",
    ".steam/steam/steamapps/workshop/content",
    ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/workshop/content",
    "snap/steam/common/.local/share/Steam/steamapps/workshop/content",
]


def engine_finds_by_id(wp: Wallpaper, home: Path | None = None) -> bool:
    """Mirror the engine's own lookup for ``--bg <id>``."""
    home = home or Path.home()
    if wp.source != "workshop":
        return False
    for d in ENGINE_WORKSHOP_DIRS:
        cand = home / d / WE_APP_ID / wp.id
        if cand.is_dir():
            return os.path.realpath(cand) == os.path.realpath(wp.path)
    return False


def _read_project(folder: Path) -> dict:
    return json.loads((folder / "project.json").read_bytes().decode("utf-8-sig", errors="replace"))


def _overlay(src: Path, dst: Path) -> None:
    """Symlink every entry of ``src`` into ``dst``, merging directories that already exist."""
    for entry in src.iterdir():
        target = dst / entry.name
        if target.is_symlink() and entry.is_dir() and target.resolve().is_dir():
            # replace the symlink with a real dir so both trees can be merged
            existing = target.resolve()
            target.unlink()
            target.mkdir()
            _overlay(existing, target)
            _overlay(entry, target)
        elif target.is_dir() and not target.is_symlink() and entry.is_dir():
            _overlay(entry, target)
        else:
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            target.symlink_to(entry.resolve())


def build_project_json(wp: Wallpaper, base: Wallpaper | None) -> dict:
    if base is not None:
        data = _read_project(base.path)
        general = data.setdefault("general", {})
        props = general.setdefault("properties", {})
        for key, value in wp.preset.items():
            if isinstance(props.get(key), dict):
                props[key]["value"] = value
        data["title"] = wp.title
        if not data.get("type"):
            data["type"] = base.type
    else:
        data = _read_project(wp.path)
    if not data.get("type"):
        data["type"] = wp.type
    return data


def ensure_shim(wp: Wallpaper, base: Wallpaper | None = None, root: Path | None = None) -> Path:
    root = root or paths.shims_dir()
    data = build_project_json(wp, base)
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False)
    sources = [str(base.path)] if base else []
    sources.append(str(wp.path))
    digest = hashlib.sha1((blob + "|" + "|".join(sources)).encode()).hexdigest()[:16]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in wp.id)
    dst = root / safe
    stamp = dst / ".shim-stamp"
    if stamp.is_file() and stamp.read_text().strip() == digest:
        return dst
    tmp = root / f".{safe}.tmp{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for src in ([base.path] if base else []) + [wp.path]:
        for entry in src.iterdir():
            if entry.name == "project.json":
                continue
            target = tmp / entry.name
            if entry.is_dir() and (target.exists() or target.is_symlink()):
                if target.is_symlink():
                    existing = target.resolve()
                    target.unlink()
                    target.mkdir()
                    _overlay(existing, target)
                _overlay(entry, target)
            else:
                if target.is_symlink() or target.exists():
                    target.unlink()
                target.symlink_to(entry.resolve())
    (tmp / "project.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (tmp / ".shim-stamp").write_text(digest)
    if dst.exists():
        old = root / f".{safe}.old{os.getpid()}"
        dst.rename(old)
        tmp.rename(dst)
        shutil.rmtree(old, ignore_errors=True)
    else:
        tmp.rename(dst)
    return dst


def engine_target(wp: Wallpaper, library: dict[str, Wallpaper] | None = None, shim_root: Path | None = None) -> str:
    """The value to pass to ``--bg``: a workshop id when the engine can find it, else a path."""
    if wp.is_preset:
        base = (library or {}).get(wp.dependency or "")
        if base is None:
            raise ValueError(f"{wp.title}: needs wallpaper {wp.dependency}, which is not installed")
        return str(ensure_shim(wp, base, shim_root))
    if wp.directly_loadable:
        if engine_finds_by_id(wp):
            return wp.id
        return str(wp.path)
    if wp.type in ("video", "scene", "web") and wp.file:
        return str(ensure_shim(wp, None, shim_root))
    raise ValueError(f"{wp.title}: {wp.error or 'not a wallpaper the engine can play'}")
