"""Filesystem locations (XDG) and engine discovery."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

APP = "wallpaper-picker"


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / APP


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / APP


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{APP}-{os.getuid()}"
    return Path(base) / APP


def config_file() -> Path:
    return config_dir() / "config.json"


def status_file() -> Path:
    """Live status written by the daemon (engines, pids, pause state, errors)."""
    return runtime_dir() / "status.json"


def thumbs_dir() -> Path:
    return cache_dir() / "thumbs"


def shims_dir() -> Path:
    return cache_dir() / "shims"


def logs_dir() -> Path:
    return state_dir() / "logs"


ENGINE_NAME = "linux-wallpaperengine"


def default_engine_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / ".local/opt/linux-wallpaperengine" / ENGINE_NAME,
        Path("/opt/linux-wallpaperengine") / ENGINE_NAME,
        home / "Projects/linux-wallpaperengine/build/output" / ENGINE_NAME,
    ]


def find_engine(configured: str | None = None) -> Path | None:
    """Return the real engine binary (not a shell wrapper, so its PID is the engine)."""
    if configured:
        p = Path(os.path.expanduser(configured))
        if p.is_file() and os.access(p, os.X_OK):
            return p
    for p in default_engine_candidates():
        if p.is_file() and os.access(p, os.X_OK):
            return p
    found = shutil.which(ENGINE_NAME)
    return Path(found) if found else None


def ensure_dirs() -> None:
    for d in (config_dir(), cache_dir(), state_dir(), logs_dir(), thumbs_dir(), shims_dir()):
        d.mkdir(parents=True, exist_ok=True)
    rd = runtime_dir()
    rd.mkdir(parents=True, exist_ok=True)
    try:
        rd.chmod(0o700)
    except OSError:
        pass
