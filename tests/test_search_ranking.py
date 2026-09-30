"""Search must return what was asked for.

Steam's QueryFiles text search is loose: it matches a term anywhere in an item (title,
description or tags) and then ranks by popularity, so searching "rain" returned
"Lofi Cafe" third. These tests pin the local re-ranking, the filetype mapping and the tag
vocabulary -- all three of which were wrong in ways that quietly made browsing useless.
"""

from __future__ import annotations

import json

import pytest

from wallpaper_picker import steam


# ------------------------------------------------------------------ relevance

def test_exact_title_ranks_best_then_prefix_then_word():
    assert steam.relevance("rain", "rain") == 0
    assert steam.relevance("Rainy Day", "rain") == 1          # starts with the term
    assert steam.relevance("Summer Rain", "rain") == 2        # starts a later word
    assert steam.relevance("Brainstorm", "rain") == 3         # mid-word only


def test_a_title_without_the_term_is_flagged_as_no_match():
    assert steam.relevance("Lofi Cafe", "rain") == -1
    assert steam.relevance("Cozy Cabin", "rain") == -1


def test_matching_is_case_insensitive():
    assert steam.relevance("RAINY DAY", "rain") == 1
    assert steam.relevance("rainy day", "RAIN") == 1


def test_rank_puts_title_matches_first_and_non_matches_last():
    """The bug that shipped: -1 sorted FIRST, so unrelated items led the results."""
    items = [
        steam.Item(id="1", title="Lofi Cafe"),
        steam.Item(id="2", title="Summer Rain"),
        steam.Item(id="3", title="Rainy Day"),
        steam.Item(id="4", title="Jinx with gemstone"),
        steam.Item(id="5", title="rain"),
    ]
    ordered = [i.title for i in steam.rank(items, "rain")]
    assert ordered == ["rain", "Rainy Day", "Summer Rain", "Lofi Cafe", "Jinx with gemstone"]


def test_rank_keeps_steams_order_within_a_band():
    items = [steam.Item(id="1", title="Rainy Day"),
             steam.Item(id="2", title="Rainy Night"),
             steam.Item(id="3", title="Rain All Day")]
    assert [i.title for i in steam.rank(items, "rain")] == \
        ["Rainy Day", "Rainy Night", "Rain All Day"]


def test_rank_is_a_no_op_without_a_search_term():
    items = [steam.Item(id="1", title="b"), steam.Item(id="2", title="a")]
    assert [i.title for i in steam.rank(items, "")] == ["b", "a"]
    assert [i.title for i in steam.rank(items, "   ")] == ["b", "a"]


# ------------------------------------------------------------------ filetype

def test_filetype_values_are_the_item_type_plus_one():
    """Measured live: querying filetype 1 returns items with file_type 2, and so on."""
    assert steam.FILETYPES["all"] == 0          # 0 means "do not filter"
    assert steam.FILETYPES["scene"] == 1
    assert steam.FILETYPES["video"] == 2
    assert steam.FILETYPES["application"] == 3
    assert steam.FILETYPES["web"] == 4


def test_named_types_are_not_all_the_same_filter():
    named = [v for k, v in steam.FILETYPES.items() if k != "all"]
    assert len(set(named)) == len(named), "each type needs its own filter value"
    assert 0 not in named, "a named type must not silently mean 'all'"


def test_build_query_params_sends_the_chosen_type():
    p = steam.build_query_params("key", filetype="video")
    assert json.loads(p["input_json"])["filetype"] == steam.FILETYPES["video"]


def test_build_query_params_rejects_an_unknown_type():
    with pytest.raises(steam.SteamError):
        steam.build_query_params("key", filetype="hologram")


# ------------------------------------------------------------------ tags

def test_suggested_tags_are_grouped_and_non_empty():
    assert set(steam.TAG_GROUPS) == {"Type", "Theme", "Age", "Resolution"}
    for group, tags in steam.TAG_GROUPS.items():
        assert tags, f"the {group} group is empty"
    assert steam.TAG_SUGGESTIONS == [t for g in steam.TAG_GROUPS.values() for t in g]


def test_the_tag_that_matched_nothing_is_gone():
    """'Dark' returned 0 results from the API; it was a guess, not a real tag."""
    assert "Dark" not in steam.TAG_SUGGESTIONS
    assert "Anime" in steam.TAG_SUGGESTIONS
    assert "Scene" in steam.TAG_SUGGESTIONS
    assert "3840 x 2160" in steam.TAG_SUGGESTIONS


def test_a_search_switches_to_the_text_query_type():
    p = steam.build_query_params("key", search="rain")
    inner = json.loads(p["input_json"])
    assert inner["query_type"] == steam.SORTS["text"]
    assert inner["search_text"] == "rain"


def test_no_search_keeps_the_chosen_sort():
    p = steam.build_query_params("key", sort="popular")
    inner = json.loads(p["input_json"])
    assert inner["query_type"] == steam.SORTS["popular"]
    assert "search_text" not in inner


# ------------------------------------------------------------------ search_items

def test_search_items_collects_pages_then_ranks(monkeypatch):
    """The best title match is often not on Steam's first page, hence multiple pages."""
    pages = {
        1: [steam.Item(id="1", title="Lofi Cafe"), steam.Item(id="2", title="Rainy Day")],
        # a SHORT page means Steam has nothing more, so the loop must stop here
        2: [steam.Item(id="3", title="rain")],
    }
    calls = []

    def fake_query(_key, **kw):
        calls.append(kw["page"])
        return pages.get(kw["page"], []), 99

    monkeypatch.setattr(steam, "PAGE_SIZE", 2)
    items, total = steam.search_items("key", "rain", pages=3, _query=fake_query)

    assert total == 99
    assert calls == [1, 2], "should stop once a page comes back short"
    assert [i.title for i in items] == ["rain", "Rainy Day", "Lofi Cafe"]


def test_search_items_deduplicates_across_pages(monkeypatch):
    def fake_query(_key, **kw):
        return [steam.Item(id="7", title="rain"), steam.Item(id="7", title="rain")], 5

    monkeypatch.setattr(steam, "PAGE_SIZE", 10)
    items, _ = steam.search_items("key", "rain", _query=fake_query)
    assert len(items) == 1


# ------------------------------------------------------------------ game names

def test_stopwords_are_dropped_from_a_search():
    """'world of warcraft' must search for 'world' and 'warcraft', not 'of'."""
    assert steam.search_words("World of Warcraft") == ["world", "warcraft"]
    assert steam.search_words("The Legend of Zelda") == ["legend", "zelda"]
    assert steam.search_words("Cyberpunk 2077") == ["cyberpunk", "2077"]
    assert steam.search_words("rain") == ["rain"]


def test_search_words_deduplicates_and_ignores_punctuation():
    assert steam.search_words("Elden  Ring") == ["elden", "ring"]
    assert steam.search_words("Hollow-Knight!") == ["hollow", "knight"]
    assert steam.search_words("  ") == []


def test_a_title_containing_every_word_beats_one_containing_some():
    """This is what makes a game name find that game's wallpapers."""
    both = steam.relevance("World of Warcraft Ashenvale", "World of Warcraft")
    some = steam.relevance("Mystical Landscape 4k", "World of Warcraft")
    assert both >= 0, "a real WoW wallpaper must match"
    assert some == -1, "an unrelated title must not match"
    assert both < 100


def test_a_wallpaper_that_matches_only_part_of_the_name_still_ranks():
    """'League of Legends' wallpapers often say just 'League of Legends' or 'Arcane'."""
    part = steam.relevance("Akali (League of Legends; 4k)", "League of Legends")
    assert part >= 0


def test_game_name_search_ranks_the_game_first():
    """The bug the user hit: 'World of Warcraft' returned no Warcraft wallpaper at all."""
    items = [
        steam.Item(id="1", title="Dome 4k {Artwork by WLOP}"),
        steam.Item(id="2", title="Mystical Landscape 4k"),
        steam.Item(id="3", title="Xal'atath - World of Warcraft [TANTAN]"),
        steam.Item(id="4", title="KonoSuba: God's Blessing on This Wonderful World!"),
    ]
    ordered = [i.title for i in steam.rank(items, "World of Warcraft")]
    assert ordered[0] == "Xal'atath - World of Warcraft [TANTAN]"
    assert ordered[-1] == "Mystical Landscape 4k"


def test_multi_word_search_queries_the_words_as_well_as_the_phrase(monkeypatch):
    """Steam treats the phrase loosely, so the words must be searched too."""
    asked: list[str] = []

    def fake_query(_key, **kw):
        asked.append(kw["search"])
        if kw["search"] == "warcraft":
            return [steam.Item(id="9", title="World of Warcraft")], 4653
        return [steam.Item(id="1", title="Dome 4k")], 26347

    items, total = steam.search_items("key", "World of Warcraft", _query=fake_query)

    assert asked[0] == "World of Warcraft", "the phrase is still searched first"
    assert "warcraft" in asked, "the distinctive word must be searched too"
    assert [i.title for i in items][0] == "World of Warcraft"
    # the phrase's count includes 26k items that do not contain the words at all
    assert total == 4653
