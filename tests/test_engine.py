import json

import pytest
from conftest import write_project

from wallpaper_picker import config, engine, shim
from wallpaper_picker.monitors import Monitor, label_positions, parse_cosmic_randr
from wallpaper_picker.workshop import Source, scan

RANDR = """\x1b[1mHDMI-A-1\x1b[0m \x1b[1;32m(enabled)\x1b[0m\x1b[1;33m
  Make: \x1b[0mLG Electronics\x1b[1;33m
  Model: \x1b[0mLG FULL HD\x1b[1;33m
  Position: \x1b[0m2133,0
  Scale: 90%
  Modes:
    1920x1080 @  74.973 Hz
    1920x1080 @  60.000 Hz (current) (preferred)
DP-1 (enabled)
  Make: Acer Technologies
  Model: XZ242Q
  Position: 0,0
  Scale: 90%
  Modes:
    1920x1080 @ 120.003 Hz (current) (preferred)
DP-2 (disabled)
  Make: Other
"""


@pytest.fixture
def mons():
    return label_positions(parse_cosmic_randr(RANDR))


@pytest.fixture
def lib(tmp_path):
    ws = tmp_path / "ws"
    write_project(ws / "1000", {"title": "Video", "type": "video", "file": "v.mp4",
                                "general": {"properties": {"schemecolor": {"type": "color", "value": "0 0 0"}}}})
    write_project(ws / "2000", {"title": "Scene", "type": "scene", "file": "scene.json", "general": {"properties": {
        "rain": {"type": "bool", "value": True, "text": "Rain"},
        "speed": {"type": "slider", "value": 0.5, "min": 0, "max": 1},
        "tint": {"type": "color", "value": "1 1 1"},
        "mode": {"type": "combo", "value": "1", "options": [{"label": "A", "value": "1"}, {"label": "B", "value": "2"}]},
        "name": {"type": "textinput", "value": "x"},
    }}})
    write_project(ws / "3000", {"title": "Web", "type": "web", "file": "index.html", "general": {"properties": {
        "rain": {"type": "bool", "value": False}}}})
    return {w.id: w for w in scan([Source(ws, "workshop")])}


def target(w, _lib):
    return w.id


def test_monitor_parsing(mons):
    assert [m.name for m in mons] == ["DP-1", "HDMI-A-1"]  # disabled dropped, sorted by x
    left, right = mons
    assert left.position == "Left" and left.pretty == "Acer XZ242Q" and left.refresh == pytest.approx(120.003)
    assert right.position == "Right" and right.pretty == "LG FULL HD" and right.scale == pytest.approx(0.9)
    assert right.x == 2133 and right.width == 1920


def test_resolve_target(mons):
    assert config.resolve_target("left", mons) == ["DP-1"]
    assert config.resolve_target("Right", mons) == ["HDMI-A-1"]
    assert config.resolve_target("HDMI-A-1", mons) == ["HDMI-A-1"]
    assert config.resolve_target("both", mons) == ["DP-1", "HDMI-A-1"]
    assert config.resolve_target("span", mons) == [config.SPAN]
    with pytest.raises(ValueError):
        config.resolve_target("middle", mons)


def argv_of(specs):
    return {s.key: s.argv for s in specs}


def test_single_monitor_command(mons, lib):
    cfg = config.Config()
    cfg.assign(["HDMI-A-1"], "1000", {"fps": 30, "scaling": "fill"}, mons)
    specs, problems = engine.build_plan(cfg, mons, lib, "/opt/lwe", target_fn=target)
    assert problems == []
    assert argv_of(specs) == {"HDMI-A-1": [
        "/opt/lwe", "--fps", "30", "--silent", "--no-audio-processing", "--no-fullscreen-pause",
        "--layer", "background",
        "--screen-root", "HDMI-A-1", "--bg", "1000", "--scaling", "fill", "--clamp", "clamp"]}


def test_both_same_wallpaper_one_process(mons, lib):
    cfg = config.Config()
    cfg.assign(["DP-1", "HDMI-A-1"], "2000", {"fps": 60}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)
    assert len(specs) == 1
    a = specs[0].argv
    assert a.count("--screen-root") == 2 and a[a.index("--fps") + 1] == "60"
    # dot-less colour default is passed as floats so the engine doesn't read it as 0..255
    assert "tint=1.000000 1.000000 1.000000" in a


def test_span_command(mons, lib):
    cfg = config.Config()
    cfg.assign([config.SPAN], "2000", {"scaling": "stretch"}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)
    a = specs[0].argv
    i = a.index("--screen-span")
    assert a[i + 1] == "DP-1,HDMI-A-1" and a[i + 2:i + 6] == ["--bg", "2000", "--scaling", "stretch"]
    assert "--screen-root" not in a


def test_span_with_one_monitor_left(mons, lib):
    cfg = config.Config()
    cfg.assign([config.SPAN], "2000", {}, mons)
    specs, _ = engine.build_plan(cfg, mons[:1], lib, "lwe", target_fn=target)
    assert specs[0].argv[specs[0].argv.index("--screen-root") + 1] == "DP-1"


def test_leaving_span_keeps_other_monitor(mons, lib):
    cfg = config.Config()
    cfg.assign([config.SPAN], "2000", {}, mons)
    cfg.assign(["DP-1"], "1000", {}, mons)
    assert not cfg.span_active
    assert cfg.slots["DP-1"].wallpaper == "1000" and cfg.slots["HDMI-A-1"].wallpaper == "2000"


def test_properties_and_toggles(mons, lib):
    cfg = config.Config()
    cfg.assign(["DP-1"], "2000", {"mouse": False, "parallax": False, "particles": False, "fps": 24}, mons)
    cfg.set_overrides("DP-1", "2000", {"rain": False, "speed": 0.25, "tint": "0 0.5 1", "mode": "2",
                                       "name": "hello world", "bogus": 1})
    specs, _ = engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)
    a = specs[0].argv
    for flag in ("--disable-mouse", "--disable-parallax", "--disable-particles"):
        assert flag in a
    props = [a[i + 1] for i, x in enumerate(a) if x == "--set-property"]
    assert props == ["mode=2", "name=hello world", "rain=false", "speed=0.25", "tint=0.000000 0.500000 1.000000"]


def test_conflicting_properties_split_processes(mons, lib):
    cfg = config.Config()
    cfg.assign(["DP-1"], "2000", {}, mons)
    cfg.assign(["HDMI-A-1"], "3000", {}, mons)
    cfg.set_overrides("DP-1", "2000", {"rain": False})  # web wallpaper also has "rain" (default False)
    units, _ = engine.build_units(cfg, mons, lib, target)
    a, b = units
    assert engine.compatible(a, b)  # rain=false agrees with the web default
    cfg.set_overrides("HDMI-A-1", "3000", {"rain": True})
    a, b = engine.build_units(cfg, mons, lib, target)[0]
    assert not engine.compatible(a, b)
    # same wallpaper, different per-monitor settings -> two processes
    cfg = config.Config()
    cfg.assign(["DP-1", "HDMI-A-1"], "2000", {}, mons)
    cfg.set_overrides("HDMI-A-1", "2000", {"speed": 0.9})
    assert len(engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)[0]) == 2
    # different fps -> two processes
    cfg = config.Config()
    cfg.assign(["DP-1"], "1000", {"fps": 30}, mons)
    cfg.assign(["HDMI-A-1"], "1000", {"fps": 60}, mons)
    assert len(engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)[0]) == 2


def test_video_and_scene_share_despite_schemecolor(mons, lib):
    cfg = config.Config()
    cfg.assign(["DP-1"], "1000", {}, mons)
    cfg.assign(["HDMI-A-1"], "2000", {}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)
    assert len(specs) == 1 and specs[0].outputs == ["DP-1", "HDMI-A-1"]


def test_audio_always_off(mons, lib):
    cfg = config.Config()
    cfg.assign(["DP-1", "HDMI-A-1"], "1000", {}, mons)
    for s in engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)[0]:
        assert "--silent" in s.argv and "--no-audio-processing" in s.argv
    assert engine.ensure_silent(["lwe", "--bg", "1"])[1:3] == ["--no-audio-processing", "--silent"]
    with pytest.raises(ValueError):
        engine.ensure_silent(["lwe", "--volume", "50"])


def test_missing_wallpaper_and_disabled(mons, lib):
    cfg = config.Config()
    cfg.assign(["DP-1"], "999", {}, mons)
    specs, problems = engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)
    assert specs == [] and "not installed" in problems[0]
    cfg.assign(["DP-1"], "1000", {}, mons)
    cfg.enabled = False
    assert engine.build_plan(cfg, mons, lib, "lwe", target_fn=target) == ([], [])


def test_config_round_trip(tmp_path, mons):
    cfg = config.Config()
    cfg.assign(["HDMI-A-1"], "2584613496", {"fps": 60, "scaling": "fit", "mouse": False}, mons)
    cfg.assign([config.SPAN], "2000", {"particles": False}, mons)
    cfg.set_overrides("HDMI-A-1", "2584613496", {"rain": False, "speed": 0.3, "tint": "1 0 0"})
    cfg.options.pause_scope = "monitor"
    p = tmp_path / "c" / "config.json"
    config.save(cfg, p)
    back = config.load(p)
    assert back.to_dict() == cfg.to_dict()
    assert back.slots[config.SPAN].outputs == ["DP-1", "HDMI-A-1"] and back.span_active
    assert back.overrides("HDMI-A-1", "2584613496")["speed"] == 0.3
    cfg.set_overrides("HDMI-A-1", "2584613496", {})
    assert "HDMI-A-1" not in cfg.properties


def test_config_tolerates_garbage(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"slots": {"DP-1": {"fps": "abc", "scaling": "weird", "wallpaper": ""}},
                             "properties": {"DP-1": {"x": "notadict"}}, "options": 5}))
    c = config.load(p)
    assert c.slots["DP-1"].fps == 30 and c.slots["DP-1"].scaling == "fill" and c.slots["DP-1"].wallpaper is None
    assert c.properties == {"DP-1": {}}
    p.write_text("{broken")
    assert config.load(p).slots == {}


def test_shim_for_preset_and_typeless(tmp_path, xdg):
    ws = tmp_path / "ws2"
    write_project(ws / "100", {"title": "Base", "type": "scene", "file": "scene.json", "general": {"properties": {
        "speed": {"type": "slider", "value": 1, "min": 0, "max": 5}}}}, extra={"scene.pkg": b"PKG", "files/a.gif": b"A"})
    write_project(ws / "200", {"title": "Preset", "dependency": "100", "preset": {"speed": 3}},
                  extra={"files/b.gif": b"B"})
    write_project(ws / "300", {"title": "Old", "file": "scene.json"}, extra={"scene.json": "{}"})
    lib = {w.id: w for w in scan([Source(ws, "workshop")])}
    t = shim.engine_target(lib["200"], lib, tmp_path / "shims")
    d = tmp_path / "shims" / "200"
    assert t == str(d)
    pj = json.loads((d / "project.json").read_text())
    assert pj["type"] == "scene" and pj["file"] == "scene.json" and pj["title"] == "Preset"
    assert pj["general"]["properties"]["speed"]["value"] == 3
    assert (d / "scene.pkg").read_bytes() == b"PKG"
    assert (d / "files" / "a.gif").read_bytes() == b"A" and (d / "files" / "b.gif").read_bytes() == b"B"
    assert shim.engine_target(lib["200"], lib, tmp_path / "shims") == t  # cached, stable
    t3 = shim.engine_target(lib["300"], lib, tmp_path / "shims")
    assert json.loads((tmp_path / "shims" / "300" / "project.json").read_text())["type"] == "scene"
    assert t3.endswith("/300")
    # a directly loadable item outside the engine's search dirs is passed by path
    write_project(ws / "400", {"title": "Plain", "type": "video", "file": "v.mp4"})
    lib = {w.id: w for w in scan([Source(ws, "workshop")])}
    assert shim.engine_target(lib["400"], lib, tmp_path / "shims") == str(ws / "400")


def test_snapshot_option(mons, lib):
    cfg = config.Config()
    cfg.assign(["HDMI-A-1"], "1000", {"fps": 30}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "lwe", target_fn=target, snapshot_dir="/run/x")
    a = specs[0].argv
    assert a[a.index("--screenshot") + 1] == "/run/x/snapshot-HDMI-A-1.png"
    assert "--screenshot" not in engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)[0][0].argv


def test_web_and_isolated_slots_get_own_process(mons, lib):
    cfg = config.Config()
    cfg.assign(["DP-1"], "3000", {}, mons)  # web
    cfg.assign(["HDMI-A-1"], "1000", {}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)
    assert sorted(s.key for s in specs) == ["DP-1", "HDMI-A-1"]
    cfg.assign(["DP-1"], "1000", {}, mons)
    assert len(engine.build_plan(cfg, mons, lib, "lwe", target_fn=target)[0]) == 1
    specs, _ = engine.build_plan(cfg, mons, lib, "lwe", target_fn=target, isolate={"DP-1"})
    assert sorted(s.key for s in specs) == ["DP-1", "HDMI-A-1"]


# --------------------------------------------------------------- compatibility settings


def test_clamp_defaults_to_clamp(mons, lib):
    cfg = config.Config()
    cfg.assign(["HDMI-A-1"], "1000", {}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "/opt/lwe", target_fn=target)
    argv = argv_of(specs)["HDMI-A-1"]
    assert argv[argv.index("--clamp") + 1] == "clamp"


def test_clamp_choice_reaches_the_engine(mons, lib):
    cfg = config.Config()
    cfg.assign(["HDMI-A-1"], "1000", {"clamp": "border"}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "/opt/lwe", target_fn=target)
    argv = argv_of(specs)["HDMI-A-1"]
    assert argv[argv.index("--clamp") + 1] == "border"


def test_different_clamps_do_not_share_one_process(mons, lib):
    """--clamp applies per output, so mismatched clamps must be separate processes."""
    cfg = config.Config()
    cfg.assign(["DP-1"], "1000", {"clamp": "clamp"}, mons)
    cfg.assign(["HDMI-A-1"], "1000", {"clamp": "repeat"}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "/opt/lwe", target_fn=target)
    assert len(specs) == 2


def test_same_clamp_still_shares_one_process(mons, lib):
    cfg = config.Config()
    cfg.assign(["DP-1"], "1000", {"clamp": "border"}, mons)
    cfg.assign(["HDMI-A-1"], "1000", {"clamp": "border"}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "/opt/lwe", target_fn=target)
    assert len(specs) == 1


def test_fps_setting_is_per_monitor(mons, lib):
    cfg = config.Config()
    cfg.assign(["HDMI-A-1"], "1000", {"fps": 15}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "/opt/lwe", target_fn=target)
    argv = argv_of(specs)["HDMI-A-1"]
    assert argv[argv.index("--fps") + 1] == "15"


def test_disabling_effects_adds_the_flags(mons, lib):
    cfg = config.Config()
    cfg.assign(["HDMI-A-1"], "1000", {"particles": False, "parallax": False, "mouse": False}, mons)
    specs, _ = engine.build_plan(cfg, mons, lib, "/opt/lwe", target_fn=target)
    argv = argv_of(specs)["HDMI-A-1"]
    assert "--disable-particles" in argv
    assert "--disable-parallax" in argv
    assert "--disable-mouse" in argv
