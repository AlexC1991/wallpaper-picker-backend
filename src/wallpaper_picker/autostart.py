"""Write/remove the daemon's autostart entry (start on login, with optional delay).

The daemon is normally started by an XDG autostart ``.desktop`` file written by
``install.sh``. The app lets you toggle "start on login" and add a delay (so the
compositor and panel are up first), so this module owns that file.

Pure string building; only :func:`autostart_path` and the read/write helpers touch disk.
"""

from __future__ import annotations

from pathlib import Path

APP_NAME = "wallpaper-picker"
DESKTOP_NAME = f"{APP_NAME}-daemon.desktop"

ENTRY_TEMPLATE = """[Desktop Entry]
Type=Application
Name=Wallpaper Engine Linux Edition daemon
Comment=Applies your animated wallpapers at login and pauses them behind full-screen windows
Exec={exec_line}
Icon=preferences-desktop-wallpaper
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled={enabled}
"""


def autostart_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "autostart"


def autostart_path(home: Path | None = None) -> Path:
    return autostart_dir(home) / DESKTOP_NAME


def build_entry(exec_path: str, delay: int = 0, enabled: bool = True) -> str:
    """The ``.desktop`` contents for the daemon.

    A delay is expressed the portable way: ``sh -c 'sleep N; exec ...'``. Several
    desktop environments honour ``X-GNOME-Autostart-Delay`` but not all, and the
    daemon has to survive that difference.
    """
    delay = max(0, int(delay))
    if delay:
        exec_line = f"/bin/sh -c 'sleep {delay}; exec \"{exec_path}\" --daemon'"
    else:
        exec_line = f"{exec_path} --daemon"
    return ENTRY_TEMPLATE.format(
        exec_line=exec_line,
        enabled="true" if enabled else "false",
    )


def parse_delay(text: str) -> int:
    """Recover the delay (seconds) from an existing entry, 0 if none/unknown."""
    import re

    m = re.search(r"sleep\s+(\d+)", text)
    return int(m.group(1)) if m else 0


def apply(exec_path: str, delay: int = 0, enabled: bool = True, home: Path | None = None) -> Path:
    """Write the entry, or remove it when autostart is switched off."""
    path = autostart_path(home)
    if not enabled:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_entry(exec_path, delay, True), encoding="utf-8")
    return path


def is_enabled(home: Path | None = None) -> bool:
    """Whether a usable entry exists (and is not switched off inside the file)."""
    path = autostart_path(home)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "X-GNOME-Autostart-enabled=false" not in text