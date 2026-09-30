"""Tests for the compatibility options: ignore list, power pause, config round-trip."""

from __future__ import annotations

from wallpaper_picker import config, pause


def _win(app_id: str = "firefox", title: str = "Mozilla", fullscreen: bool = True,
         focused: bool = True):
    return pause.WindowInfo(id="1", app_id=app_id, title=title, output="DP-1",
                            focused=focused, minimized=False, fullscreen=fullscreen)


# --------------------------------------------------------------------------- options

def test_new_options_defaults():
    o = config.Options()
    assert o.pause_on_battery is False
    assert o.battery_threshold == 20
    assert o.pause_ignore_appids == []
    assert o.start_on_login is True
    assert o.startup_delay == 0


def test_options_clamp_ranges():
    o = config.Options.from_dict({"battery_threshold": 500, "startup_delay": 900})
    assert o.battery_threshold == 100
    assert o.startup_delay == 300
    o2 = config.Options.from_dict({"battery_threshold": "junk", "startup_delay": "junk"})
    assert o2.battery_threshold == 20 and o2.startup_delay == 0


def test_ignore_appids_filters_blanks_and_non_strings():
    o = config.Options.from_dict({"pause_ignore_appids": ["a", "", "  ", 5]})
    assert o.pause_ignore_appids == ["a", "5"]


def test_ignore_appids_rejects_non_list():
    assert config.Options.from_dict({"pause_ignore_appids": "nope"}).pause_ignore_appids == []


def test_options_survive_a_config_round_trip():
    cfg = config.Config.from_dict({"options": {
        "pause_on_battery": True, "battery_threshold": 35,
        "pause_ignore_appids": ["steam_app_107410"], "start_on_login": False,
        "startup_delay": 12, "pause_scope": "monitor",
    }})
    again = config.Config.from_dict(cfg.to_dict())
    assert again.options == cfg.options


def test_old_config_without_new_keys_still_loads():
    """A config written by v2 must not break."""
    old = {"version": 1, "enabled": True,
           "slots": {"HDMI-A-1": {"wallpaper": "5", "fps": 30, "scaling": "fill"}},
           "options": {"pause_on_fullscreen": True, "pause_scope": "all"}}
    cfg = config.Config.from_dict(old)
    assert cfg.slots["HDMI-A-1"].clamp == "clamp"
    assert cfg.options.pause_on_battery is False


def test_slot_clamp_round_trips_and_rejects_junk():
    assert config.Slot.from_dict({"clamp": "repeat"}).clamp == "repeat"
    assert config.Slot.from_dict({"clamp": "junk"}).clamp == "clamp"
    assert config.Slot.from_dict({}).clamp == "clamp"


def test_apply_settings_carries_clamp():
    s = config.Slot()
    s.apply_settings({"clamp": "border", "fps": 24})
    assert s.clamp == "border" and s.fps == 24


# --------------------------------------------------------------------------- pause

def test_ignore_list_skips_by_app_id():
    wins = [_win(app_id="steam_app_107410", title="Arma 3")]
    assert pause.blocking_windows(wins) == wins
    assert pause.blocking_windows(wins, ["steam_app_107410"]) == []


def test_ignore_list_is_case_insensitive_and_matches_titles():
    assert pause.blocking_windows([_win(title="Some Movie.mp4")], ["movie.mp4"]) == []
    assert pause.blocking_windows([_win(app_id="Firefox")], ["firefox"]) == []


def test_ignore_list_ignores_blank_entries():
    wins = [_win(app_id="firefox")]
    assert pause.blocking_windows(wins, ["", "   "]) == wins


def test_controller_configure_updates_ignore_list():
    paused, resumed = [], []
    c = pause.PauseController(paused.append, resumed.append, scope="all", enabled=True)
    c.set_windows([_win(app_id="steam_app_1")])
    c.set_engines({"k": {"DP-1"}})
    assert c.paused == {"k"}
    c.configure("all", True, ["steam_app_1"])
    assert c.paused == set()


def test_power_pause_wins_over_nothing_fullscreen():
    resumed = []
    c = pause.PauseController(lambda k: None, resumed.append, scope="all", enabled=True)
    c.set_engines({"a": {"DP-1"}, "b": {"HDMI-A-1"}})
    c.set_power_paused(True)
    assert c.paused == {"a", "b"}
    c.set_power_paused(False)
    assert c.paused == set()
    assert "a" in resumed and "b" in resumed


def test_power_pause_and_fullscreen_are_combined():
    c = pause.PauseController(lambda k: None, lambda k: None, scope="monitor", enabled=True)
    c.set_engines({"a": {"DP-1"}, "b": {"HDMI-A-1"}})
    c.set_windows([_win()])          # full-screen on DP-1 only
    assert c.paused == {"a"}
    c.set_power_paused(True)         # battery: everything stops
    assert c.paused == {"a", "b"}
    c.set_power_paused(False)        # back on mains: only the covered output stays paused
    assert c.paused == {"a"}


def test_fullscreen_and_power_switches_are_independent():
    """`pause_on_fullscreen` governs full-screen pausing; the battery policy is its own
    option, so turning one off must not disable the other."""
    c = pause.PauseController(lambda k: None, lambda k: None, scope="all", enabled=False)
    c.set_engines({"a": {"DP-1"}})
    c.set_windows([_win()])
    assert c.paused == set()          # full-screen pausing switched off
    c.set_power_paused(True)
    assert c.paused == {"a"}          # battery policy still applies
    c.set_power_paused(False)
    assert c.paused == set()


def test_forget_clears_a_paused_engine_after_restart():
    c = pause.PauseController(lambda k: None, lambda k: None, scope="all", enabled=True)
    c.set_engines({"a": {"DP-1"}})
    c.set_power_paused(True)
    assert c.paused == {"a"}
    c.forget("a")
    assert c.paused == set()