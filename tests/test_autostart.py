"""Tests for the autostart entry writer."""

from __future__ import annotations

from wallpaper_picker import autostart


def test_entry_without_delay_is_a_plain_exec():
    text = autostart.build_entry("/home/u/.local/bin/wallpaper-picker")
    assert "Exec=/home/u/.local/bin/wallpaper-picker --daemon" in text
    assert "sleep" not in text
    assert "X-GNOME-Autostart-enabled=true" in text


def test_entry_with_delay_sleeps_then_execs():
    text = autostart.build_entry("/bin/wp", delay=8)
    assert "sleep 8" in text
    assert "exec \"/bin/wp\" --daemon" in text


def test_delay_is_not_negative():
    assert "sleep" not in autostart.build_entry("/bin/wp", delay=-10)


def test_parse_delay_round_trips():
    assert autostart.parse_delay(autostart.build_entry("/bin/wp", delay=12)) == 12
    assert autostart.parse_delay(autostart.build_entry("/bin/wp")) == 0


def test_apply_writes_and_is_enabled(tmp_path):
    p = autostart.apply("/bin/wp", delay=3, home=tmp_path)
    assert p == tmp_path / ".config/autostart" / autostart.DESKTOP_NAME
    assert p.is_file()
    assert autostart.is_enabled(tmp_path) is True
    assert "sleep 3" in p.read_text()


def test_apply_disabled_removes_the_file(tmp_path):
    autostart.apply("/bin/wp", home=tmp_path)
    autostart.apply("/bin/wp", enabled=False, home=tmp_path)
    assert not autostart.autostart_path(tmp_path).exists()
    assert autostart.is_enabled(tmp_path) is False


def test_apply_disabled_when_never_created_is_safe(tmp_path):
    autostart.apply("/bin/wp", enabled=False, home=tmp_path)  # must not raise
    assert autostart.is_enabled(tmp_path) is False


def test_is_enabled_false_when_file_says_so(tmp_path):
    p = autostart.autostart_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(autostart.build_entry("/bin/wp", enabled=False))
    assert autostart.is_enabled(tmp_path) is False