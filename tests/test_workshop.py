import json

from conftest import write_project

from wallpaper_picker import workshop
from wallpaper_picker.workshop import Source, eval_condition, load_project, scan


def test_bom_is_accepted(tmp_path):
    f = write_project(tmp_path / "111", {"title": "Hello", "type": "video", "file": "a.mp4", "preview": "preview.jpg"},
                      bom=True, extra={"preview.jpg": b"x"})
    wp = load_project(f)
    assert wp.title == "Hello" and wp.type == "video" and wp.error is None
    assert wp.preview == f / "preview.jpg"
    assert wp.directly_loadable


def test_type_is_case_insensitive(tmp_path):
    wp = load_project(write_project(tmp_path / "1", {"title": "T", "type": "Scene", "file": "scene.json"}))
    assert wp.type == "scene" and wp.raw_type == "scene" and wp.directly_loadable


def test_missing_fields(tmp_path):
    wp = load_project(write_project(tmp_path / "222", {}, extra={"preview.gif": b"GIF89a"}))
    assert wp.title == "222"
    assert wp.type == "unknown"
    assert wp.error
    assert wp.preview.name == "preview.gif"  # found without a "preview" field
    assert not wp.playable


def test_type_inferred_from_file(tmp_path):
    wp = load_project(write_project(tmp_path / "b", {"title": "Old built-in", "file": "x.json"}))
    assert wp.type == "scene" and wp.raw_type is None
    assert not wp.directly_loadable  # needs a shim with "type" added
    assert wp.playable


def test_exe_is_application(tmp_path):
    wp = load_project(write_project(tmp_path / "s", {"title": "Sheep", "file": "sheep.exe"}))
    assert wp.type == "application" and not wp.playable


def test_broken_json(tmp_path):
    wp = load_project(write_project(tmp_path / "333", "{not json"))
    assert wp.type == "unknown" and "Unreadable" in wp.error


def test_unknown_type_preset_resolves_to_dependency(tmp_path):
    ws = tmp_path / "ws"
    write_project(ws / "100", {"title": "Base", "type": "scene", "file": "scene.json",
                               "general": {"properties": {"speed": {"type": "slider", "text": "Speed",
                                                                    "value": 1, "min": 0, "max": 5}}}})
    write_project(ws / "200", {"title": "Preset", "dependency": "100", "preset": {"speed": 3}})
    write_project(ws / "300", {"title": "Orphan", "dependency": "999", "preset": {}})
    wps = {w.id: w for w in scan([Source(ws, "workshop")])}
    assert wps["200"].type == "scene" and wps["200"].is_preset and wps["200"].playable
    assert wps["200"].prop("speed").default == 3.0
    assert wps["100"].prop("speed").default == 1.0  # base untouched
    assert not wps["300"].playable and "999" in wps["300"].error


def test_properties_parsed_and_sorted(tmp_path):
    props = {
        "b": {"type": "bool", "text": "B", "value": True, "order": 2},
        "c": {"type": "color", "text": "<b>Colour</b>", "value": "1 0 0", "order": 1},
        "k": {"type": "combo", "text": "K", "value": 2, "order": 3,
              "options": [{"label": "One", "value": 1}, {"label": "Two", "value": 2}]},
        "g": {"type": "group", "text": "Group", "order": 0},
        "t": {"type": "textinput", "text": "T", "value": "hi", "order": 4},
        "x": "garbage",
        "n": {"text": "no type"},
    }
    wp = load_project(write_project(tmp_path / "p", {"title": "P", "type": "scene", "file": "s.json",
                                                    "general": {"properties": props}}))
    assert [p.key for p in wp.properties] == ["g", "c", "b", "k", "t"]
    c = wp.prop("c")
    assert c.label == "Colour" and c.default == "1.000000 0.000000 0.000000"
    k = wp.prop("k")
    assert k.default == "2" and k.options == [("One", "1"), ("Two", "2")]


def test_property_cli_values(tmp_path):
    p = workshop.parse_property("s", {"type": "slider", "value": 0.5, "min": 0, "max": 1})
    assert p.cli_value(0.25) == "0.25"
    assert p.cli_value(7) == "1"  # clamped to max
    assert workshop.parse_property("i", {"type": "slider", "value": 3, "min": 0, "max": 10}).cli_value(4.0) == "4"
    b = workshop.parse_property("b", {"type": "bool", "value": False})
    assert b.cli_value(True) == "true" and b.cli_value("0") == "false"
    c = workshop.parse_property("c", {"type": "color", "value": "1 1 1"})
    assert c.cli_value("0 0.5 1") == "0.000000 0.500000 1.000000"
    assert c.cli_value("255 0 0") == "1.000000 0.000000 0.000000"
    assert c.cli_value("#00ff00") == "0.000000 1.000000 0.000000"
    k = workshop.parse_property("k", {"type": "combo", "value": 1, "options": [{"label": "a", "value": 1}]})
    assert k.cli_value(1) == "1"


def test_color_needs_fix():
    assert workshop.color_needs_fix("1 1 1")
    assert workshop.color_needs_fix("0 1 1")
    assert not workshop.color_needs_fix("0.5 1 1")
    assert not workshop.color_needs_fix("255 128 0")
    assert not workshop.color_needs_fix("#ffffff")
    assert not workshop.color_needs_fix(None)


def test_conditions():
    v = {"rain": True, "mode": "2", "n": 3.0, "text": "abc"}
    assert eval_condition("", v)
    assert eval_condition("rain.value == true", v)
    assert not eval_condition("rain.value == false", v)
    assert eval_condition("mode.value == 2", v)
    assert eval_condition("mode.value === '2'", v)
    assert not eval_condition("!rain.value", v)
    assert eval_condition("rain.value && n.value > 2", v)
    assert eval_condition("rain.value == false || (n.value >= 3 && mode.value != 1)", v)
    assert eval_condition("this is @@ garbage", v)  # unknown syntax keeps the row visible


def test_library_folders_vdf(tmp_path):
    vdf = '''"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"/home/u/.steam/debian-installation"\n\t}\n
    \t"1"\n\t{\n\t\t"path"\t\t"/mnt/games/SteamLibrary"\n\t\t"apps"\t{ "431960" "1" }\n\t}\n}'''
    assert workshop.parse_library_folders(vdf) == ["/home/u/.steam/debian-installation", "/mnt/games/SteamLibrary"]


def test_workshop_discovery_across_libraries(tmp_path):
    home = tmp_path / "home"
    main = home / ".steam" / "steam"
    extra = tmp_path / "games" / "SteamLibrary"
    (main / "steamapps" / "workshop" / "content" / "431960" / "1").mkdir(parents=True)
    (extra / "steamapps" / "workshop" / "content" / "431960" / "2").mkdir(parents=True)
    (extra / "steamapps" / "common" / "wallpaper_engine" / "projects" / "defaultprojects").mkdir(parents=True)
    (extra / "steamapps" / "common" / "wallpaper_engine" / "assets").mkdir(parents=True)
    (main / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "0" {{ "path" "{main}" }} "1" {{ "path" "{extra}" }} }}')
    srcs = workshop.discover_sources(home)
    kinds = [(s.kind, s.path) for s in srcs]
    assert ("workshop", (main / "steamapps/workshop/content/431960").resolve()) in \
        [(k, p.resolve()) for k, p in kinds]
    assert ("workshop", (extra / "steamapps/workshop/content/431960").resolve()) in \
        [(k, p.resolve()) for k, p in kinds]
    assert any(k == "built-in" for k, _ in kinds)
    assert workshop.find_assets_dir(home).resolve() == (extra / "steamapps/common/wallpaper_engine/assets").resolve()
    # the same library listed twice (symlink) is only returned once
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "Steam").symlink_to(main)
    assert len([s for s in workshop.discover_sources(home) if s.kind == "workshop"]) == 2


def test_find_wallpaper(tmp_path):
    ws = tmp_path / "ws"
    write_project(ws / "10", {"title": "Synth City", "type": "video", "file": "a.mp4"})
    write_project(ws / "20", {"title": "Synth Wave", "type": "video", "file": "a.mp4"})
    wps = scan([Source(ws, "workshop")])
    assert workshop.find_wallpaper(wps, "10").title == "Synth City"
    assert workshop.find_wallpaper(wps, "synth city").id == "10"
    assert workshop.find_wallpaper(wps, "wave").id == "20"
    assert workshop.find_wallpaper(wps, "synth") is None  # ambiguous


def test_real_library_smoke():
    """Parses whatever is installed on this machine without raising."""
    wps = scan()
    for w in wps:
        json.dumps({"id": w.id, "title": w.title, "type": w.type})
