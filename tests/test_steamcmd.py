"""Tests for steamcmd discovery, command building and output interpretation."""

from __future__ import annotations

from pathlib import Path

import pytest

from wallpaper_picker import steam

APP = steam.WE_APP_ID


# --------------------------------------------------------------------------- libraries

def _make_library(root: Path, items: tuple[str, ...] = ()) -> Path:
    sa = root / "steamapps"
    (sa / "workshop" / "content" / str(APP)).mkdir(parents=True)
    for i in items:
        d = sa / "workshop" / "content" / str(APP) / i
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text("{}")
    return sa


def test_steam_libraries_finds_standard_root(tmp_path, monkeypatch):
    sa = _make_library(tmp_path / ".steam/debian-installation")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert sa in steam.steam_libraries()


def test_steam_libraries_follows_libraryfolders_vdf(tmp_path, monkeypatch):
    home = tmp_path / "home"
    extra = tmp_path / "external"
    _make_library(extra)
    sa = _make_library(home / ".steam/debian-installation")
    (sa / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % extra
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    libs = steam.steam_libraries()
    assert sa in libs
    assert (extra / "steamapps") in libs


def test_steam_libraries_ignores_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert steam.steam_libraries() == []


def test_installed_item_dirs_maps_and_dedupes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _make_library(home / ".steam/debian-installation", ("111", "222"))
    _make_library(home / ".local/share/Steam", ("111", "333"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    got = steam.installed_item_dirs()
    assert sorted(got) == ["111", "222", "333"]
    assert got["111"].name == "111"


def test_installed_item_dirs_skips_non_numeric_folders(tmp_path, monkeypatch):
    home = tmp_path / "home"
    sa = _make_library(home / ".steam/debian-installation", ("111",))
    (sa / "workshop" / "content" / str(APP) / "not-an-id").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert list(steam.installed_item_dirs()) == ["111"]


def test_login_state_reads_account_and_guard(tmp_path):
    sa = _make_library(tmp_path / "steam")
    cfg = sa.parent / "config"
    cfg.mkdir()
    (cfg / "loginusers.vdf").write_text(
        '"users"\n{\n\t"1"\n\t{\n\t\t"AccountName"\t\t"batty251"\n\t\t"UseSteamGuard"\t\t"1"\n\t}\n}\n'
    )
    assert steam.login_state(sa) == ("batty251", True)


def test_login_state_without_file(tmp_path):
    assert steam.login_state(tmp_path / "steam/steamapps") == (None, False)


# --------------------------------------------------------------------------- command

def test_build_argv_logs_in_and_targets_the_library(tmp_path):
    sa = _make_library(tmp_path / "steam")
    argv = steam.build_download_argv(Path("/usr/bin/steamcmd"), "3122339805", sa,
                                     username="batty251", password="pw")
    assert argv[0] == "/usr/bin/steamcmd"
    assert argv[1:3] == ["+force_install_dir", str(sa.parent)]
    assert argv[3:6] == ["+login", "batty251", "pw"]
    assert "+workshop_download_item" in argv
    i = argv.index("+workshop_download_item")
    assert argv[i + 1] == str(APP) and argv[i + 2] == "3122339805"
    assert argv[-1] == "+quit"


def test_build_argv_anonymous_when_no_username(tmp_path):
    sa = _make_library(tmp_path / "steam")
    argv = steam.build_download_argv(Path("/x/steamcmd"), "5", sa)
    assert argv[3:5] == ["+login", "anonymous"]


def test_find_steamcmd_uses_path_when_nothing_managed_exists(monkeypatch, tmp_path):
    """Hermetic: no candidate paths, so only PATH can answer."""
    fake = _managed(tmp_path)                 # has its linux32/ client, so it is usable
    monkeypatch.setattr(steam, "_CANDIDATES", ())
    monkeypatch.setattr(steam.shutil, "which", lambda name: str(fake))
    assert steam.find_steamcmd() == fake


def test_find_steamcmd_returns_none_when_absent(monkeypatch, tmp_path):
    """Nothing managed, nothing on PATH -- and the real machine's steamcmd must not leak
    into the test, so the candidate list is emptied rather than HOME being faked
    (Path.expanduser() reads $HOME, not Path.home(), so faking the latter is not enough)."""
    monkeypatch.setattr(steam, "_CANDIDATES", ())
    monkeypatch.setattr(steam.shutil, "which", lambda name: None)
    assert steam.find_steamcmd() is None


# --------------------------------------------------------------------------- output

OK_OUTPUT = """
Connecting anonymously to Steam Public...OK
Downloading item 3122339805 ...
Success. Downloaded item 3122339805 to "steamapps/workshop/content/431960/3122339805" (1234 bytes)
"""

NOMATCH_OUTPUT = """
Connecting anonymously to Steam Public...OK
Downloading item 2833160490 ...
ERROR! Download item 2833160490 failed (No match).
"""

GUARD_OUTPUT = """
Logging in user 'batty251' to Steam Public...
This account is protected by Steam Guard.
Please enter the current code from your Steam Guard Mobile Authenticator app
"""

BADPASS_OUTPUT = "FAILED (Invalid Password)\nLogin Failure: Invalid Password\n"


def test_parse_success_requires_the_item_folder(tmp_path):
    sa = _make_library(tmp_path / "steam")
    res = steam.parse_steamcmd_output(OK_OUTPUT, "3122339805", sa)
    assert res.ok is False          # folder was not actually created


def test_parse_success_when_folder_exists(tmp_path):
    sa = _make_library(tmp_path / "steam", ("3122339805",))
    res = steam.parse_steamcmd_output(OK_OUTPUT, "3122339805", sa)
    assert res.ok is True
    assert res.item_dir == sa / "workshop" / "content" / str(APP) / "3122339805"


def test_parse_anonymous_no_match_reported_as_failure(tmp_path):
    sa = _make_library(tmp_path / "steam")
    res = steam.parse_steamcmd_output(NOMATCH_OUTPUT, "2833160490", sa)
    assert res.ok is False
    assert res.needs_guard is False
    assert "No match" in res.output


def test_parse_steam_guard_prompts_are_flagged(tmp_path):
    sa = _make_library(tmp_path / "steam")
    res = steam.parse_steamcmd_output(GUARD_OUTPUT, "5", sa)
    assert res.ok is False
    assert res.needs_guard is True


def test_parse_bad_password_is_not_guard(tmp_path):
    sa = _make_library(tmp_path / "steam")
    res = steam.parse_steamcmd_output(BADPASS_OUTPUT, "5", sa)
    assert res.ok is False and res.needs_guard is False


def test_parse_empty_folder_is_not_a_success(tmp_path):
    sa = _make_library(tmp_path / "steam", ("77",))
    (sa / "workshop" / "content" / str(APP) / "77" / "project.json").unlink()
    res = steam.parse_steamcmd_output("", "77", sa)
    assert res.ok is False


def test_download_without_steamcmd_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(steam, "_CANDIDATES", ())
    monkeypatch.setattr(steam.shutil, "which", lambda name: None)
    sa = _make_library(tmp_path / "steam")
    with pytest.raises(steam.SteamError, match="not installed"):
        steam.download("5", sa)


def test_download_uses_injected_runner(tmp_path):
    sa = _make_library(tmp_path / "steam")
    exe = tmp_path / "steamcmd"
    exe.write_text("#!/bin/sh\n")
    calls = []

    def runner(argv, code, timeout=0):
        calls.append(argv)
        item = sa / "workshop" / "content" / str(APP) / "3122339805"
        item.mkdir(parents=True, exist_ok=True)
        (item / "project.json").write_text("{}")
        return OK_OUTPUT

    res = steam.download("3122339805", sa, steamcmd=exe, runner=runner)
    assert res.ok is True
    assert calls and calls[0][0] == str(exe)

# ------------------------------------------------- login detection (verified behaviour)

def test_no_cached_credentials_is_reported_as_needs_login(tmp_path):
    """Real output captured on this machine: no cached credentials + a password prompt."""
    sa = _make_library(tmp_path / "steam")
    text = ("Cached credentials not found.\n"
            "password:\n"
            "Proceeding with login using username/password.\n"
            "Logging in user 'batty251' [U:1:81547896] to Steam Public...ERROR (Invalid Password)")
    res = steam.parse_steamcmd_output(text, "3122339805", sa)
    assert res.ok is False
    assert res.needs_login is True
    assert res.needs_guard is False


def test_invalid_password_is_also_a_login_problem(tmp_path):
    sa = _make_library(tmp_path / "steam")
    res = steam.parse_steamcmd_output("Login Failure: Invalid Password\n", "5", sa)
    assert res.needs_login is True


def test_a_successful_download_beats_a_login_warning(tmp_path):
    """Steam logs noise even on success; the item folder is the truth."""
    sa = _make_library(tmp_path / "steam", ("77",))
    res = steam.parse_steamcmd_output("Cached credentials not found.\nSuccess.\n", "77", sa)
    assert res.ok is True and res.needs_login is False


def test_probe_login_reads_steamcmds_own_output(tmp_path, monkeypatch):
    """The only reliable signal: steamcmd printing that it used cached credentials.

    File inspection was wrong. On this machine steamcmd logged in with cached credentials
    while writing no ssfn* and no loginusers.vdf of its own, so the old check reported
    "not signed in" and blocked a working setup.
    """
    exe = tmp_path / "steamcmd.sh"
    exe.write_text("#!/bin/sh\n")

    class Proc:
        stdout = "Logging in using cached credentials.\nLogging in user 'batty251'...OK\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(steam.subprocess, "run", lambda *a, **k: Proc())
    ok, text = steam.probe_login(exe)
    assert ok is True
    assert "cached credentials" in text


def test_probe_login_false_when_credentials_are_not_cached(tmp_path, monkeypatch):
    exe = tmp_path / "steamcmd.sh"
    exe.write_text("#!/bin/sh\n")

    class Proc:
        stdout = "Cached credentials not found.\npassword:\nERROR (Invalid Password)\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(steam.subprocess, "run", lambda *a, **k: Proc())
    ok, _ = steam.probe_login(exe)
    assert ok is False


def test_probe_login_handles_a_missing_or_failing_steamcmd(tmp_path, monkeypatch):
    # probe_login(None) looks steamcmd up itself, so pin discovery to "nothing here" --
    # otherwise this machine's real install answers and the test is not hermetic.
    monkeypatch.setattr(steam, "find_steamcmd", lambda: None)
    assert steam.probe_login(None) == (False, "steamcmd is not installed.")

    exe = tmp_path / "steamcmd.sh"
    exe.write_text("#!/bin/sh\n")

    def boom(*a, **k):
        raise OSError("no such file")

    monkeypatch.setattr(steam.subprocess, "run", boom)
    ok, text = steam.probe_login(exe)
    assert ok is False and "could not run steamcmd" in text


def test_probe_login_never_passes_stdin(tmp_path, monkeypatch):
    """stdin must be closed, or a password prompt would hang the app."""
    exe = tmp_path / "steamcmd.sh"
    exe.write_text("#!/bin/sh\n")
    seen = {}

    class Proc:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run(argv, **kw):
        seen.update(kw)
        return Proc()

    monkeypatch.setattr(steam.subprocess, "run", fake_run)
    steam.probe_login(exe, "batty251")
    assert seen.get("stdin") is steam.subprocess.DEVNULL
    assert seen.get("timeout")


def test_login_hint_names_the_binary_and_the_command(tmp_path):
    exe = tmp_path / "steamcmd.sh"
    hint = steam.login_hint(exe)
    assert str(exe) in hint
    assert "+login" in hint and "+quit" in hint
    assert "cannot be downloaded anonymously" in hint


# --------------------------------------------- discovery robustness (real bug, 2026-09-30)

def _managed(root, name="steamcmd.sh"):
    exe = root / name
    exe.write_text("#!/bin/sh\nexec real\n")
    plat = root / "linux32"
    plat.mkdir(parents=True, exist_ok=True)
    (plat / "steamcmd").write_bytes(b"\x7fELF")     # the sibling client steamcmd.sh wants
    return exe


def test_a_real_install_is_usable(tmp_path):
    assert steam.is_usable_steamcmd(_managed(tmp_path)) is True


def test_a_symlink_into_another_directory_is_not_usable(tmp_path):
    """steamcmd.sh resolves its client from $0, so this fails with
    "Couldn't find steamcmd at <link dir>/linux32/steamcmd"."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real = _managed(real_dir)
    link = tmp_path / "bin" / "steamcmd"
    link.parent.mkdir()
    link.symlink_to(real)
    assert steam.is_usable_steamcmd(link) is False


def test_a_wrapper_script_counts_as_usable(tmp_path):
    """~/.local/bin/steamcmd is a wrapper, because a symlink cannot work."""
    wrapper = tmp_path / "steamcmd"
    wrapper.write_text("#!/bin/sh\nexec \"$HOME/.local/share/wallpaper-picker/"
                       "steamcmd/steamcmd.sh\" \"$@\"\n")
    assert steam.is_usable_steamcmd(wrapper) is True


def test_missing_path_is_not_usable(tmp_path):
    assert steam.is_usable_steamcmd(None) is False
    assert steam.is_usable_steamcmd(tmp_path / "nope") is False


def test_broken_steamcmd_paths_reports_a_stale_symlink(tmp_path, monkeypatch):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real = _managed(real_dir)
    link = tmp_path / "bin" / "steamcmd"
    link.parent.mkdir()
    link.symlink_to(real)
    monkeypatch.setattr(steam, "_CANDIDATES", (str(link),))
    monkeypatch.setattr(steam.shutil, "which", lambda name: None)
    assert steam.broken_steamcmd_paths() == [link]


def test_broken_steamcmd_is_never_chosen_over_a_working_one(tmp_path, monkeypatch):
    """The failure mode that mattered: a symlink on PATH hijacked discovery and broke
    the app even though a perfectly good install existed."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real = _managed(real_dir)
    link = tmp_path / "bin" / "steamcmd"
    link.parent.mkdir()
    link.symlink_to(real)          # on PATH, but unusable
    monkeypatch.setattr(steam, "_CANDIDATES", (str(real),))
    monkeypatch.setattr(steam.shutil, "which", lambda name: str(link))
    assert steam.find_steamcmd() == real


def test_failure_reason_prefers_steamcmds_own_words():
    assert "No match" in steam.failure_reason("ERROR! Download item 5 failed (No match).")
    text = "steamcmd.sh[1]: Couldn't find steamcmd at /x/linux32/steamcmd, exiting"
    assert "Couldn't find steamcmd at" in steam.failure_reason(text)
    assert steam.failure_reason("nothing useful here") == steam.GENERIC_FAIL
