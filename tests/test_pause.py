"""Full-screen pause state machine, and the winbar watcher against a fake bus."""

import asyncio
from types import SimpleNamespace

from wallpaper_picker.pause import (WINBAR_IFACE, WINBAR_NAME, PauseController, WindowInfo, WinbarWatcher,
                                    compute_paused)


def win(id="1", output="DP-1", focused=True, fullscreen=True, minimized=False, active=True, app="mpv"):
    return WindowInfo(id, app, f"{app} window", output, focused, minimized, fullscreen, active)


ENGINES = {"DP-1": {"DP-1"}, "HDMI-A-1": {"HDMI-A-1"}}


def test_compute_paused_rules():
    assert compute_paused([], ENGINES) == set()
    assert compute_paused([win()], ENGINES) == {"DP-1", "HDMI-A-1"}
    assert compute_paused([win(focused=False)], ENGINES) == set()
    assert compute_paused([win(fullscreen=False)], ENGINES) == set()
    assert compute_paused([win(minimized=True)], ENGINES) == set()
    assert compute_paused([win(active=False)], ENGINES) == set()
    assert compute_paused([win()], ENGINES, scope="monitor") == {"DP-1"}
    assert compute_paused([win(output="")], ENGINES, scope="monitor") == {"DP-1", "HDMI-A-1"}
    assert compute_paused([win()], ENGINES, enabled=False) == set()
    assert compute_paused([win()], {"DP-1+HDMI-A-1": {"DP-1", "HDMI-A-1"}}, scope="monitor") == {"DP-1+HDMI-A-1"}


def make_controller(**kw):
    calls = []
    c = PauseController(lambda k: calls.append(("pause", k)), lambda k: calls.append(("resume", k)), **kw)
    return c, calls


def test_transitions_only():
    c, calls = make_controller()
    c.set_engines({"E": {"HDMI-A-1"}})
    assert calls == []
    c.set_windows([win()])
    c.set_windows([win(), win(id="2", focused=False, fullscreen=False)])  # no change -> no extra call
    assert calls == [("pause", "E")]
    c.set_windows([win(focused=False)])  # fullscreen window lost focus
    assert calls == [("pause", "E"), ("resume", "E")]


def test_winbar_disappearing_resumes():
    c, calls = make_controller()
    c.set_engines({"E": {"DP-1"}})
    c.set_windows([win()])
    c.set_available(False)
    assert calls == [("pause", "E"), ("resume", "E")]
    assert c.blocking == []


def test_restarted_engine_is_repaused():
    c, calls = make_controller()
    c.set_engines({"E": {"DP-1"}})
    c.set_windows([win()])
    c.forget("E")  # engine restarted -> new process is running
    c.set_engines({"E": {"DP-1"}})
    assert calls == [("pause", "E"), ("pause", "E")]


def test_removed_engine_not_resumed():
    c, calls = make_controller()
    c.set_engines({"E": {"DP-1"}})
    c.set_windows([win()])
    c.set_engines({})
    c.set_windows([])
    assert calls == [("pause", "E")]


def test_disable_resumes():
    c, calls = make_controller()
    c.set_engines({"E": {"DP-1"}})
    c.set_windows([win()])
    c.configure("all", False)
    assert calls[-1] == ("resume", "E")


class FakeBus:
    """Just enough of dbus_fast.aio.MessageBus for WinbarWatcher."""

    def __init__(self):
        self.handlers = []
        self.windows = []
        self.present = True
        self.list_calls = 0

    def add_message_handler(self, h):
        self.handlers.append(h)

    async def call(self, msg):
        from dbus_fast import MessageType
        if msg.member == "AddMatch":
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[])
        if msg.member == "List":
            self.list_calls += 1
            if not self.present:
                return SimpleNamespace(message_type=MessageType.ERROR, body=["no such name"])
            structs = [[w.id, w.app_id, w.title, w.output, w.focused, w.minimized, w.fullscreen,
                        0, 0, 100, 100, w.on_active_workspace] for w in self.windows]
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[structs])
        raise AssertionError(msg.member)

    def emit(self, member, body=(), interface=WINBAR_IFACE):
        m = SimpleNamespace(interface=interface, member=member, body=list(body))
        for h in self.handlers:
            h(m)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_watcher_with_fake_winbar():
    bus = FakeBus()
    c, calls = make_controller()
    c.set_engines({"E": {"HDMI-A-1"}})
    w = WinbarWatcher(bus, c)
    await w.start()
    await settle()
    assert c.available and calls == []

    bus.windows = [win(output="HDMI-A-1")]
    bus.emit("FocusChanged", ["1"])
    await settle()
    assert calls == [("pause", "E")]

    bus.windows = [win(output="HDMI-A-1", fullscreen=False)]
    bus.emit("WindowsChanged")
    await settle()
    assert calls == [("pause", "E"), ("resume", "E")]

    # winbar goes away while something is full screen -> resume and stay running
    bus.windows = [win()]
    bus.emit("WindowsChanged")
    await settle()
    assert calls[-1] == ("pause", "E")
    bus.present = False
    bus.emit("NameOwnerChanged", [WINBAR_NAME, ":1.5", ""], interface="org.freedesktop.DBus")
    await settle()
    assert calls[-1] == ("resume", "E") and not c.available

    # ...and comes back
    bus.present = True
    bus.emit("NameOwnerChanged", [WINBAR_NAME, "", ":1.9"], interface="org.freedesktop.DBus")
    await settle()
    assert c.available and calls[-1] == ("pause", "E")


async def test_watcher_without_winbar():
    bus = FakeBus()
    bus.present = False
    c, calls = make_controller()
    c.set_engines({"E": {"DP-1"}})
    await WinbarWatcher(bus, c).start()
    await settle()
    assert not c.available and calls == []


async def test_signal_burst_is_coalesced():
    bus = FakeBus()
    c, _ = make_controller()
    w = WinbarWatcher(bus, c)
    await w.start()
    await settle()
    before = bus.list_calls
    for _ in range(10):
        bus.emit("WindowsChanged")
    await settle()
    assert bus.list_calls - before <= 2
