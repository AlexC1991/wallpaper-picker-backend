"""Monitor discovery must work beyond COSMIC.

The project started on COSMIC, where `cosmic-randr list` is the only precise source. Anyone
else (sway/Hyprland, GNOME, KDE, plain X11) has no such tool, so without fallbacks the app
would find no monitors and appear broken. These tests pin the parsers and the order they
are tried in.
"""

from __future__ import annotations

import json

import pytest

from wallpaper_picker import monitors as M

# Verbatim `xrandr --query` output, trimmed to the lines that matter. The active mode is
# marked with '*' and the preferred one with '+' -- two monitors at different rates, so a
# parser that grabs the first mode line instead of the starred one is caught.
XRANDR = """Screen 0: minimum 16 x 16, current 4266 x 1200, maximum 32767 x 32767
HDMI-A-1 connected primary 2133x1200+2133+0 (normal left inverted right x axis y axis) 480mm x 270mm
   2133x1200     59.95*+
   1600x1200     59.87
   1280x1024     59.89
DP-1 connected 2133x1200+0+0 (normal left inverted right x axis y axis) 520mm x 290mm
   2133x1200    119.90*+
   1920x1080     60.00
VGA-1 disconnected (normal left inverted right x axis y axis)
"""

# `wlr-randr --json`, the shape sway/Hyprland emit.
WLR = json.dumps([
    {"name": "DP-2", "enabled": True, "make": "Dell Inc.", "model": "DELL U2720Q",
     "position": {"x": 0, "y": 0}, "scale": 1.0,
     "current_mode": {"width": 3840, "height": 2160, "refresh": 59.997}},
    {"name": "HDMI-A-1", "enabled": True, "make": "LG Electronics", "model": "LG FULL HD",
     "position": {"x": 3840, "y": 0}, "scale": 1.0,
     "current_mode": {"width": 1920, "height": 1080, "refresh": 60.0}},
])


# ------------------------------------------------------------------ xrandr

def test_xrandr_finds_connected_outputs_and_their_geometry():
    mons = {m.name: m for m in M.parse_xrandr(XRANDR)}
    assert set(mons) == {"HDMI-A-1", "DP-1", "VGA-1"}
    assert mons["DP-1"].x == 0
    assert mons["HDMI-A-1"].x == 2133          # left-to-right order comes from the offset
    assert (mons["DP-1"].width, mons["DP-1"].height) == (2133, 1200)


def test_xrandr_marks_a_disconnected_output_disabled():
    mons = {m.name: m for m in M.parse_xrandr(XRANDR)}
    assert mons["VGA-1"].enabled is False
    assert all(m.enabled for name, m in mons.items() if name != "VGA-1")


def test_xrandr_reads_the_active_refresh_not_the_first_mode():
    """DP-1 runs at 120Hz; the first listed mode is not the active one."""
    mons = {m.name: m for m in M.parse_xrandr(XRANDR)}
    assert mons["DP-1"].refresh == pytest.approx(119.90)
    assert mons["HDMI-A-1"].refresh == pytest.approx(59.95)


def test_xrandr_labels_left_and_right_by_x_position():
    labelled = M.label_positions(M.parse_xrandr(XRANDR))
    assert [(m.name, m.position) for m in labelled] == [("DP-1", "Left"), ("HDMI-A-1", "Right")]


# ------------------------------------------------------------------ wlr-randr

def test_wlr_randr_reads_geometry_and_names():
    mons = {m.name: m for m in M.parse_wlr_randr(WLR)}
    assert set(mons) == {"DP-2", "HDMI-A-1"}
    assert (mons["DP-2"].width, mons["DP-2"].height) == (3840, 2160)
    assert mons["HDMI-A-1"].x == 3840
    assert mons["DP-2"].refresh == pytest.approx(59.997)


def test_wlr_randr_splits_make_from_model():
    mons = {m.name: m for m in M.parse_wlr_randr(WLR)}
    assert "Dell" in mons["DP-2"].make
    assert "U2720Q" in mons["DP-2"].model


def test_wlr_randr_labels_positions():
    labelled = M.label_positions(M.parse_wlr_randr(WLR))
    assert [(m.name, m.position) for m in labelled] == [("DP-2", "Left"), ("HDMI-A-1", "Right")]


def test_wlr_randr_handles_description_instead_of_make_model():
    """Some wlroots versions report a single `description` string."""
    doc = json.dumps([{"name": "eDP-1", "enabled": True, "description": "BOE 0x095F",
                       "current_mode": {"width": 1920, "height": 1080, "refresh": 60.0}}])
    mons = M.parse_wlr_randr(doc)
    assert mons and mons[0].name == "eDP-1"


# ------------------------------------------------------------------ robustness

@pytest.mark.parametrize("parser", [M.parse_wlr_randr, M.parse_xrandr])
def test_parsers_return_nothing_for_junk(parser):
    """A tool that exists but prints something unexpected must not be fatal."""
    for junk in ("", "not a monitor listing", "\x00\x01", "null", "{}", "[]"):
        assert parser(junk) == []


def test_wlr_randr_survives_an_object_instead_of_a_list():
    assert M.parse_wlr_randr('{"name": "DP-1"}') == []


def test_providers_are_tried_in_order_with_cosmic_first():
    names = [p[0] for p in M.PROVIDERS]
    assert names == ["cosmic-randr", "wlr-randr", "xrandr"], (
        "COSMIC is the most precise source and must be tried first"
    )
    for _tool, args, parser in M.PROVIDERS:
        assert isinstance(args, tuple) and args, "each provider needs its argv"
        assert callable(parser)


def test_falls_back_to_drm_when_no_tool_is_installed(monkeypatch):
    """With nothing on PATH, discovery must still return something rather than crash."""
    monkeypatch.setattr(M.shutil, "which", lambda _name: None)
    drm = [M.Monitor(name="DP-1", enabled=True), M.Monitor(name="HDMI-A-1", enabled=True)]
    monkeypatch.setattr(M, "_drm_fallback", lambda: drm)
    assert {m.name for m in M.list_monitors()} == {"DP-1", "HDMI-A-1"}


def test_a_broken_tool_is_skipped_for_the_next_one(monkeypatch):
    """If cosmic-randr exists but prints garbage, xrandr's answer is still used."""
    def fake_which(name):
        return f"/usr/bin/{name}" if name in ("cosmic-randr", "xrandr") else None

    def fake_run(argv):
        if argv[0].endswith("cosmic-randr"):
            return "unexpected output"
        return XRANDR

    monkeypatch.setattr(M.shutil, "which", fake_which)
    monkeypatch.setattr(M, "_run", fake_run)
    named = [m.name for m in M.list_monitors()]
    assert named == ["DP-1", "HDMI-A-1"]
