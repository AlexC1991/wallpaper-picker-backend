"""/proc helpers: finding, signalling, stopping and measuring engine processes."""

from __future__ import annotations

import os
import re
import signal
import time
from dataclasses import dataclass
from pathlib import Path

ENGINE_BASENAME = "linux-wallpaperengine"
CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")


def _stat_fields(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # comm may contain spaces/parens: split after the last ')'
    rp = raw.rfind(")")
    return [raw[:rp + 1]] + raw[rp + 2:].split()


def starttime(pid: int) -> int | None:
    f = _stat_fields(pid)
    return int(f[20]) if f and len(f) > 20 else None


def state(pid: int) -> str | None:
    f = _stat_fields(pid)
    return f[1] if f else None


def cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def alive(pid: int, start: int | None = None) -> bool:
    st = state(pid)
    if st is None or st == "Z":
        return False
    return start is None or starttime(pid) == start


def is_engine(pid: int) -> bool:
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        exe = ""
    if os.path.basename(exe.replace(" (deleted)", "")) == ENGINE_BASENAME:
        return True
    argv = cmdline(pid)
    return bool(argv) and os.path.basename(argv[0]) == ENGINE_BASENAME


def screen_outputs(argv: list[str]) -> set[str]:
    outs: set[str] = set()
    for i, a in enumerate(argv[:-1]):
        if a in ("--screen-root", "-r"):
            outs.add(argv[i + 1])
        elif a == "--screen-span":
            outs.update(x for x in argv[i + 1].split(",") if x)
    return outs


def find_screen_engines(exclude: set[int] | None = None) -> list[tuple[int, list[str]]]:
    """This user's linux-wallpaperengine processes that draw on screens (not windowed previews)."""
    exclude = exclude or set()
    uid = os.getuid()
    found = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        pid = int(d.name)
        if pid in exclude or pid == os.getpid():
            continue
        try:
            if d.stat().st_uid != uid:
                continue
        except OSError:
            continue
        if not is_engine(pid) or state(pid) in (None, "Z"):
            continue
        argv = cmdline(pid)
        if screen_outputs(argv):
            found.append((pid, argv))
    return found


def children_map() -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        f = _stat_fields(int(d.name))
        if f and len(f) > 3:
            kids.setdefault(int(f[2]), []).append(int(d.name))
    return kids


def descendants(pid: int, kids: dict[int, list[int]] | None = None) -> list[int]:
    kids = kids if kids is not None else children_map()
    out, stack = [], list(kids.get(pid, []))
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(kids.get(p, []))
    return out


def signal_tree(pid: int, sig: int) -> None:
    """Signal a process, its process group (if it leads one) and all its descendants."""
    targets = [pid] + descendants(pid)
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    for p in targets:
        try:
            os.kill(p, sig)
        except (ProcessLookupError, PermissionError):
            pass


def terminate(pid: int, start: int | None = None, timeout: float = 4.0) -> bool:
    """Stop exactly this process (and its children). Returns True once it is gone."""
    if not alive(pid, start):
        return True
    tree = [pid] + descendants(pid)
    for sig in (signal.SIGTERM, signal.SIGCONT):  # a SIGSTOPped process needs CONT to act on TERM
        signal_tree(pid, sig)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid, start):
            break
        time.sleep(0.05)
    if alive(pid, start):
        signal_tree(pid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while alive(pid, start) and time.monotonic() < deadline:
            time.sleep(0.05)
    for p in tree[1:]:  # stragglers (e.g. CEF helpers)
        if alive(p):
            try:
                os.kill(p, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    return not alive(pid, start)


# --------------------------------------------------------------------------- usage

def _cpu_ticks(pid: int) -> int:
    f = _stat_fields(pid)
    # f[0] is "pid (comm)", so f[i] is stat field i+2: utime=14 -> f[12], stime=15 -> f[13]
    return int(f[12]) + int(f[13]) if f and len(f) > 13 else 0


def _rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        return int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * PAGE // 1024
    except (OSError, ValueError, IndexError):
        return 0


@dataclass
class Usage:
    cpu_percent: float
    rss_mb: float
    processes: int


class UsageSampler:
    """CPU% (of one core) and memory (PSS) of a process tree, between successive calls."""

    def __init__(self) -> None:
        self._last: dict[int, tuple[float, int]] = {}

    def sample(self, pid: int) -> Usage:
        tree = [pid] + descendants(pid)
        ticks = sum(_cpu_ticks(p) for p in tree)
        mem = sum(_rss_kb(p) for p in tree)
        now = time.monotonic()
        prev = self._last.get(pid)
        self._last[pid] = (now, ticks)
        cpu = 0.0
        if prev and now > prev[0]:
            cpu = 100.0 * (ticks - prev[1]) / CLK_TCK / (now - prev[0])
        return Usage(round(max(cpu, 0.0), 1), round(mem / 1024, 1), len(tree))


# --------------------------------------------------------------------------- engine logs

BENIGN = [
    r"libcuda", r"CUDA", r"Failed to initialize GLEW", r"No GLX display",
    r"Fullscreen detection", r"VolumeLight objects are not supported",
    r"^Running with:", r"AVHWDeviceContext", r"Using hardware decoding",
    r"^\s*\(\+\)", r"^VO:", r"^AO:", r"ALSA", r"pulse", r"pipewire",
    r"dbus", r"DBus", r"gpu_memory_buffer", r"\bviz\b", r"sandbox",
    r"ERROR:.*(gles2_cmd_decoder|shared_image|command_buffer|angle_platform|gpu_channel|gl_utils)",
    r"Fontconfig", r"libva info", r"Applying override value",
]
_BENIGN_RE = re.compile("|".join(BENIGN))
_ERROR_RE = re.compile(
    r"(exception|error|failed|failure|cannot|can't|could not|unable|not found|invalid|"
    r"unsupported|not supported|missing|segmentation|abort|terminate called)", re.I)
_FATAL_RE = re.compile(r"(terminate called|segmentation fault|core dumped|Cannot find workshop|"
                       r"Project type missing|main file missing|Unsupported project type|Aborted|"
                       r"close symbol missing|FATAL:|Check failed|CefInitialize: failed)", re.I)

# Plain-language explanations for known engine failures.
HINTS = [
    (re.compile(r"close symbol missing|zygote|CefInitialize|FATAL:.*\.cc"),
     "The engine's built-in browser (CEF) crashed while starting, so web wallpapers can't run "
     "with this linux-wallpaperengine build."),
    (re.compile(r"Cannot find workshop"), "The engine can't find this wallpaper's workshop folder."),
    (re.compile(r"Unsupported project type|Project type missing"), "The engine doesn't support this wallpaper type."),
]


def explain(errors: list[str]) -> str | None:
    text = "\n".join(errors)
    for rx, hint in HINTS:
        if rx.search(text):
            return hint
    return None


def analyze_log(text: str) -> tuple[list[str], list[str]]:
    """Split engine output into (errors, warnings) worth showing to the user."""
    errors: list[str] = []
    warnings: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or _BENIGN_RE.search(s):
            continue
        if _FATAL_RE.search(s):
            if s not in errors:
                errors.append(s[:400])
        elif _ERROR_RE.search(s):
            if s not in warnings:
                warnings.append(s[:400])
    return errors, warnings[:30]


def tail(path: Path, lines: int = 25) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            data = f.read().decode(errors="replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])
