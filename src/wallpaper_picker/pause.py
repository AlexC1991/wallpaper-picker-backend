"""Pause the engine while a full-screen window is focused, using winbar's D-Bus window list.

COSMIC doesn't give linux-wallpaperengine what it needs for its own full-screen pause, but
the winbar taskbar exports ``dev.winbar.Windows1`` on the session bus::

    List() -> a(ssssbbbiiiib)   id, app_id, title, output, focused, minimized, fullscreen,
                                x, y, w, h, on_active_workspace
    signal FocusChanged(s id), signal WindowsChanged()

``PauseController`` is the pure state machine; ``WinbarWatcher`` feeds it from the bus and
falls back to "never paused" whenever winbar isn't there.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

WINBAR_NAME = "dev.winbar.Windows"
WINBAR_PATH = "/dev/winbar/Windows"
WINBAR_IFACE = "dev.winbar.Windows1"


@dataclass(frozen=True)
class WindowInfo:
    id: str
    app_id: str
    title: str
    output: str
    focused: bool
    minimized: bool
    fullscreen: bool
    on_active_workspace: bool = True

    @classmethod
    def from_struct(cls, s: Iterable[Any]) -> "WindowInfo":
        v = list(s)
        return cls(id=str(v[0]), app_id=str(v[1]), title=str(v[2]), output=str(v[3]),
                   focused=bool(v[4]), minimized=bool(v[5]), fullscreen=bool(v[6]),
                   on_active_workspace=bool(v[11]) if len(v) > 11 else True)


def blocking_windows(windows: Iterable[WindowInfo],
                     ignore_appids: Iterable[str] = ()) -> list[WindowInfo]:
    """Full-screen, focused windows that should pause the wallpaper.

    ``ignore_appids`` skips windows whose app id contains any of the given values
    (case-insensitive) -- the same idea as the engine's own
    ``--fullscreen-pause-ignore-appid``.
    """
    needles = [n.strip().lower() for n in ignore_appids if n and n.strip()]
    out = []
    for w in windows:
        if not (w.fullscreen and w.focused and not w.minimized and w.on_active_workspace):
            continue
        blob = f"{w.app_id} {w.title}".lower()
        if any(n in blob for n in needles):
            continue
        out.append(w)
    return out


def compute_paused(windows: Iterable[WindowInfo], engines: dict[str, set[str]],
                   scope: str = "all", enabled: bool = True,
                   ignore_appids: Iterable[str] = ()) -> set[str]:
    """Which engine keys should be paused right now."""
    if not enabled:
        return set()
    fs = blocking_windows(windows, ignore_appids)
    if not fs:
        return set()
    if scope != "monitor":
        return set(engines)
    covered = {w.output for w in fs}
    if "" in covered:
        return set(engines)
    return {k for k, outs in engines.items() if outs & covered}


class PauseController:
    """Tracks windows + engines and calls pause/resume only on transitions."""

    def __init__(self, pause: Callable[[str], None], resume: Callable[[str], None],
                 scope: str = "all", enabled: bool = True,
                 ignore_appids: Iterable[str] = ()) -> None:
        self._pause, self._resume = pause, resume
        self.scope, self.enabled = scope, enabled
        self.ignore_appids: list[str] = list(ignore_appids)
        self.available = False
        self.windows: list[WindowInfo] = []
        self.engines: dict[str, set[str]] = {}
        self.paused: set[str] = set()
        # set by the daemon from the power policy (battery); None = no opinion
        self.power_paused = False

    @property
    def blocking(self) -> list[WindowInfo]:
        return blocking_windows(self.windows) if self.available else []

    def set_available(self, available: bool) -> None:
        self.available = available
        if not available:
            self.windows = []
        self._reconcile()

    def set_windows(self, windows: list[WindowInfo]) -> None:
        self.available = True
        self.windows = list(windows)
        self._reconcile()

    def set_engines(self, engines: dict[str, set[str]]) -> None:
        self.engines = {k: set(v) for k, v in engines.items()}
        self.paused &= set(self.engines)  # forget engines that are gone
        self._reconcile()

    def configure(self, scope: str, enabled: bool,
                  ignore_appids: Iterable[str] | None = None) -> None:
        self.scope, self.enabled = scope, enabled
        if ignore_appids is not None:
            self.ignore_appids = list(ignore_appids)
        self._reconcile()

    def forget(self, key: str) -> None:
        """An engine was (re)started: it is running, whatever we thought before."""
        self.paused.discard(key)

    def set_power_paused(self, paused: bool) -> None:
        """The daemon's power policy (e.g. running on battery) asks for a pause."""
        if paused != self.power_paused:
            self.power_paused = paused
            self._reconcile()

    def reconcile(self) -> None:
        self._reconcile()

    def _reconcile(self) -> None:
        want = compute_paused(self.windows if self.available else [], self.engines,
                              self.scope, self.enabled, self.ignore_appids)
        # power policy is applied on top: either source can ask for a pause
        if self.power_paused:
            want |= set(self.engines)
        for k in sorted(want - self.paused):
            self.paused.add(k)
            self._pause(k)
        for k in sorted(self.paused - want):
            self.paused.discard(k)
            self._resume(k)


class WinbarWatcher:
    """Follows winbar on the session bus and keeps a PauseController up to date.

    ``bus`` is a connected dbus_fast.aio.MessageBus (or a test double with ``call`` and
    ``add_message_handler``).
    """

    def __init__(self, bus: Any, controller: PauseController) -> None:
        self.bus = bus
        self.controller = controller
        self._refresh_task: asyncio.Task | None = None
        self._dirty = False

    async def start(self) -> None:
        from dbus_fast import Message

        self.bus.add_message_handler(self._on_message)
        for rule in (
            f"type='signal',interface='{WINBAR_IFACE}',path='{WINBAR_PATH}'",
            f"type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='{WINBAR_NAME}'",
        ):
            await self.bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                        interface="org.freedesktop.DBus", member="AddMatch",
                                        signature="s", body=[rule]))
        self.schedule_refresh()

    def _on_message(self, msg: Any) -> None:
        try:
            if msg.interface == WINBAR_IFACE and msg.member in ("FocusChanged", "WindowsChanged"):
                self.schedule_refresh()
            elif msg.member == "NameOwnerChanged" and msg.body and msg.body[0] == WINBAR_NAME:
                new_owner = msg.body[2]
                if new_owner:
                    log.info("winbar appeared on the bus; full-screen pause active")
                    self.schedule_refresh()
                else:
                    log.info("winbar left the bus; full-screen pause off")
                    self.controller.set_available(False)
        except Exception:  # never let a bad signal kill the daemon
            log.exception("winbar signal handling failed")

    def schedule_refresh(self) -> None:
        self._dirty = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.ensure_future(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        while self._dirty:
            self._dirty = False
            await self.refresh()

    async def refresh(self) -> None:
        windows = await self.list_windows()
        if windows is None:
            self.controller.set_available(False)
        else:
            self.controller.set_windows(windows)

    async def list_windows(self) -> list[WindowInfo] | None:
        from dbus_fast import Message, MessageType

        try:
            reply = await asyncio.wait_for(self.bus.call(Message(
                destination=WINBAR_NAME, path=WINBAR_PATH, interface=WINBAR_IFACE, member="List")), 3)
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            log.debug("winbar List failed: %s", e)
            return None
        if reply is None or reply.message_type != MessageType.METHOD_RETURN:
            return None
        try:
            return [WindowInfo.from_struct(s) for s in reply.body[0]]
        except (IndexError, TypeError):
            return None


