"""Talk to the daemon over D-Bus (starting it if needed)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from typing import Any

from . import paths
from .daemon import BUS_NAME, IFACE, OBJ_PATH


class DaemonError(RuntimeError):
    pass


async def _call(member: str, timeout: float) -> Any:
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    try:
        reply = await asyncio.wait_for(
            bus.call(Message(destination=BUS_NAME, path=OBJ_PATH, interface=IFACE, member=member)), timeout)
    finally:
        bus.disconnect()
    if reply.message_type == MessageType.ERROR:
        raise DaemonError(f"{reply.error_name}: {reply.body[0] if reply.body else ''}")
    body = reply.body[0] if reply.body else ""
    try:
        return json.loads(body)
    except ValueError:
        return body


async def _has_owner() -> bool:
    from dbus_fast import BusType, Message
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    try:
        reply = await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                       interface="org.freedesktop.DBus", member="NameHasOwner",
                                       signature="s", body=[BUS_NAME]))
        return bool(reply.body and reply.body[0])
    finally:
        bus.disconnect()


def daemon_running() -> bool:
    try:
        return asyncio.run(_has_owner())
    except Exception:  # noqa: BLE001
        return False


def spawn_daemon(wait: float = 15.0) -> bool:
    """Start ``wallpaper-picker --daemon`` detached; wait until it owns its bus name."""
    paths.ensure_dirs()
    out = open(paths.state_dir() / "daemon.stdout.log", "ab")
    env = dict(os.environ)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    subprocess.Popen([sys.executable, "-m", "wallpaper_picker", "--daemon"], stdin=subprocess.DEVNULL,
                     stdout=out, stderr=subprocess.STDOUT, start_new_session=True, env=env,
                     cwd=str(paths.state_dir()))
    out.close()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if daemon_running():
            return True
        time.sleep(0.2)
    return False


def call(member: str, timeout: float = 60.0, start: bool = True) -> Any:
    if not daemon_running():
        if not start:
            raise DaemonError("the wallpaper-picker daemon is not running")
        if not spawn_daemon():
            raise DaemonError("could not start the wallpaper-picker daemon (see ~/.local/state/wallpaper-picker/daemon.log)")
        # A fresh daemon applies the config on startup; Apply waits for that and is then a no-op.
    return asyncio.run(_call(member, timeout))


def apply() -> dict:
    return call("Apply")


def stop() -> dict:
    return call("Stop", start=False)


def status() -> dict:
    """Live status from the daemon, or the last status file it wrote."""
    try:
        if daemon_running():
            return asyncio.run(_call("Status", 5))
    except Exception:  # noqa: BLE001
        pass
    try:
        return json.loads(paths.status_file().read_text(encoding="utf-8")) | {"daemon_pid": None}
    except (OSError, ValueError):
        return {"daemon_pid": None, "engines": []}
