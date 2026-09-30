"""Steam Workshop browsing (official Web API) and fetching items (steamcmd).

Browsing uses ``IPublishedFileService/QueryFiles``, which needs a free Steam Web API key
from steamcommunity.com/dev/apikey.

Fetching uses ``steamcmd`` with the user's own Steam account. Wallpaper Engine is *not*
in Valve's anonymous-download set -- an anonymous steamcmd replies
``ERROR! Download item <id> failed (No match)`` -- so a real login is required. Items
land in a Steam library's ``steamapps/workshop/content/431960/<id>``, which is exactly
where linux-wallpaperengine looks when given ``--bg <id>``.

Everything here is pure except :func:`query` and :func:`run_steamcmd`, so request
building and response parsing are unit-testable with no key and no Steam login.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .workshop import WE_APP_ID

QUERY_URL = "https://api.steampowered.com/IPublishedFileService/QueryFiles/v1/"
DETAILS_URL = "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/"
ITEM_PAGE = "https://steamcommunity.com/sharedfiles/filedetails/?id={id}"
SUBSCRIBE_URL = "steam://url/CommunityFilePage/{id}"

# QueryFiles query_type values.
SORTS: dict[str, int] = {
    "trend": 3,     # RankedByTrend
    "popular": 12,  # RankedByTotalUniqueSubscriptions
    "recent": 1,    # RankedByPublicationDate
    "updated": 21,  # RankedByLastUpdatedDate
    "text": 11,     # RankedByTextSearch
}
DEFAULT_SORT = "trend"
PAGE_SIZE = 30

# The Workshop's `filetype` filter is the item's own `file_type` PLUS ONE (0 means "all").
# Measured against the live API: querying 1 returns file_type 2, 2 -> 3, 3 -> 4, 4 -> 5.
# For Wallpaper Engine that maps to:
#   file_type 2 = Scene      3 = Video      4 = Application      5 = Web
# Every named entry used to be 0, so choosing a type silently did nothing at all.
FILETYPES: dict[str, int] = {
    "all": 0,
    "scene": 1,        # item file_type 2
    "video": 2,        # item file_type 3
    "application": 3,  # item file_type 4  (often collections rather than wallpapers)
    "web": 4,          # item file_type 5
}

# The tags Wallpaper Engine items actually carry, in the groups the app shows them in.
# Verified against the live API: every tag here matches real items, while the previous
# hand-written list contained tags that do not exist ("Dark" matched 0).
TAG_GROUPS: dict[str, list[str]] = {
    "Type": ["Scene", "Video", "Wallpaper", "Customizable", "MMD"],
    "Theme": ["Anime", "Game", "Music", "Abstract", "Nature", "Landscape", "Space",
              "Sci-Fi", "Fantasy", "Animal", "Relaxing", "Pixel art"],
    "Age": ["Everyone", "Questionable", "Mature"],
    "Resolution": ["1920 x 1080", "2560 x 1440", "3840 x 2160", "Other resolution"],
}
TAG_SUGGESTIONS = [t for group in TAG_GROUPS.values() for t in group]


class SteamError(RuntimeError):
    """Something stopped us talking to Steam or steamcmd."""


# --------------------------------------------------------------------------- models


@dataclass
class Item:
    """One workshop entry, normalised from the API's shape."""

    id: str
    title: str
    author: str = ""
    preview: str = ""
    subscriptions: int = 0
    favorited: int = 0
    views: int = 0
    file_size: int = 0
    time_updated: int = 0
    time_created: int = 0
    tags: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "author": self.author,
            "preview": self.preview,
            "subscriptions": self.subscriptions,
            "favorited": self.favorited,
            "views": self.views,
            "file_size": self.file_size,
            "time_updated": self.time_updated,
            "time_created": self.time_created,
            "tags": self.tags,
            "description": self.description,
            "url": ITEM_PAGE.format(id=self.id),
            "subscribe_url": SUBSCRIBE_URL.format(id=self.id),
        }


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _tag_names(raw: Any) -> list[str]:
    """Pull tag names out of a publishedfiledetails entry.

    Steam sends tags as ``{"tag": "Anime", "display_name": "Anime"}`` objects, not bare
    strings. Filtering for strings (as this used to) silently dropped every tag on every
    item, so the "tags" list was always empty in search results.
    """
    names: list[str] = []
    for entry in raw or []:
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = entry.get("display_name") or entry.get("tag") or ""
        else:
            continue
        name = str(name).strip()
        if name and name not in names:
            names.append(name)
    return names


def parse_item(raw: dict[str, Any]) -> Item:
    """Normalise one ``publishedfiledetails`` entry."""
    return Item(
        id=str(raw.get("publishedfileid", "")),
        title=(raw.get("title") or "").strip() or "(untitled)",
        author=(raw.get("creator") or "").strip(),
        preview=(raw.get("preview_url") or "").strip(),
        subscriptions=_int(raw.get("subscriptions")),
        favorited=_int(raw.get("favorited")),
        views=_int(raw.get("views")),
        file_size=_int(raw.get("file_size")),
        time_updated=_int(raw.get("time_updated")),
        time_created=_int(raw.get("time_created")),
        tags=_tag_names(raw.get("tags")),
        description=(raw.get("short_description") or raw.get("file_description") or "").strip(),
    )


# Words too common to carry meaning in a wallpaper title. Dropped before a multi-word
# search is broken up, so "world of warcraft" searches for "world" and "warcraft".
STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "with", "by",
    "is", "it", "my", "or", "from", "as", "be", "this", "that",
})


def search_words(term: str) -> list[str]:
    """The meaningful words in a search term, in order, without duplicates."""
    words = [w for w in re.split(r"[^\w\u4e00-\u9fff]+", term.casefold()) if w]
    keep = [w for w in words if len(w) > 1 and w not in STOPWORDS]
    out: list[str] = []
    for w in keep:
        if w not in out:
            out.append(w)
    return out


def relevance(title: str, term: str) -> int:
    """How well a title answers a search term. Lower is better; -1 means it does not match.

    Steam's own text search is loose: it matches the *description* and tags as readily as the
    title, and then ranks by popularity. That produced two bad results in a row -- searching
    "rain" put "Lofi Cafe" third, and searching "World of Warcraft" returned no Warcraft
    wallpaper at all in the top ten. So results are matched here instead.

    Bands 0-3 are exact/near-phrase matches. Bands 4+ are *coverage*: for a multi-word
    search, a title containing every word beats one containing only some, which is what
    makes a query like "world of warcraft" find Warcraft wallpapers.
    """
    if not term.strip():
        return 0
    t, q = title.casefold(), term.strip().casefold()
    if t == q:
        return 0
    if t.startswith(q):
        return 1
    # a match at the start of any word is still a strong one
    if re.search(r"\b" + re.escape(q), t):
        return 2
    if q in t:
        return 3
    words = search_words(term)
    if not words:
        return -1
    hits = sum(1 for w in words if w in t)
    if not hits:
        return -1
    return 4 + (len(words) - hits) * 4


def rank(items: list[Item], term: str) -> list[Item]:
    """Put title matches first, keeping Steam's own order within each band.

    `relevance` returns -1 for "the title does not contain the term at all", which must sort
    LAST, not first: sorting on the raw score put every non-matching item at the top.
    """
    if not term.strip():
        return list(items)
    return sorted(items, key=lambda i: (lambda r: (r < 0, r))(relevance(i.title, term)))


def parse_query(payload: dict[str, Any]) -> tuple[list[Item], int]:
    """``(items, total)`` from a QueryFiles response body."""
    body = payload.get("response") or {}
    items = [parse_item(d) for d in (body.get("publishedfiledetails") or []) if isinstance(d, dict)]
    return items, _int(body.get("total"))


# --------------------------------------------------------------------------- requests


def build_query_params(api_key: str, *, sort: str = DEFAULT_SORT, page: int = 1,
                       search: str = "", tags: list[str] | None = None,
                       filetype: str = "all", appid: int = WE_APP_ID,
                       page_size: int = PAGE_SIZE) -> dict[str, str]:
    """Query string for one page of workshop results."""
    if sort not in SORTS:
        raise SteamError(f"unknown sort: {sort}")
    if filetype not in FILETYPES:
        raise SteamError(f"unknown file type: {filetype}")
    page = max(1, int(page))
    payload: dict[str, Any] = {
        "appid": appid,
        "query_type": SORTS[sort],
        "page": page,
        "numperpage": max(1, min(100, int(page_size))),
        "return_previews": True,
        "return_tags": True,
        "return_short_description": True,
        "filetype": FILETYPES[filetype],
        "include_metadata": True,
        "cache_max_age_seconds": 0,
    }
    if search.strip():
        # a text query must use the text-search sort for the API to honour it
        payload["query_type"] = SORTS["text"]
        payload["search_text"] = search.strip()
    if tags:
        payload["requiredtags"] = list(tags)
    return {"key": api_key, "input_json": json.dumps(payload)}


def _http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "wallpaper-picker"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed host)
        return resp.read()


def query(api_key: str, timeout: float = 20.0, _get: Callable[[str, float], bytes] | None = None,
          **kwargs: Any) -> tuple[list[Item], int]:
    """Fetch one page of workshop results. Raises :class:`SteamError` on any failure."""
    if not api_key:
        raise SteamError("no Steam Web API key set")
    params = build_query_params(api_key, **kwargs)
    url = QUERY_URL + "?" + urllib.parse.urlencode(params)
    get = _get or _http_get
    try:
        raw = get(url, timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SteamError("Steam rejected the API key (check it at steamcommunity.com/dev/apikey)") from e
        raise SteamError(f"Steam returned HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise SteamError(f"could not reach Steam: {e}") from e
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as e:
        raise SteamError("Steam sent a response that was not JSON") from e
    return parse_query(payload)


def search_items(api_key: str, term: str, *, sort: str = DEFAULT_SORT, filetype: str = "all",
                 tags: list[str] | None = None, pages: int = 3,
                 _query: Callable[..., tuple[list[Item], int]] | None = None) -> tuple[list[Item], int]:
    """Search, then order by how well each title matches.

    Several pages are fetched because the ranking happens locally: the best title match for
    a term is often not on Steam's first page. ``total`` is Steam's count for the query, not
    the number of items returned.
    """
    q = _query or query
    seen: dict[str, Item] = {}
    totals: list[int] = []

    words = search_words(term)
    if len(words) <= 1:
        # A single distinctive word: Steam's own paging is fine, just rank the lot.
        plan: list[tuple[str, int]] = [(term, pages)]
    else:
        # Steam treats a multi-word phrase loosely and ranks by popularity, so
        # "World of Warcraft" came back as unrelated popular items. Search the phrase AND
        # each significant word, then let the coverage ranking decide.
        plan = [(term, 1)] + [(w, 1) for w in words[:4]]

    for text, page_count in plan:
        for page in range(1, max(1, page_count) + 1):
            items, total = q(api_key, sort=sort, page=page, search=text,
                             tags=tags, filetype=filetype)
            if total:
                totals.append(total)
            if not items:
                break
            for it in items:
                if it.id and it.id not in seen:
                    seen[it.id] = it
            if len(items) < PAGE_SIZE:
                break       # Steam had nothing more to give

    # Report the smallest count rather than the phrase's: Steam's number for a loose phrase
    # match ("World of Warcraft" -> 26,347) counts items that do not contain the words at all.
    total = min(totals) if totals else 0
    return rank(list(seen.values()), term), total


def iter_pages(api_key: str, pages: int = 3, **kwargs: Any) -> Iterator[Item]:
    """Yield items across consecutive pages, stopping when Steam runs out."""
    seen: set[str] = set()
    for page in range(1, max(1, pages) + 1):
        items, _ = query(api_key, page=page, **kwargs)
        if not items:
            return
        for it in items:
            if it.id and it.id not in seen:
                seen.add(it.id)
                yield it


# --------------------------------------------------------------------------- steamcmd


# Where a working steamcmd may live, best first. The managed copy comes first on
# purpose: it is the one we install and verify.
_MANAGED = "~/.local/share/wallpaper-picker/steamcmd/steamcmd.sh"
_CANDIDATES = (
    _MANAGED,
    "~/.local/share/Steam/steamcmd/steamcmd.sh",
    "/usr/games/steamcmd",
    "/usr/bin/steamcmd",
    "~/.steam/steamcmd/steamcmd.sh",
)


def steamcmd_root_candidate(exe: Path) -> Path:
    """The folder steamcmd.sh expects to find its client in, judging by its own path."""
    return exe.parent


def _is_wrapper_script(exe: Path) -> bool:
    """A small shell script that forwards to the real steamcmd.sh.

    Needed because ~/.local/bin/steamcmd is exactly that: steamcmd.sh cannot be
    symlinked (it resolves its client from $0), so the convenient PATH entry is a
    wrapper. It works, but it has no sibling client of its own.
    """
    try:
        if exe.stat().st_size > 64 * 1024:      # a real binary, not a wrapper
            return False
        head = exe.read_text(encoding="utf-8", errors="replace")[:2048]
    except OSError:
        return False
    return "steamcmd.sh" in head and not head.startswith("\x7fELF")


def is_usable_steamcmd(exe: Path | None) -> bool:
    """Whether running this steamcmd would actually start.

    steamcmd.sh resolves its client relative to itself, so a symlink pointing at the real
    script from elsewhere (e.g. ~/.local/bin) is *not* usable: it looks for
    <link dir>/linux32/steamcmd and exits. A real install has the sibling client; a
    wrapper script that execs the real one is also fine.
    """
    if exe is None:
        return False
    try:
        if not exe.is_file():
            return False
        root = steamcmd_root_candidate(exe)
        for plat in ("linux32", "linux64", "linuxarm64"):
            if (root / plat / "steamcmd").is_file():
                return True
    except OSError:
        return False
    return _is_wrapper_script(exe)


def find_steamcmd() -> Path | None:
    """Locate a steamcmd we can actually run.

    The managed install is checked first; then PATH and the package locations, but each
    candidate must pass is_usable_steamcmd() so a stale or symlinked entry cannot win.
    """
    home = Path.home()
    for cand in _CANDIDATES:
        path = Path(cand).expanduser()
        if is_usable_steamcmd(path):
            return path
    found = shutil.which("steamcmd")
    if found and is_usable_steamcmd(Path(found)):
        return Path(found)
    return None


def broken_steamcmd_paths() -> list[Path]:
    """steamcmd scripts that exist but cannot run -- reported so the user can fix them."""
    home = Path.home()
    broken: list[Path] = []
    seen: set[str] = set()
    for cand in list(_CANDIDATES) + [shutil.which("steamcmd") or ""]:
        if not cand:
            continue
        path = Path(cand).expanduser()
        if str(path) in seen:
            continue
        seen.add(str(path))
        try:
            if path.is_file() and not is_usable_steamcmd(path):
                broken.append(path)
        except OSError:
            continue
    return broken


def steam_libraries(home: Path | None = None) -> list[Path]:
    """Every Steam ``steamapps`` directory we can see, most likely first."""
    home = home or Path.home()
    roots = [
        home / ".steam/debian-installation",
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / "snap/steam/common/.local/share/Steam",
    ]
    out: list[Path] = []
    for root in roots:
        sa = root / "steamapps"
        if not sa.is_dir() or sa in out:
            continue
        out.append(sa)
        vdf = sa / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s*"([^"]+)"', text):
            extra = Path(m.group(1)) / "steamapps"
            if extra.is_dir() and extra not in out:
                out.append(extra)
    return out


def workshop_dir_for(steamapps: Path, appid: int = WE_APP_ID) -> Path:
    return steamapps / "workshop" / "content" / str(appid)


def installed_item_dirs(appid: int = WE_APP_ID, home: Path | None = None) -> dict[str, Path]:
    """``{workshop id: folder}`` for every downloaded item across all libraries."""
    found: dict[str, Path] = {}
    for sa in steam_libraries(home):
        wd = workshop_dir_for(sa, appid)
        if not wd.is_dir():
            continue
        for child in wd.iterdir():
            if child.is_dir() and child.name.isdigit() and child.name not in found:
                found[child.name] = child
    return found


def login_state(steamapps: Path) -> tuple[str | None, bool]:
    """``(account name, guard enabled)`` from ``config/loginusers.vdf``, for this library."""
    vdf = steamapps.parent / "config" / "loginusers.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, False
    who = re.search(r'"AccountName"\s*"([^"]+)"', text)
    guard = re.search(r'"UseSteamGuard"\s*"(\d)"', text)
    if who:
        return who.group(1), bool(guard and guard.group(1) == "1")
    return None, False


@dataclass
class SteamCmdResult:
    ok: bool
    output: str
    item_dir: Path | None = None
    needs_guard: bool = False
    # steamcmd has no cached credentials, so it asked for a password it cannot get.
    needs_login: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "output": self.output,
            "item_dir": str(self.item_dir) if self.item_dir else None,
            "needs_guard": self.needs_guard,
            "needs_login": self.needs_login,
        }


GUARD_RE = re.compile(r"(two-factor code|Steam Guard code|please enter the current code|"
                      r"Enter the current code|Invalid Login Auth Code)", re.I)
# steamcmd has nothing cached and is prompting for a password we cannot supply.
NO_CREDS_RE = re.compile(r"(Cached credentials not found|password:\s*$|"
                         r"Proceeding with login using username/password)", re.I | re.M)
LOGIN_FAIL_RE = re.compile(r"(Invalid Password|Account Logon Denied|Login Failure|"
                           r"Account Disabled|Rate Limit Exceeded)", re.I)
LOGIN_HINT = ("steamcmd is not signed in to Steam. Wallpaper Engine items cannot be "
              "downloaded anonymously, so log in once from a terminal:\n"
              "    {exe} +login <your-steam-account> +quit\n"
              "Enter your password and Steam Guard code once; steamcmd then caches the "
              "credentials and downloads work from the app.")
FAIL_RE = re.compile(r"Unknown command|FAILED|Error|failed", re.I)

# The most useful line steamcmd prints when something goes wrong.
_REASON_RES = (
    re.compile(r"Couldn't find steamcmd at .*", re.I),
    re.compile(r"ERROR! Download item \d+ failed \(([^)]+)\)", re.I),
    re.compile(r"ERROR \(([^)]+)\)"),
    re.compile(r"^FAILED \(([^)]+)\)", re.I | re.M),
    re.compile(r"Unknown command .*", re.I),
)
GENERIC_FAIL = "steamcmd could not download this item."


def failure_reason(text: str) -> str:
    """Pull steamcmd's own explanation out of its output, for a message worth reading."""
    for rx in _REASON_RES:
        m = rx.search(text or "")
        if m:
            reason = m.group(1).strip() if m.groups() else m.group(0).strip()
            return f"steamcmd failed: {reason}"
    return GENERIC_FAIL


def build_download_argv(steamcmd: Path, item_id: str, steamapps: Path,
                        username: str | None = None, password: str | None = None,
                        guard_code: str | None = None, appid: int = WE_APP_ID) -> list[str]:
    """The steamcmd command line that downloads ``item_id`` into ``steamapps``' owner.

    ``steamcmd`` insists on a real install dir, and it writes workshop content under
    ``<dir>/steamapps/workshop/content/<appid>/<item>``. We point it at the Steam
    library's parent so the result lands where the engine already looks.
    """
    login = ["+login", "anonymous"] if not username else ["+login", username]
    if username and password:
        login.append(password)
    argv = [
        str(steamcmd),
        "+force_install_dir", str(steamapps.parent),
        *login,
        "+workshop_download_item", str(appid), str(item_id), "validate",
        "+quit",
    ]
    return argv


def parse_steamcmd_output(text: str, item_id: str, steamapps: Path,
                          appid: int = WE_APP_ID) -> SteamCmdResult:
    """Interpret steamcmd's console output."""
    item_dir = workshop_dir_for(steamapps, appid) / str(item_id)

    # A successful download wins over anything else in the log.
    if item_dir.is_dir() and any(item_dir.iterdir()):
        return SteamCmdResult(True, text, item_dir)

    if GUARD_RE.search(text):
        return SteamCmdResult(False, text, None, needs_guard=True)
    # no cached credentials (and possibly a password prompt we could not answer)
    if NO_CREDS_RE.search(text):
        return SteamCmdResult(False, text, None, needs_login=True)
    if LOGIN_FAIL_RE.search(text):
        return SteamCmdResult(False, text, None, needs_login=True)
    return SteamCmdResult(False, text, None)


def run_steamcmd(argv: list[str], input_text: str | None = None,
                 timeout: float = 900.0) -> str:
    """Run steamcmd and return its combined output."""
    try:
        proc = subprocess.run(  # noqa: S603 (argv is built by us)
            argv, input=(input_text + "\n" if input_text else None),
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise SteamError(f"steamcmd timed out after {timeout:.0f}s") from e
    except OSError as e:
        raise SteamError(f"could not run steamcmd: {e}") from e
    return (proc.stdout or "") + (proc.stderr or "")


# steamcmd prints this when it can log in without a password.
CACHED_OK_RE = re.compile(r"Logging in using cached credentials", re.I)


def probe_login(steamcmd: Path | None = None, username: str | None = None,
                timeout: int = 60) -> tuple[bool, str]:
    """Ask steamcmd whether it can actually log in, and report what it said.

    Why not inspect files: measured on this machine, steamcmd logged in using cached
    credentials while writing no ssfn* file and no loginusers.vdf of its own -- it reuses
    the Steam client config under ~/.steam. Guessing from files gave a false negative
    that blocked an already signed-in user. steamcmd's own output line is the only
    reliable signal, so we run a short no-op and read it.

    Returns (logged_in, raw_output).
    """
    exe = find_steamcmd() if steamcmd is None else steamcmd
    if exe is None:
        return False, "steamcmd is not installed."

    argv = [str(exe), "+login", username or "anonymous", "+quit"]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, cwd=str(exe.parent),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"could not run steamcmd: {e}"

    text = (proc.stdout or "") + (proc.stderr or "")
    return bool(CACHED_OK_RE.search(text)), text


def login_hint(steamcmd: Path | None = None) -> str:
    """The exact command the user should run once."""
    exe = steamcmd or find_steamcmd()
    return LOGIN_HINT.format(exe=exe or "steamcmd.sh")


def download(item_id: str, steamapps: Path, *, username: str | None = None,
             password: str | None = None, guard_code: str | None = None,
             appid: int = WE_APP_ID, steamcmd: Path | None = None,
             timeout: float = 900.0, runner: Callable[..., str] | None = None) -> SteamCmdResult:
    """Fetch one workshop item into ``steamapps``."""
    exe = steamcmd or find_steamcmd()
    if exe is None:
        raise SteamError("steamcmd is not installed")
    argv = build_download_argv(exe, item_id, steamapps, username, password, guard_code, appid)
    run = runner or run_steamcmd
    try:
        text = run(argv, guard_code, timeout=timeout)
    except TypeError:
        text = run(argv, guard_code)  # injected runners often take just argv
    return parse_steamcmd_output(text, item_id, steamapps, appid)