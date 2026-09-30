"""Tests for the JSON CLI contract the Electron app depends on.

The app shells out to this CLI and parses stdout as JSON, so these tests pin the
shape of that output, the exit codes, and the fact that no prose leaks into --json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wallpaper_picker import cli, config
from wallpaper_picker.workshop import Wallpaper


def make_wallpaper(**kw) -> Wallpaper:
    """A playable wallpaper, so command tests never need a real Steam library."""
    fields = {
        "id": "1", "path": Path("/tmp/wp"), "source": "workshop",
        "title": "Test Wallpaper", "type": "video", "raw_type": "video",
        "file": "a.mp4",
    }
    fields.update(kw)
    return Wallpaper(**fields)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point config.load/save at throwaway values so tests never touch the real config."""
    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config())
    monkeypatch.setattr(config, "save", lambda *a, **k: None)
    return tmp_path / "config.json"


# --------------------------------------------------------------------------- helpers

def test_emit_ok_returns_zero_and_prints_json(capsys):
    assert cli.emit({"ok": True, "x": 1}, True) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "x": 1}


def test_emit_failure_returns_one(capsys):
    assert cli.emit({"ok": False}, True) == 1


def test_fail_prints_json_and_returns_code(capsys):
    assert cli._fail("boom", True, 3) == 3
    assert json.loads(capsys.readouterr().out) == {"ok": False, "message": "boom"}


def test_fail_without_json_uses_stderr(capsys):
    assert cli._fail("boom", False) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "boom" in captured.err


# --------------------------------------------------------------------------- browse

def test_browse_without_a_key_is_a_clean_error(capsys, isolated_config):
    """Exit code 3 tells the app to open the Settings pane."""
    code = cli.main(["browse", "--json"])
    assert code == 3
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "API key" in out["message"]


def test_browse_passes_key_sort_search_and_tags(capsys, isolated_config, monkeypatch):
    from wallpaper_picker import steam

    seen = {}

    def fake_query(key, **kw):
        seen["key"] = key
        seen.update(kw)
        return ([steam.parse_item({"publishedfileid": "1", "title": "T"})], 7)

    monkeypatch.setattr(steam, "query", fake_query)
    monkeypatch.setattr(steam, "installed_item_dirs", lambda *a, **k: {"1": None})
    code = cli.main(["browse", "--json", "--key", "K", "--sort", "popular",
                     "--page", "2", "--search", "dragon", "--tag", "Anime"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert seen["key"] == "K" and seen["sort"] == "popular"
    # A search is ranked locally across several pages, so it always starts from page 1 and
    # --page is ignored; --page still pages through browsing with no search term.
    assert seen["page"] == 1 and seen["search"] == "dragon" and seen["tags"] == ["Anime"]
    assert out["total"] == 7
    assert out["items"][0]["installed"] is True      # marked from the local library
    assert set(out) >= {"items", "total", "page", "tags", "sorts"}


def test_browse_uses_the_saved_key(capsys, monkeypatch):
    from wallpaper_picker import steam

    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config(steam_api_key="SAVED"))
    monkeypatch.setattr(steam, "query", lambda key, **kw: ([], 0))
    monkeypatch.setattr(steam, "installed_item_dirs", lambda *a, **k: {})
    assert cli.main(["browse", "--json"]) == 0
    json.loads(capsys.readouterr().out)


def test_browse_reports_a_steam_error(capsys, isolated_config, monkeypatch):
    from wallpaper_picker import steam

    def boom(key, **kw):
        raise steam.SteamError("Steam rejected the API key")

    monkeypatch.setattr(steam, "query", boom)
    assert cli.main(["browse", "--json", "--key", "K"]) == 2
    assert "rejected" in json.loads(capsys.readouterr().out)["message"]


# --------------------------------------------------------------------------- install

def test_install_without_steam_libraries_fails_cleanly(capsys, isolated_config, monkeypatch):
    from wallpaper_picker import steam

    monkeypatch.setattr(steam, "steam_libraries", lambda *a, **k: [])
    assert cli.main(["install", "5", "--json"]) == 2
    assert "Steam library" in json.loads(capsys.readouterr().out)["message"]


def test_install_without_a_login_asks_for_one(capsys, isolated_config, monkeypatch, tmp_path):
    from wallpaper_picker import steam

    monkeypatch.setattr(steam, "steam_libraries", lambda *a, **k: [tmp_path / "steam/steamapps"])
    monkeypatch.setattr(steam, "login_state", lambda *a, **k: (None, False))
    assert cli.main(["install", "5", "--json"]) == 3


def test_install_success_reports_the_folder(capsys, isolated_config, monkeypatch, tmp_path):
    from wallpaper_picker import steam

    monkeypatch.setattr(steam, "steam_libraries", lambda *a, **k: [tmp_path / "steam/steamapps"])
    monkeypatch.setattr(steam, "login_state", lambda *a, **k: ("batty251", False))
    monkeypatch.setattr(steam, "download", lambda *a, **k: steam.SteamCmdResult(
        True, "Downloaded", tmp_path / "item"))
    assert cli.main(["install", "5", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["message"] == "Downloaded."


def test_install_guard_prompt_is_reported(capsys, isolated_config, monkeypatch, tmp_path):
    from wallpaper_picker import steam

    monkeypatch.setattr(steam, "steam_libraries", lambda *a, **k: [tmp_path / "steam/steamapps"])
    monkeypatch.setattr(steam, "login_state", lambda *a, **k: ("batty251", False))
    monkeypatch.setattr(steam, "download", lambda *a, **k: steam.SteamCmdResult(
        False, "code?", None, needs_guard=True))
    assert cli.main(["install", "5", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["needs_guard"] is True and "Steam Guard" in out["message"]


# --------------------------------------------------------------------------- options

def test_options_json_lists_every_setting(capsys, isolated_config):
    assert cli.main(["options", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    for key in ("pause_on_battery", "battery_threshold", "pause_ignore_appids",
                "start_on_login", "startup_delay", "pause_on_fullscreen", "pause_scope"):
        assert key in out["options"], key


def test_options_rejects_an_unknown_key(capsys, isolated_config):
    assert cli.main(["options", "--json", "--set", "nonsense=1"]) == 2
    assert "unknown option" in json.loads(capsys.readouterr().out)["message"]


def test_options_set_coerces_types(capsys, isolated_config, monkeypatch):
    saved = {}
    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config(enabled=False))
    monkeypatch.setattr(config, "save", lambda cfg, *a, **k: saved.update(cfg.options.__dict__))
    assert cli.main(["options", "--json", "--set", "pause_on_battery=true",
                     "--set", "pause_ignore_appids=firefox,vlc"]) == 0
    assert saved["pause_on_battery"] is True
    assert saved["pause_ignore_appids"] == ["firefox", "vlc"]


# --------------------------------------------------------------------------- wiring

@pytest.mark.parametrize("argv", [
    ["state", "--json"],
    ["monitors", "--json"],
    ["wallpapers", "--json"],
    ["options", "--json"],
    ["login-status", "--json"],
    ["browse", "--json", "--key", ""],
])
def test_app_facing_commands_are_registered(argv, capsys):
    """Every command the app calls must exist and return a parseable JSON object."""
    cli.main(argv)
    out = capsys.readouterr().out
    if out.strip():
        json.loads(out)

def test_install_reports_not_signed_in_rather_than_failing_obscurely(capsys, isolated_config,
                                                                    monkeypatch, tmp_path):
    """There is no pre-flight any more (see test_steamcmd.py -- file inspection gave a
    false negative on a signed-in machine). Instead steamcmd is run, and its own
    'Cached credentials not found' is translated into the one-time login instructions."""
    from wallpaper_picker import steam

    monkeypatch.setattr(steam, "steam_libraries", lambda *a, **k: [tmp_path / "steam/steamapps"])
    monkeypatch.setattr(steam, "find_steamcmd", lambda: tmp_path / "steamcmd.sh")
    monkeypatch.setattr(steam, "login_state", lambda *a, **k: ("batty251", False))
    no_creds = ("Cached credentials not found.\npassword:\n"
                "Proceeding with login using username/password.\nERROR (Invalid Password)")
    monkeypatch.setattr(steam, "download", lambda *a, **k: steam.parse_steamcmd_output(
        no_creds, "5", tmp_path / "steam/steamapps"))

    assert cli.main(["install", "5", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["needs_login"] is True
    assert "+login" in out["message"] and "+quit" in out["message"]


def test_install_does_not_block_a_signed_in_machine(capsys, isolated_config,
                                                    monkeypatch, tmp_path):
    """The regression that mattered: a signed-in user was refused because the app looked
    for credential files that steamcmd never writes."""
    from wallpaper_picker import steam

    monkeypatch.setattr(steam, "steam_libraries", lambda *a, **k: [tmp_path / "steam/steamapps"])
    monkeypatch.setattr(steam, "login_state", lambda *a, **k: ("batty251", False))
    monkeypatch.setattr(steam, "find_steamcmd", lambda: tmp_path / "steamcmd.sh")
    called = {}
    monkeypatch.setattr(steam, "download", lambda *a, **k: called.update(k) or
                        steam.SteamCmdResult(True, "Success. Downloaded item 5", tmp_path / "i"))

    assert cli.main(["install", "5", "--json"]) == 0
    assert called, "steamcmd must actually be run"
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_login_status_probes_steamcmd_for_real(capsys, isolated_config, monkeypatch, tmp_path):
    from wallpaper_picker import steam

    exe = tmp_path / "steamcmd.sh"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(steam, "find_steamcmd", lambda: exe)
    monkeypatch.setattr(steam, "steam_libraries", lambda *a, **k: [tmp_path / "steam/steamapps"])
    monkeypatch.setattr(steam, "login_state", lambda *a, **k: ("batty251", False))
    monkeypatch.setattr(steam, "probe_login", lambda *a, **k: (True, "Logging in using cached credentials."))

    assert cli.main(["login-status", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["logged_in"] is True
    assert out["ready"] is True


# ------------------------------------------- action commands accept --json (real bug)

# Every command the desktop app runs, exactly as main.js invokes it.
APP_COMMANDS = {
    "state": ["state"],
    "wallpapers": ["wallpapers"],
    "monitors": ["monitors"],
    "options": ["options"],
    "login-status": ["login-status"],
    "browse": ["browse"],
    "install": ["install", "5"],
    "save-steam": ["save-steam"],
    "autostart": ["autostart"],
    "set": ["set", "5"],
    "clear": ["clear", "HDMI-A-1"],
    "stop": ["stop"],
    "status": ["status"],
    "apply": ["apply"],
}


def _subparsers():
    import argparse

    parser = cli.build_parser()
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


@pytest.mark.parametrize("command", sorted(APP_COMMANDS))
def test_every_app_command_accepts_json(command):
    """The UI invokes each of these with --json and parses stdout as JSON.

    This was silently broken: set, clear, stop, status and apply had no --json flag, so
    argparse rejected the argument and EVERY action in the UI failed with a usage error.
    The UI had been written against an interface that was never implemented.
    """
    parser = _subparsers().choices[command]
    flags = [o for a in parser._actions for o in a.option_strings]
    assert "--json" in flags, f"{command} must accept --json (the app passes it)"

    # a subparser is parsed WITHOUT the command word (use the full parser for that)
    ns = parser.parse_args(APP_COMMANDS[command][1:] + ["--json"])
    assert ns.json is True


@pytest.mark.parametrize("command", sorted(APP_COMMANDS))
def test_app_commands_parse_without_a_usage_error(command):
    """A SystemExit here is what the user saw as 'an error trying to use a wallpaper'."""
    parser = _subparsers().choices[command]
    try:
        parser.parse_args(APP_COMMANDS[command][1:] + ["--json"])
    except SystemExit:  # pragma: no cover - only on regression
        pytest.fail(f"{command} rejects the arguments the app passes")


def test_set_json_reports_the_applied_wallpaper(capsys, monkeypatch):
    from wallpaper_picker import cli, client, config, monitors, workshop

    wp = make_wallpaper(id="42", title="Nice")
    monkeypatch.setattr(workshop, "scan", lambda *a, **k: [wp])
    monkeypatch.setattr(workshop, "find_wallpaper", lambda w, q: wp)
    monkeypatch.setattr(monitors, "list_monitors", lambda *a, **k: [])
    monkeypatch.setattr(config, "resolve_target", lambda t, m: ["HDMI-A-1"])
    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config())
    monkeypatch.setattr(config, "save", lambda *a, **k: None)
    monkeypatch.setattr(client, "apply", lambda: {"ok": True, "message": "Applied"})

    assert cli.main(["set", "42", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["wallpaper"] == "42"
    assert out["targets"] == ["HDMI-A-1"]


def test_set_json_reports_an_unknown_wallpaper_cleanly(capsys, monkeypatch):
    from wallpaper_picker import cli, config, monitors, workshop

    monkeypatch.setattr(workshop, "scan", lambda *a, **k: [])
    monkeypatch.setattr(workshop, "find_wallpaper", lambda w, q: None)
    monkeypatch.setattr(monitors, "list_monitors", lambda *a, **k: [])
    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config())

    assert cli.main(["set", "nope", "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "matches" in out["message"]


def test_clear_json_emits_the_daemon_result(capsys, monkeypatch):
    from wallpaper_picker import cli, client, config, monitors

    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config())
    monkeypatch.setattr(config, "save", lambda *a, **k: None)
    monkeypatch.setattr(monitors, "list_monitors", lambda *a, **k: [])
    monkeypatch.setattr(config, "resolve_target", lambda t, m: ["HDMI-A-1"])
    monkeypatch.setattr(client, "apply", lambda: {"ok": True, "message": "Applied"})

    assert cli.main(["clear", "HDMI-A-1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["targets"] == ["HDMI-A-1"]


def test_stop_json_reports_what_it_did(capsys, monkeypatch):
    from wallpaper_picker import cli, client, config, procs

    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config())
    monkeypatch.setattr(config, "save", lambda *a, **k: None)
    monkeypatch.setattr(client, "daemon_running", lambda: False)
    monkeypatch.setattr(procs, "find_screen_engines", lambda: [(111, "x")])
    monkeypatch.setattr(procs, "terminate", lambda pid, st: True)
    monkeypatch.setattr(procs, "starttime", lambda pid: 1)

    assert cli.main(["stop", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["stopped"] == 1


def test_stop_json_with_nothing_running(capsys, monkeypatch):
    from wallpaper_picker import cli, client, config, procs

    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config())
    monkeypatch.setattr(config, "save", lambda *a, **k: None)
    monkeypatch.setattr(client, "daemon_running", lambda: False)
    monkeypatch.setattr(procs, "find_screen_engines", lambda: [])

    assert cli.main(["stop", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stopped"] == 0 and "No wallpaper engine" in out["message"]


def test_action_json_when_the_daemon_is_unreachable(capsys, monkeypatch):
    """A dead daemon must be a JSON error, never a traceback or a bare exit code."""
    from wallpaper_picker import cli, client, config, monitors

    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config())
    monkeypatch.setattr(config, "save", lambda *a, **k: None)
    monkeypatch.setattr(monitors, "list_monitors", lambda *a, **k: [])
    monkeypatch.setattr(config, "resolve_target", lambda t, m: ["HDMI-A-1"])

    def dead():
        raise client.DaemonError("no daemon on the bus")

    monkeypatch.setattr(client, "apply", dead)

    assert cli.main(["clear", "HDMI-A-1", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "daemon" in out["message"]


def test_apply_json_works_and_reenables(capsys, monkeypatch):
    from wallpaper_picker import cli, client, config

    cfg = config.Config(enabled=False)
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    saved = {}
    monkeypatch.setattr(config, "save", lambda c, *a, **k: saved.update(enabled=c.enabled))
    monkeypatch.setattr(client, "apply", lambda: {"ok": True, "message": "Applied"})

    assert cli.main(["apply", "--json"]) == 0
    assert saved["enabled"] is True
    assert json.loads(capsys.readouterr().out)["ok"] is True
