"""Tests for the Steam Workshop client (browse params, parsing, steamcmd)."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

from wallpaper_picker import steam


# --------------------------------------------------------------------------- parsing

RAW = {
    "publishedfileid": "3122339805",
    "title": "Gengar",
    "creator": "76561198000000000",
    "preview_url": "https://images.steamusercontent.com/ugc/1/preview.gif",
    "subscriptions": 12500,
    "favorited": 900,
    "views": 40000,
    "file_size": "1234567",
    "time_updated": 1700000000,
    "time_created": 1690000000,
    "tags": ["Pixel art", "Anime", {"bad": "tag"}],
    "short_description": "A purple friend",
}


def test_parse_item_normalises_fields():
    it = steam.parse_item(RAW)
    assert it.id == "3122339805"
    assert it.title == "Gengar"
    assert it.preview.endswith("preview.gif")
    assert it.subscriptions == 12500
    assert it.file_size == 1234567          # arrives as a string
    assert it.tags == ["Pixel art", "Anime"]  # non-strings dropped
    assert it.to_dict()["url"].endswith("id=3122339805")
    assert it.to_dict()["subscribe_url"].startswith("steam://")


def test_parse_item_survives_missing_and_junk():
    it = steam.parse_item({"publishedfileid": 7, "title": "  ", "file_size": "nope"})
    assert it.id == "7"
    assert it.title == "(untitled)"
    assert it.file_size == 0
    assert it.tags == []


def test_parse_query_reads_total_and_details():
    payload = {"response": {"total": 42, "publishedfiledetails": [RAW, {"publishedfileid": "9"}]}}
    items, total = steam.parse_query(payload)
    assert total == 42
    assert [i.id for i in items] == ["3122339805", "9"]


def test_parse_query_tolerates_empty_response():
    assert steam.parse_query({}) == ([], 0)


# --------------------------------------------------------------------------- requests

def _params(**kw):
    qs = steam.build_query_params("KEY", **kw)
    assert qs["key"] == "KEY"
    return json.loads(qs["input_json"])


def test_query_params_defaults():
    p = _params()
    assert p["appid"] == steam.WE_APP_ID
    assert p["query_type"] == steam.SORTS["trend"]
    assert p["page"] == 1
    assert p["numperpage"] == steam.PAGE_SIZE
    assert p["return_previews"] is True
    assert "requiredtags" not in p


@pytest.mark.parametrize("sort", sorted(steam.SORTS))
def test_query_params_every_sort_maps(sort):
    assert _params(sort=sort)["query_type"] == steam.SORTS[sort]


def test_query_params_rejects_unknown_sort():
    with pytest.raises(steam.SteamError):
        steam.build_query_params("K", sort="nonsense")


def test_search_text_forces_text_sort():
    p = _params(sort="trend", search="  dragon  ")
    assert p["query_type"] == steam.SORTS["text"]
    assert p["search_text"] == "dragon"


def test_tags_are_sent_as_a_list():
    p = _params(tags=["Anime", "Space"])
    assert p["requiredtags"] == ["Anime", "Space"]


def test_page_size_is_clamped_and_page_floored():
    assert _params(page_size=999)["numperpage"] == 100
    assert _params(page=0)["page"] == 1


def test_query_requires_a_key():
    with pytest.raises(steam.SteamError, match="API key"):
        steam.query("")


def test_query_builds_expected_url_and_parses():
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        return json.dumps({"response": {"total": 1, "publishedfiledetails": [RAW]}}).encode()

    items, total = steam.query("KEY", _get=fake_get, sort="popular", page=2)
    assert total == 1 and items[0].title == "Gengar"
    q = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
    assert q["key"] == ["KEY"]
    body = json.loads(q["input_json"][0])
    assert body["query_type"] == steam.SORTS["popular"]
    assert body["page"] == 2


def test_query_reports_a_bad_key_clearly():
    import urllib.error

    def fake_get(url, timeout):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    with pytest.raises(steam.SteamError, match="rejected the API key"):
        steam.query("BAD", _get=fake_get)


def test_query_reports_unreachable_steam():
    def fake_get(url, timeout):
        raise OSError("network is down")

    with pytest.raises(steam.SteamError, match="could not reach Steam"):
        steam.query("KEY", _get=fake_get)


def test_query_reports_non_json():
    with pytest.raises(steam.SteamError, match="not JSON"):
        steam.query("KEY", _get=lambda url, timeout: b"<html>hi</html>")


def test_iter_pages_dedupes_and_stops_when_empty():
    pages = {
        1: {"response": {"total": 3, "publishedfiledetails": [RAW, {"publishedfileid": "9", "title": "b"}]}},
        2: {"response": {"total": 3, "publishedfiledetails": [{"publishedfileid": "9", "title": "b"}]}},
        3: {"response": {"total": 3, "publishedfiledetails": []}},
    }

    def fake_get(url, timeout):
        page = int(json.loads(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["input_json"][0])["page"])
        return json.dumps(pages[page]).encode()

    got = [i.id for i in steam.iter_pages("KEY", pages=3, _get=fake_get)]
    assert got == ["3122339805", "9"]

# ------------------------------------------------------------------ tags

def test_tags_are_read_from_steam_tag_objects():
    """Steam sends tags as objects, not strings. Keeping only strings lost all of them."""
    item = steam.parse_item({
        "publishedfileid": "1",
        "title": "Luna Snow",
        "tags": [
            {"tag": "Video", "display_name": "Video"},
            {"tag": "Anime", "display_name": "Anime"},
            {"tag": "1920 x 1080", "display_name": "1920 x 1080"},
        ],
    })
    assert item.tags == ["Video", "Anime", "1920 x 1080"]


def test_tag_parsing_also_accepts_plain_strings_and_skips_junk():
    item = steam.parse_item({
        "publishedfileid": "1", "title": "x",
        "tags": ["Anime", {"tag": "Video"}, {"display_name": "Music"}, {}, None, "Anime"],
    })
    # "Video" falls back to the `tag` key, {} and None are skipped, duplicates dropped
    assert item.tags == ["Anime", "Video", "Music"]


def test_missing_tags_is_an_empty_list():
    assert steam.parse_item({"publishedfileid": "1", "title": "x"}).tags == []
