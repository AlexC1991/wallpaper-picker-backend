"""Background daemon: owns the engine processes.

* applies the saved config at login and whenever asked (D-Bus ``Apply``),
* pauses engines (SIGSTOP) while a full-screen window is focused (via winbar),
* restarts crashed engines with backoff (3 tries, then gives up),
* re-applies when monitors change,
* writes live status to $XDG_RUNTIME_DIR/wallpaper-picker/status.json.

D-Bus: name ``dev.wallpaperpicker.Daemon``, object ``/dev/wallpaperpicker/Daemon``,
interface ``dev.wallpaperpicker.Daemon1``: ``Apply() -> s``, ``Stop() -> s``,
``Status() -> s``, ``Quit() -> s`` (JSON strings), signal ``StatusChanged(s)``.
"""
# NB: no ``from __future__ import annotations`` here: dbus-fast reads the signature strings
# from the method annotations.

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config as config_mod
from . import engine as engine_mod
from . import monitors as monitors_mod
from . import paths, power, procs, workshop
from .pause import PauseController, WinbarWatcher

log = logging.getLogger("wallpaper_picker.daemon")

BUS_NAME = "dev.wallpaperpicker.Daemon"
OBJ_PATH = "/dev/wallpaperpicker/Daemon"
IFACE = "dev.wallpaperpicker.Daemon1"

VERIFY_SECONDS = 4.0
BACKOFF = (2.0, 5.0, 15.0)
MAX_RESTARTS = 3
STABLE_SECONDS = 120.0
MONITOR_POLL = 3.0
STATUS_POLL = 5.0


@dataclass
class Engine:
    spec: engine_mod.EngineSpec
    log_path: Path
    proc: Optional[asyncio.subprocess.Process] = None
    pid: int = 0
    start_ticks: Optional[int] = None
    started_mono: float = 0.0
    started_at: float = 0.0
    state: str = "starting"  # starting|running|paused|restarting|failed|stopped
    restarts: int = 0
    exit_code: Optional[int] = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    stopping: bool = False
    cpu: float = 0.0
    mem_mb: float = 0.0
    nprocs: int = 0
    waiter: Optional[asyncio.Task] = None
    restart_handle: Optional[asyncio.TimerHandle] = None

    @property
    def key(self) -> str:
        return self.spec.key

    def to_dict(self) -> dict:
        return {
            "key": self.key, "pid": self.pid, "state": self.state, "argv": self.spec.argv,
            "outputs": self.spec.outputs, "units": self.spec.describe(),
            "started_at": self.started_at, "restarts": self.restarts, "exit_code": self.exit_code,
            "errors": self.errors, "warnings": self.warnings, "log": str(self.log_path),
            "cpu_percent": self.cpu, "mem_mb": self.mem_mb, "processes": self.nprocs,
        }


def setup_logging(verbose: bool = False) -> None:
    paths.ensure_dirs()
    root = logging.getLogger("wallpaper_picker")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(paths.state_dir() / "daemon.log", maxBytes=512_000, backupCount=2)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


class Daemon:
    def __init__(self) -> None:
        self.engines: dict = {}
        self.lock = asyncio.Lock()
        self.cfg = config_mod.Config()
        self.monitors: list = []
        self.mon_sig: tuple = ()
        self.problems: list = []
        self.last_apply: dict = {}
        self.sampler = procs.UsageSampler()
        self.controller = PauseController(self._pause, self._resume)
        self.bus = None
        self.iface = None
        self.stop_event = asyncio.Event()
        self.power = power.PowerState()
        self._status_dirty = False
        self.isolated: set = set()  # slot keys that crashed while sharing a process

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {
            "daemon_pid": os.getpid(),
            "updated": time.time(),
            "enabled": self.cfg.enabled,
            "monitors": [m.to_dict() for m in self.monitors],
            "engines": [e.to_dict() for e in self.engines.values()],
            "problems": self.problems,
            "winbar": self.controller.available,
            "fullscreen": [w.title or w.app_id for w in self.controller.blocking],
            "paused": sorted(self.controller.paused),
            "power": self.power.to_dict(),
            "power_paused": self.controller.power_paused,
            "last_apply": self.last_apply,
        }

    def publish(self) -> None:
        st = self.status()
        blob = json.dumps(st, ensure_ascii=False)
        path = paths.status_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(blob, encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            log.warning("could not write status: %s", e)
        if self.iface is not None:
            try:
                self.iface.StatusChanged(blob)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ pause hooks
    def _pause(self, key: str) -> None:
        e = self.engines.get(key)
        if e and e.pid and procs.alive(e.pid, e.start_ticks):
            procs.signal_tree(e.pid, signal.SIGSTOP)
            e.state = "paused"
            log.info("paused %s (pid %d): full-screen window focused", key, e.pid)
            self.publish()

    def _resume(self, key: str) -> None:
        e = self.engines.get(key)
        if e and e.pid and procs.alive(e.pid, e.start_ticks):
            procs.signal_tree(e.pid, signal.SIGCONT)
            if e.state == "paused":
                e.state = "running"
            log.info("resumed %s (pid %d)", key, e.pid)
            self.publish()

    def _sync_controller(self) -> None:
        self.controller.configure(self.cfg.options.pause_scope, self.cfg.options.pause_on_fullscreen,
                                  self.cfg.options.pause_ignore_appids)
        self.controller.set_engines({k: set(e.spec.outputs) for k, e in self.engines.items()
                                     if e.state in ("running", "paused", "starting")})

    # ------------------------------------------------------------------ processes
    async def _start(self, eng: Engine) -> None:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        if eng.log_path.exists():
            try:
                os.replace(eng.log_path, eng.log_path.with_suffix(".prev.log"))
            except OSError:
                pass
        argv = engine_mod.ensure_silent(eng.spec.argv)
        logf = open(eng.log_path, "wb")
        try:
            eng.proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=logf, stderr=asyncio.subprocess.STDOUT,
                cwd=str(Path(argv[0]).parent), start_new_session=True)
        except OSError as e:
            logf.close()
            eng.state, eng.errors = "failed", [f"Could not start engine: {e}"]
            log.error("could not start %s: %s", eng.key, e)
            return
        logf.close()
        eng.pid = eng.proc.pid
        eng.start_ticks = procs.starttime(eng.pid)
        eng.started_mono = time.monotonic()
        eng.started_at = time.time()
        eng.state, eng.exit_code, eng.errors, eng.warnings, eng.stopping = "starting", None, [], [], False
        log.info("started %s pid %d: %s", eng.key, eng.pid, " ".join(argv))
        self.controller.forget(eng.key)
        eng.waiter = asyncio.ensure_future(self._watch(eng, eng.proc))

    async def _verify(self, eng: Engine) -> None:
        # the embedded browser takes longer to fail than scenes/videos
        web = any(u.wtype == "web" for u in eng.spec.units)
        deadline = time.monotonic() + (VERIFY_SECONDS * 2.5 if web else VERIFY_SECONDS)
        while time.monotonic() < deadline:
            if eng.proc is None or eng.proc.returncode is not None:
                break
            await asyncio.sleep(0.2)
        self._scan_log(eng)
        if eng.proc is not None and eng.proc.returncode is None and eng.state == "starting":
            eng.state = "running"

    def _scan_log(self, eng: Engine) -> None:
        try:
            text = eng.log_path.read_text(errors="replace")
        except OSError:
            return
        errors, warnings = procs.analyze_log(text)
        if eng.state in ("failed", "restarting") and eng.errors:
            errors = list(dict.fromkeys(eng.errors + errors))
        eng.errors, eng.warnings = errors, warnings

    async def _watch(self, eng: Engine, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        if eng.proc is not proc:
            return
        eng.exit_code = code
        if eng.stopping:
            eng.state = "stopped"
            return
        ran = time.monotonic() - eng.started_mono
        self._scan_log(eng)
        if not eng.errors:
            eng.errors = [f"Engine exited with code {code}"] + \
                [ln for ln in procs.tail(eng.log_path, 8).splitlines() if ln.strip()][-6:]
        fatal = ran < 15 and any(procs._FATAL_RE.search(e) for e in eng.errors)
        if ran > STABLE_SECONDS:
            eng.restarts = 0
        log.warning("engine %s (pid %d) exited with %s after %.0fs", eng.key, eng.pid, code, ran)
        self.controller.forget(eng.key)
        hint = procs.explain(eng.errors)
        if hint and hint not in eng.errors:
            eng.errors.insert(0, hint)
        if fatal or eng.restarts >= MAX_RESTARTS:
            eng.state = "failed"
            log.error("giving up on %s: %s", eng.key,
                      "wallpaper failed to load" if fatal else f"{MAX_RESTARTS} restarts failed")
            if len(eng.spec.units) > 1:
                # it shared a process: split it up so the healthy monitors come back
                self.isolated.update(u.slot for u in eng.spec.units)
                log.info("splitting %s into one engine per monitor", eng.key)
                asyncio.get_running_loop().call_later(
                    0.5, lambda: asyncio.ensure_future(self.apply("isolating a failed engine")))
        else:
            delay = BACKOFF[min(eng.restarts, len(BACKOFF) - 1)]
            eng.restarts += 1
            eng.state = "restarting"
            log.info("restarting %s in %.0fs (attempt %d/%d)", eng.key, delay, eng.restarts, MAX_RESTARTS)
            loop = asyncio.get_running_loop()
            eng.restart_handle = loop.call_later(delay, lambda: asyncio.ensure_future(self._restart(eng)))
        self._sync_controller()
        self.publish()

    async def _restart(self, eng: Engine) -> None:
        async with self.lock:
            if self.engines.get(eng.key) is not eng or eng.state != "restarting":
                return
            await self._start(eng)
            await self._verify(eng)
            self._sync_controller()
            self.publish()

    async def _stop(self, eng: Engine) -> None:
        eng.stopping = True
        if eng.restart_handle:
            eng.restart_handle.cancel()
        if eng.pid:
            pid, ticks = eng.pid, eng.start_ticks
            await asyncio.get_running_loop().run_in_executor(None, procs.terminate, pid, ticks)
            log.info("stopped %s (pid %d)", eng.key, pid)
        if eng.waiter:
            try:
                await asyncio.wait_for(eng.waiter, 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        eng.state = "stopped"

    async def _stop_foreign(self, outputs: Optional[set]) -> list:
        """Stop other linux-wallpaperengine screen processes (exact PIDs) on these outputs."""
        ours = {e.pid for e in self.engines.values() if e.pid}
        stopped = []
        for pid, argv in procs.find_screen_engines(exclude=ours):
            outs = procs.screen_outputs(argv)
            if outputs is None or outs & outputs:
                ticks = procs.starttime(pid)
                log.info("taking over: stopping pid %d (%s)", pid, " ".join(argv))
                await asyncio.get_running_loop().run_in_executor(None, procs.terminate, pid, ticks)
                stopped.append(pid)
        return stopped

    # ------------------------------------------------------------------ apply
    async def apply(self, reason: str = "request") -> dict:
        async with self.lock:
            return await self._apply(reason)

    async def _apply(self, reason: str) -> dict:
        loop = asyncio.get_running_loop()
        self.cfg = config_mod.load()
        mons = await loop.run_in_executor(None, monitors_mod.list_monitors)
        if mons:
            self.monitors, self.mon_sig = mons, monitors_mod.signature(mons)
        library_list = await loop.run_in_executor(None, workshop.scan)
        library = {w.id: w for w in library_list}
        engine_bin = paths.find_engine(self.cfg.options.engine)
        specs: list = []
        problems: list = []
        if engine_bin is None:
            problems.append("linux-wallpaperengine is not installed")
        elif self.cfg.enabled:
            specs, problems = await loop.run_in_executor(
                None, lambda: engine_mod.build_plan(
                    self.cfg, self.monitors, library, str(engine_bin), engine_mod.resolve_assets_dir(self.cfg),
                    snapshot_dir=str(paths.runtime_dir()) if self.cfg.options.snapshots else None,
                    isolate=self.isolated))
        self.problems = problems
        wanted = {s.key: s for s in specs}
        # stop engines that are gone or changed
        for key, eng in list(self.engines.items()):
            same = key in wanted and wanted[key].argv == eng.spec.argv and eng.state in ("running", "paused", "starting")
            if not same:
                await self._stop(eng)
                del self.engines[key]
        self._sync_controller()
        # stop anyone else drawing on our screens (e.g. a manually started engine)
        taken = await self._stop_foreign(None if not specs else {o for s in specs for o in s.outputs})
        started = []
        for key, spec in wanted.items():
            if key in self.engines:
                self.engines[key].spec = spec
                continue
            safe = "".join(c if c.isalnum() or c in "-_+" else "_" for c in key)
            eng = Engine(spec=spec, log_path=paths.logs_dir() / f"engine-{safe}.log")
            self.engines[key] = eng
            await self._start(eng)
            started.append(eng)
        await asyncio.gather(*(self._verify(e) for e in started))
        self._sync_controller()
        failed = [e for e in self.engines.values() if e.state in ("failed", "restarting") or e.exit_code is not None]
        ok = not failed and not problems
        msg = []
        for e in failed:
            titles = ", ".join(u.title for u in e.spec.units)
            msg.append(f"{titles} on {', '.join(e.spec.outputs)} failed: " + ("; ".join(e.errors[:3]) or "engine exited"))
        msg.extend(problems)
        self.last_apply = {
            "time": time.time(), "reason": reason, "ok": ok,
            "started": [e.key for e in started], "kept": [k for k in wanted if k not in [e.key for e in started]],
            "took_over": taken, "message": "\n".join(msg) if msg else "Applied",
        }
        log.info("apply (%s): %s", reason, self.last_apply["message"].replace("\n", " | "))
        self.publish()
        return {**self.last_apply, "status": self.status()}

    async def stop_all(self) -> dict:
        async with self.lock:
            for key, eng in list(self.engines.items()):
                await self._stop(eng)
                del self.engines[key]
            taken = await self._stop_foreign(None)
            self._sync_controller()
            self.last_apply = {"time": time.time(), "reason": "stop", "ok": True, "took_over": taken,
                               "message": "Stopped"}
            self.publish()
            return {**self.last_apply, "status": self.status()}

    # ------------------------------------------------------------------ loops
    async def monitor_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self.stop_event.is_set():
            await asyncio.sleep(MONITOR_POLL)
            try:
                mons = await loop.run_in_executor(None, monitors_mod.list_monitors)
            except Exception:  # noqa: BLE001
                continue
            sig = monitors_mod.signature(mons)
            if not mons or sig == self.mon_sig:
                continue
            await asyncio.sleep(2.0)  # let the compositor settle, then confirm
            mons = await loop.run_in_executor(None, monitors_mod.list_monitors)
            if mons and monitors_mod.signature(mons) != self.mon_sig:
                log.info("monitors changed: %s", [m.name for m in mons])
                await self.apply("monitors changed")

    async def _refresh_power(self) -> bool:
        """Read the power source and apply the battery policy. True if it changed."""
        opts = self.cfg.options
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(None, power.read_power)
        changed = state != self.power
        self.power = state
        want = power.should_pause(state, opts.pause_on_battery, opts.battery_threshold)
        self.controller.set_power_paused(want)
        return changed

    async def status_loop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(STATUS_POLL)
            changed = False
            try:
                changed = await self._refresh_power() or changed
            except Exception:  # noqa: BLE001 - power is best-effort
                log.debug("power check failed", exc_info=True)
            for e in self.engines.values():
                if e.pid and e.state in ("running", "paused", "starting"):
                    u = self.sampler.sample(e.pid)
                    e.cpu, e.mem_mb, e.nprocs = u.cpu_percent, u.rss_mb, u.processes
                    before = (list(e.errors), list(e.warnings))
                    self._scan_log(e)
                    changed = True if (list(e.errors), list(e.warnings)) != before else changed
            self.publish()

    async def shutdown(self) -> None:
        log.info("daemon shutting down")
        for key, eng in list(self.engines.items()):
            await self._stop(eng)
        self.engines.clear()
        self.publish()

    async def run(self) -> int:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import NameFlag, RequestNameReply

        paths.ensure_dirs()
        self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self.iface = DaemonInterface(self)
        self.bus.export(OBJ_PATH, self.iface)
        reply = await self.bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE)
        if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
            log.error("another wallpaper-picker daemon is already running")
            self.bus.disconnect()
            return 1
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            loop.add_signal_handler(sig, self.stop_event.set)
        log.info("daemon started (pid %d)", os.getpid())
        await self.apply("startup")
        watcher = WinbarWatcher(self.bus, self.controller)
        try:
            await watcher.start()
        except Exception as e:  # noqa: BLE001
            log.warning("winbar watcher unavailable: %s", e)
        tasks = [asyncio.ensure_future(self.monitor_loop()), asyncio.ensure_future(self.status_loop())]
        await self.stop_event.wait()
        for t in tasks:
            t.cancel()
        await self.shutdown()
        self.bus.disconnect()
        return 0


from dbus_fast.service import ServiceInterface, method, signal as dbus_signal  # noqa: E402


class DaemonInterface(ServiceInterface):
    def __init__(self, daemon: Daemon) -> None:
        super().__init__(IFACE)
        self.daemon = daemon

    @method()
    async def Apply(self) -> "s":  # noqa: N802,F821
        return json.dumps(await self.daemon.apply("dbus"), ensure_ascii=False)

    @method()
    async def Stop(self) -> "s":  # noqa: N802,F821
        return json.dumps(await self.daemon.stop_all(), ensure_ascii=False)

    @method()
    def Status(self) -> "s":  # noqa: N802,F821
        return json.dumps(self.daemon.status(), ensure_ascii=False)

    @method()
    def Quit(self) -> "s":  # noqa: N802,F821
        self.daemon.stop_event.set()
        return "bye"

    @dbus_signal()
    def StatusChanged(self, status: "s") -> "s":  # noqa: N802,F821
        return status


def main(verbose: bool = False) -> int:
    setup_logging(verbose)
    try:
        return asyncio.run(Daemon().run())
    except KeyboardInterrupt:
        return 0
