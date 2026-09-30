"""Fuzzy search + sorting, and the property headings the panel groups by."""

from __future__ import annotations

import pytest

from wallpaper_picker import workshop


def wp(title, **kw):
    fields = {"id": kw.pop("id", "1"), "path": None, "source": "workshop",
              "title": title, "type": "scene"}
    fields.update(kw)
    return workshop.Wallpaper(**fields)


# ------------------------------------------------------------------ fuzzy search

def test_substring_beats_scattered_letters():
    good = workshop.fuzzy_score("Firewatch - The Tower", "firewatch")
    poor = workshop.fuzzy_score("Firewatch - The Tower", "ftwr")
    assert good > poor > 0


def test_multi_term_must_all_match():
    assert workshop.fuzzy_score("[Triple FHD] Firewatch - The Tower", "fw tower") > 0
    assert workshop.fuzzy_score("[Triple FHD] Firewatch - The Tower", "firewatch banana") == 0


def test_word_start_scores_higher_than_mid_word():
    assert workshop.fuzzy_score("Dark Winter Path", "dark") > workshop.fuzzy_score("Dark Winter Path", "ark")


def test_case_insensitive_and_empty_query():
    assert workshop.fuzzy_score("Bongo Space Cat HD", "BONGO CAT") > 0
    assert workshop.fuzzy_score("anything", "") == 1      # empty query matches everything


def test_subsequence_fallback_finds_scattered_letters():
    assert workshop.fuzzy_score("Binary Code 4K", "bcd") > 0
    assert workshop.fuzzy_score("Binary Code 4K", "zzz") == 0


def test_partial_word_terms():
    """"bongo cat" should find "Bongo Space Cat"."""
    assert workshop.fuzzy_score("Bongo Space Cat HD", "bongo cat") > 0


# ------------------------------------------------------------------ sorting

def test_sort_by_name_is_case_insensitive():
    items = [wp("banana"), wp("Apple"), wp("cherry")]
    assert [w.title for w in workshop.sort_wallpapers(items, "name")] == ["Apple", "banana", "cherry"]


def test_sort_by_properties_puts_the_richest_first():
    from wallpaper_picker.workshop import Property
    few = wp("few", id="a")
    many = wp("many", id="b")
    many.properties = [Property(key=f"k{i}", type="bool", label="x") for i in range(5)]
    assert [w.id for w in workshop.sort_wallpapers([few, many], "properties")] == ["b", "a"]


def test_sort_by_newest_uses_mtime():
    old = wp("old", id="o", mtime=1.0)
    new = wp("new", id="n", mtime=9.0)
    assert [w.id for w in workshop.sort_wallpapers([old, new], "newest")] == ["n", "o"]


def test_unknown_sort_falls_back_to_name():
    items = [wp("b"), wp("a")]
    assert [w.title for w in workshop.sort_wallpapers(items, "nonsense")] == ["a", "b"]


def test_sorts_constant_lists_every_choice():
    assert set(workshop.SORTS) == {"name", "newest", "properties", "type", "author"}


# ------------------------------------------------------------------ headings

def test_heading_level_read_from_the_separator_html():
    assert workshop._heading_level("<h3><b><i>X</i></b></h3>") == 3
    assert workshop._heading_level("<h4>Y</h4>") == 4
    assert workshop._heading_level("just a label") == 0
    assert workshop._heading_level(None) == 0


def test_separator_becomes_a_heading_not_an_editable_property():
    prop = workshop.parse_property("separator_effects", {
        "type": "text", "text": "<h3><b><i>EFFECTS</i></b></h3>", "order": 10,
    })
    assert prop is not None
    assert prop.is_heading is True
    assert prop.editable is False
    assert prop.label == "EFFECTS"


def test_a_normal_property_is_not_a_heading():
    prop = workshop.parse_property("bloom", {"type": "slider", "text": "Bloom", "order": 11})
    assert prop is not None
    assert prop.is_heading is False
    assert prop.editable is True
