"""Command line entry point.

    wallpaper-picker                         open the desktop UI
    wallpaper-picker --daemon                run the background daemon
    wallpaper-picker --apply                 (re)apply the saved config via the daemon
    wallpaper-picker list                    list installed wallpapers
    wallpaper-picker set <id|title> [target] [--fps N] [--scaling M] [--clamp C] [-p k=v]
    wallpaper-picker clear <left|right|span|both>
    wallpaper-picker stop                    stop the wallpaper engine
    wallpaper-picker status                  show what runs where

For the desktop app (and scripts): every command above takes ``--json`` and then prints
one JSON object on stdout, with no prose. The app-facing commands are:

    wallpaper-picker state                   everything the UI needs to draw its first frame
    wallpaper-picker monitors                attached outputs
    wallpaper-picker wallpapers              installed wallpapers (the library)
    wallpaper-picker options                 compatibility + startup settings
    wallpaper-picker options --set k=v ...   change those settings
    wallpaper-picker browse ...              search the Steam Workshop
    wallpaper-picker install <id>            fetch a workshop item with steamcmd
    wallpaper-picker login-status            whether steamcmd has a usable login
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from . import config, workshop  # build_parser() needs these for its --sort/
                                        # --clamp choices


def emit(payload: dict, as_json: bool) -> int:
    """Print a result as JSON (for the app) or return it for the text path."""
    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0 if payload.get("ok", True) else 1


def _fail(message: str, as_json: bool, code: int = 2) -> int:
    if as_json:
        json.dump({"ok": False, "message": message}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(message, file=sys.stderr)
    return code


def cmd_state(args: argparse.Namespace) -> int:
    """One call that gives the UI its whole first frame."""
    from . import client, config, monitors, power, workshop

    cfg = config.load()
    mons = monitors.list_monitors()
    st = client.status()
    lib = workshop.scan()
    assignments = {}
    for key, slot, outs in cfg.active_assignments(mons):
        for out in outs:
            assignments[out] = {"slot": key, "wallpaper": slot.wallpaper,
                                "settings": slot.settings()}
    payload = {
        "ok": True,
        "enabled": cfg.enabled,
        "monitors": [m.to_dict() for m in mons],
        "assignments": assignments,
        "status": st,
        "power": st.get("power") or power.read_power().to_dict(),
        "options": asdict(cfg.options),
        "counts": {"installed": len(lib),
                   "playable": sum(1 for w in lib if w.playable)},
        "daemon_running": bool(st.get("daemon_pid")),
        "engine_available": bool(config.engine_path() if hasattr(config, "engine_path") else True),
    }
    if args.json:
        return emit(payload, True)
    print(f"daemon: {'running' if payload['daemon_running'] else 'stopped'}"
          f"   enabled: {cfg.enabled}   wallpapers: {payload['counts']['playable']}")
    return 0


def cmd_monitors(args: argparse.Namespace) -> int:
    from . import monitors

    mons = monitors.list_monitors()
    payload = {"ok": True, "monitors": [m.to_dict() for m in mons]}
    if args.json:
        return emit(payload, True)
    for m in mons:
        print(f"{m.name:<14} {m.width}x{m.height} {m.position.lower():<7} enabled={m.enabled}")
    return 0


def cmd_wallpapers(args: argparse.Namespace) -> int:
    from . import config, monitors, workshop

    cfg = config.load()
    mons = monitors.list_monitors()
    where: dict[str, list[str]] = {}
    for key, slot, outs in cfg.active_assignments(mons):
        label = "span" if key == config.SPAN else next(
            (m.position.lower() or m.name for m in mons if m.name == key), key)
        for out in outs:
            where.setdefault(slot.wallpaper or "", []).append(label)
    wps = workshop.scan()
    query = getattr(args, "search", None)
    if query:
        # fuzzy: rank by score, and a term that matches nothing rules the wallpaper out
        ranked = [(workshop.fuzzy_score(f"{w.title} {' '.join(w.tags)} {w.id}", query), w)
                  for w in wps]
        wps = [w for score, w in sorted(ranked, key=lambda p: (-p[0], p[1].title.lower()))
               if score > 0]
    else:
        wps = workshop.sort_wallpapers(wps, getattr(args, "sort", "name"))

    items = []
    for w in wps:
        d = w.to_dict() if hasattr(w, "to_dict") else {}
        d.setdefault("id", w.id)
        d.setdefault("title", w.title)
        d["type"] = w.type
        d["error"] = w.error
        d["playable"] = w.playable
        d["is_preset"] = w.is_preset
        d["dependency"] = w.dependency
        d["on"] = where.get(w.id, [])
        d["source"] = w.source
        d["tags"] = list(w.tags)
        d["description"] = w.description
        # absolute local path; the app serves it to the renderer through its own protocol
        d["preview"] = str(w.preview) if w.preview and Path(w.preview).is_file() else None
        d["properties"] = [
            {"key": pp.key, "label": pp.label or pp.key, "type": pp.type,
             "default": pp.default, "min": pp.min, "max": pp.max, "step": pp.step,
             "options": [{"value": v, "label": l} for v, l in pp.options],
             "editable": pp.editable, "condition": pp.condition,
             "order": pp.order, "heading": pp.heading}
            for pp in w.properties
        ]
        items.append(d)
    payload = {"ok": True, "wallpapers": items}
    if args.json:
        return emit(payload, True)
    for it in items:
        print(f"{it['id']:<14} {it['title']}")
    return 0


def core_command() -> list[str]:
    """How to invoke the Python core again — the frozen sidecar, or the installed CLI.

    Used for the autostart entry so the daemon starts the same way the app does.
    """
    import shutil
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = shutil.which("wallpaper-picker")
    if exe:
        return [exe]
    return [sys.executable, "-m", "wallpaper_picker"]


def sync_autostart(cfg: config_mod.Config) -> str | None:
    """Write or remove the login entry to match the config. Returns a note, if any."""
    from . import autostart

    opts = cfg.options
    try:
        if not opts.start_on_login:
            autostart.apply("", 0, False)
            return "start-at-login disabled"
        argv = core_command()
        if len(argv) == 1:
            exec_line = argv[0]
        else:
            exec_line = " ".join(argv)
        autostart.apply(exec_line, opts.startup_delay, True)
        return f"login entry: {exec_line} (delay {opts.startup_delay}s)"
    except OSError as e:
        return f"could not write the login entry: {e}"


def cmd_vocab(args: argparse.Namespace) -> int:
    """The static Workshop vocabulary: tags (grouped), types and sorts.

    Reading this costs nothing -- it is the vocabulary, not a query -- so a front end can
    build its filters at startup instead of waiting for the user's first search.
    """
    from . import steam

    payload = {
        "ok": True,
        "tags": steam.TAG_SUGGESTIONS,
        "tag_groups": steam.TAG_GROUPS,
        "types": list(steam.FILETYPES),
        "sorts": list(steam.SORTS),
    }
    if args.json:
        return emit(payload, True)
    for group, tags in steam.TAG_GROUPS.items():
        print(f"{group}: " + ", ".join(tags))
    print("types: " + ", ".join(steam.FILETYPES))
    print("sorts: " + ", ".join(steam.SORTS))
    return 0


def cmd_options(args: argparse.Namespace) -> int:
    from . import config

    cfg = config.load()
    startup_changed = False
    if args.set:
        updates = {}
        for kv in args.set:
            k, _, v = kv.partition("=")
            k = k.strip()
            if k not in asdict(config.Options()):
                return _fail(f"unknown option: {k}", args.json)
            if v.lower() in ("true", "false"):
                updates[k] = v.lower() == "true"
            elif v.lower() in ("on", "off"):
                updates[k] = v.lower() == "on"
            elif "," in v:
                updates[k] = [x for x in v.split(",") if x]
            else:
                updates[k] = v
        merged = asdict(cfg.options) | updates
        cfg.options = config.Options.from_dict(merged)
        config.save(cfg)
        startup_changed = bool({"start_on_login", "startup_delay"} & set(updates))
        if cfg.enabled:
            from . import client
            try:
                client.apply()
            except client.DaemonError:
                pass
    payload = {"ok": True, "options": asdict(cfg.options)}
    if startup_changed:
        note = sync_autostart(cfg)
        if note:
            payload["autostart"] = note
    if args.json:
        return emit(payload, True)
    for k, v in payload["options"].items():
        print(f"{k} = {v}")
    if payload.get("autostart"):
        print(payload["autostart"])
    return 0


def cmd_autostart(args: argparse.Namespace) -> int:
    """Inspect or rewrite the start-at-login entry."""
    from . import autostart, config

    cfg = config.load()
    if args.enable is not None or args.delay is not None:
        if args.enable is not None:
            cfg.options.start_on_login = args.enable.lower() in ("true", "on", "1", "yes")
        if args.delay is not None:
            cfg.options.startup_delay = max(0, min(300, int(args.delay)))
        config.save(cfg)
        note = sync_autostart(cfg)
    else:
        note = None
    path = autostart.autostart_path()
    payload = {
        "ok": True,
        "enabled": autostart.is_enabled(),
        "path": str(path),
        "delay": config.load().options.startup_delay,
        "note": note,
    }
    if args.json:
        return emit(payload, True)
    print(f"start at login: {'yes' if payload['enabled'] else 'no'}")
    print(f"entry: {payload['path']}")
    if note:
        print(note)
    return 0


def cmd_browse(args: argparse.Namespace) -> int:
    """Search the Steam Workshop through the official Web API."""
    from . import steam

    key = args.key or config_api_key()
    if not key:
        return _fail("No Steam Web API key set. Add one in Settings "
                     "(free: steamcommunity.com/dev/apikey).", args.json, 3)
    try:
        if args.search:
            # Ranked search: Steam's own ordering is by popularity, so a title match can sit
            # well below items that merely mention the term in their description.
            items, total = steam.search_items(
                key, args.search, sort=args.sort, filetype=args.type,
                tags=args.tag or [])
        else:
            items, total = steam.query(
                key, sort=args.sort, page=args.page, filetype=args.type,
                tags=args.tag or [])
    except steam.SteamError as e:
        return _fail(str(e), args.json)
    installed = set(steam.installed_item_dirs())
    payload = {
        "ok": True,
        "total": total,
        "page": args.page,
        "sort": args.sort,
        "items": [i.to_dict() | {"installed": i.id in installed} for i in items],
        "tags": steam.TAG_SUGGESTIONS,
        "tag_groups": steam.TAG_GROUPS,
        "sorts": list(steam.SORTS),
        "types": list(steam.FILETYPES),
        "type": args.type,
    }
    if args.json:
        return emit(payload, True)
    for it in payload["items"]:
        mark = "*" if it["installed"] else " "
        print(f"{mark} {it['id']:<12} {it['title'][:52]:<52} {it['subscriptions']:>9,} subs")
    print(f"page {args.page} of ~{-(-total // steam.PAGE_SIZE)} ({total} items)")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Fetch a workshop item with steamcmd, into the Steam library the engine reads."""
    from . import steam

    libs = steam.steam_libraries()
    if not libs:
        return _fail("No Steam library found (is Steam installed?).", args.json)
    target = libs[0]
    account, _guard = steam.login_state(target)
    if args.username is None:
        args.username = config_saved_account()
    if not account and not args.username:
        return _fail("steamcmd needs a Steam login for Wallpaper Engine items. "
                     "Set your Steam account name in Settings.", args.json, 3)
    try:
        res = steam.download(
            args.id, target,
            username=args.username or account,
            password=args.password,
            guard_code=args.guard_code,
        )
    except steam.SteamError as e:
        return _fail(str(e), args.json)
    out = res.to_dict() | {"ok": res.ok, "id": args.id, "library": str(target.parent)}
    if res.ok:
        out["message"] = "Downloaded."
    elif out["needs_login"]:
        out["message"] = steam.login_hint()
    elif out["needs_guard"]:
        out["message"] = "Steam asked for a Steam Guard code (see the login instructions)."
    else:
        out["message"] = steam.failure_reason(res.output)
    if args.json:
        return emit(out, True)
    print(out["message"])
    return 0 if res.ok else 1


def cmd_login_status(args: argparse.Namespace) -> int:
    """What the app needs to know before offering an in-app install."""
    from . import steam

    libs = steam.steam_libraries()
    exe = steam.find_steamcmd()
    account = None
    guard = False
    if libs:
        account, guard = steam.login_state(libs[0])
    # A real probe: steamcmd logs in with cached credentials, or tells us it cannot.
    logged_in, _probe = (steam.probe_login(exe, account or "anonymous")
                         if exe else (False, ""))
    payload = {
        "ok": True,
        "steamcmd": str(exe) if exe else None,
        "steamcmd_installed": exe is not None,
        "logged_in": logged_in,
        "login_hint": steam.login_hint(exe),
        "broken_steamcmd": [str(b) for b in steam.broken_steamcmd_paths()],
        "libraries": [str(p.parent) for p in libs],
        "account": account,
        "needs_guard": guard,
        "installed_items": len(steam.installed_item_dirs()),
        "ready": bool(exe and libs and logged_in),
    }
    if args.json:
        return emit(payload, True)
    print(f"steamcmd: {payload['steamcmd'] or 'not installed'}")
    for bad in payload["broken_steamcmd"]:
        print(f"  (ignoring {bad}: it cannot find its own client, so it will not run)")
    print(f"account:  {account or 'unknown'}")
    print(f"logged in to steamcmd: {'yes' if payload['logged_in'] else 'no'}")
    print(f"items:    {payload['installed_items']}")
    if exe and not payload["logged_in"]:
        print()
        print(payload["login_hint"])
    return 0


def cmd_save_steam(args: argparse.Namespace) -> int:
    """Store the Steam Web API key / account name in the user's config file.

    These never go into the app bundle: the key is personal to the user's Steam account.
    """
    from . import config

    cfg = config.load()
    if args.key is not None:
        cfg.steam_api_key = args.key.strip()
    if args.account is not None:
        cfg.steam_account = args.account.strip()
    config.save(cfg)
    payload = {
        "ok": True,
        "has_key": bool(cfg.steam_api_key),
        "account": cfg.steam_account,
        "message": "Saved.",
    }
    if args.json:
        return emit(payload, True)
    print(payload["message"])
    return 0


def config_saved_account() -> str:
    """The Steam account name the user saved in Settings."""
    from . import config

    return getattr(config.load(), "steam_account", "") or ""


def config_api_key() -> str:
    """The Steam Web API key, from the config file (never bundled into the app)."""
    from . import config

    return getattr(config.load(), "steam_api_key", "") or ""


def _print_result(res: dict) -> int:
    msg = res.get("message") or ""
    ok = res.get("ok", True)
    for e in (res.get("status") or {}).get("engines", []):
        titles = ", ".join(f"{u['title']} [{u['wallpaper']}]" for u in e.get("units", []))
        print(f"  {'+'.join(e['outputs']):<20} {e['state']:<10} pid {e['pid']:<8} {titles}")
    if res.get("took_over"):
        print(f"  took over from pid(s): {', '.join(map(str, res['took_over']))}")
    print(("OK: " if ok else "Problem: ") + msg)
    return 0 if ok else 1


def cmd_list(args: argparse.Namespace) -> int:
    from . import config, monitors, workshop

    cfg = config.load()
    mons = monitors.list_monitors()
    where: dict[str, list[str]] = {}
    for key, slot, outs in cfg.active_assignments(mons):
        label = "span" if key == config.SPAN else next((m.position.lower() or m.name for m in mons if m.name == key), key)
        where.setdefault(slot.wallpaper or "", []).append(label)
    wps = sorted(workshop.scan(), key=lambda w: (w.source != "workshop", w.title.lower()))
    for w in wps:
        if args.type and w.type != args.type:
            continue
        flags = []
        if w.is_preset:
            flags.append(f"preset of {w.dependency}")
        if w.error:
            flags.append(w.error)
        on = f"  <- {', '.join(where[w.id])}" if w.id in where else ""
        extra = f"  ({'; '.join(flags)})" if flags else ""
        print(f"{w.id:<28} {w.type:<7} {w.title}{extra}{on}")
    return 0


def _action_payload(res: dict, **extra: object) -> dict:
    """The app-facing shape for an action: the daemon's own result plus context."""
    payload = {"ok": bool(res.get("ok", True)), "message": res.get("message") or ""}
    for key in ("status", "took_over"):
        if key in res:
            payload[key] = res[key]
    payload.update(extra)
    if not payload["message"]:
        payload["message"] = "Done."
    return payload


def cmd_set(args: argparse.Namespace) -> int:
    from . import client, config, monitors, workshop

    wps = workshop.scan()
    wp = workshop.find_wallpaper(wps, args.wallpaper)
    if wp is None:
        return _fail(f"No single wallpaper matches {args.wallpaper!r}; "
                     "try the Installed tab or `wallpaper-picker list`.", args.json, 2)
    if not wp.playable:
        return _fail(f"{wp.title}: {wp.error or 'cannot be played'}", args.json, 2)
    mons = monitors.list_monitors()
    cfg = config.load()
    try:
        targets = config.resolve_target(args.target, mons)
    except ValueError as e:
        return _fail(str(e), args.json, 2)
    settings = {}
    if args.fps:
        settings["fps"] = args.fps
    if args.scaling:
        settings["scaling"] = args.scaling
    if args.clamp:
        settings["clamp"] = args.clamp
    for flag in ("mouse", "parallax", "particles"):
        v = getattr(args, flag)
        if v is not None:
            settings[flag] = v == "on"
    cfg.assign(targets, wp.id, settings, mons)
    if args.property:
        for t in targets:
            ov = cfg.overrides(t, wp.id)
            for kv in args.property:
                k, _, v = kv.partition("=")
                p = wp.prop(k)
                if p is None or not p.editable:
                    return _fail(f"{wp.title} has no editable property {k!r}", args.json, 2)
                ov[k] = p.normalize(v)
            cfg.set_overrides(t, wp.id, ov)
    cfg.enabled = True
    config.save(cfg)
    try:
        res = client.apply()
    except client.DaemonError as e:
        return _fail(f"the wallpaper daemon could not be reached: {e}", args.json, 1)
    if args.json:
        return emit(_action_payload(res, wallpaper=wp.id, title=wp.title, targets=targets), True)
    print(f"{wp.title} -> {args.target}")
    return _print_result(res)


def cmd_clear(args: argparse.Namespace) -> int:
    from . import client, config, monitors

    mons = monitors.list_monitors()
    cfg = config.load()
    try:
        targets = config.resolve_target(args.target, mons)
    except ValueError as e:
        return _fail(str(e), args.json, 2)
    for t in targets:
        cfg.clear(t)
        if t != config.SPAN and cfg.span_active and t in cfg.slots[config.SPAN].outputs:
            cfg.clear(config.SPAN)
    config.save(cfg)
    try:
        res = client.apply()
    except client.DaemonError as e:
        return _fail(f"the wallpaper daemon could not be reached: {e}", args.json, 1)
    if args.json:
        return emit(_action_payload(res, targets=targets), True)
    return _print_result(res)


def cmd_stop(args: argparse.Namespace) -> int:
    from . import client, config, procs

    cfg = config.load()
    cfg.enabled = False
    config.save(cfg)
    if client.daemon_running():
        try:
            res = client.stop()
        except client.DaemonError as e:
            return _fail(f"the wallpaper daemon could not be reached: {e}", args.json, 1)
        if args.json:
            return emit(_action_payload(res), True)
        return _print_result(res)
    stopped = [pid for pid, _ in procs.find_screen_engines() if procs.terminate(pid, procs.starttime(pid))]
    message = (f"Stopped {len(stopped)} engine process(es)." if stopped
               else "No wallpaper engine was running.")
    if args.json:
        return emit({"ok": True, "stopped": len(stopped), "message": message}, True)
    print(message)
    return 0


def cmd_apply(_args: argparse.Namespace | None = None) -> int:
    from . import client, config

    as_json = bool(getattr(_args, "json", False)) if _args is not None else False
    cfg = config.load()
    if not cfg.enabled:
        cfg.enabled = True
        config.save(cfg)
    try:
        res = client.apply()
    except client.DaemonError as e:
        return _fail(f"the wallpaper daemon could not be reached: {e}", as_json, 1)
    if as_json:
        return emit(_action_payload(res), True)
    return _print_result(res)


def cmd_status(_args: argparse.Namespace) -> int:
    from . import client

    st = client.status()
    print(f"daemon: {'pid ' + str(st['daemon_pid']) if st.get('daemon_pid') else 'not running'}"
          f"   winbar: {'yes' if st.get('winbar') else 'no'}   enabled: {st.get('enabled')}")
    if st.get("fullscreen"):
        print(f"full-screen: {', '.join(st['fullscreen'])}")
    for e in st.get("engines", []):
        titles = ", ".join(u["title"] for u in e.get("units", []))
        print(f"  {'+'.join(e['outputs']):<20} {e['state']:<10} pid {e['pid']:<8} "
              f"cpu {e.get('cpu_percent', 0):5.1f}%  mem {e.get('mem_mb', 0):6.1f} MB  {titles}")
        for err in e.get("errors", [])[:5]:
            print(f"      ! {err}")
    for p in st.get("problems", []):
        print(f"  ! {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The full argument parser.

    Exposed as its own function so tests can assert the CLI's contract directly -- in
    particular that every command the desktop app calls accepts --json. That contract was
    silently broken once (set/clear/stop/apply lacked the flag), so it is now a test.
    """
    p = argparse.ArgumentParser(prog="wallpaper-picker", description="Wallpaper Engine picker for linux-wallpaperengine")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="list installed wallpapers")
    pl.add_argument("--type", choices=["video", "scene", "web", "unknown"])
    pl.set_defaults(func=cmd_list)
    ps = sub.add_parser("set", help="put a wallpaper on a monitor")
    ps.add_argument("wallpaper", help="workshop id or (part of) the title")
    ps.add_argument("target", nargs="?", default="both", help="left, right, both, span or an output name (default: both)")
    ps.add_argument("--fps", type=int)
    ps.add_argument("--scaling", choices=["fill", "fit", "stretch", "default"])
    ps.add_argument("--clamp", choices=list(config.CLAMP_MODES),
                    help="what the engine does past the wallpaper's edges")
    ps.add_argument("--mouse", choices=["on", "off"])
    ps.add_argument("--parallax", choices=["on", "off"])
    ps.add_argument("--particles", choices=["on", "off"])
    ps.add_argument("-p", "--property", action="append", metavar="KEY=VALUE", help="wallpaper property override")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_set)
    pc = sub.add_parser("clear", help="remove the wallpaper from a monitor")
    pc.add_argument("target")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=cmd_clear)

    psp = sub.add_parser("stop", help="stop the wallpaper engine")
    psp.add_argument("--json", action="store_true")
    psp.set_defaults(func=cmd_stop)

    pstt = sub.add_parser("status", help="what runs where")
    pstt.add_argument("--json", action="store_true")
    pstt.set_defaults(func=cmd_status)

    pap = sub.add_parser("apply", help="same as --apply")
    pap.add_argument("--json", action="store_true")
    pap.set_defaults(func=cmd_apply)

    pst = sub.add_parser("state", help="everything the desktop app needs to start")
    pst.add_argument("--json", action="store_true")
    pst.set_defaults(func=cmd_state)

    pm = sub.add_parser("monitors", help="attached outputs")
    pm.add_argument("--json", action="store_true")
    pm.set_defaults(func=cmd_monitors)

    pw = sub.add_parser("wallpapers", help="installed wallpapers")
    pw.add_argument("--json", action="store_true")
    pw.add_argument("--search", help="fuzzy filter over title, tags and id")
    pw.add_argument("--sort", choices=list(workshop.SORTS), default="name",
                    help="order of the results")
    pw.set_defaults(func=cmd_wallpapers)

    po = sub.add_parser("options", help="compatibility and startup settings")
    po.add_argument("--json", action="store_true")
    po.add_argument("--set", action="append", metavar="KEY=VALUE")
    po.set_defaults(func=cmd_options)

    pb = sub.add_parser("browse", help="search the Steam Workshop")
    pb.add_argument("--json", action="store_true")
    pb.add_argument("--sort", default="trend", choices=["trend", "popular", "recent", "updated"])
    pb.add_argument("--page", type=int, default=1)
    pb.add_argument("--search", default="")
    pb.add_argument("--tag", action="append",
                    help="require a workshop tag, repeatable, e.g. --tag Anime --tag Video")
    pb.add_argument("--type", default="all", choices=["all", "scene", "video", "web",
                                                      "application"],
                    help="only wallpapers of this kind")
    pb.add_argument("--all-pages", action="store_true",
                    help="search more pages (slower, finds better title matches)")
    pb.add_argument("--key", help="Steam Web API key (else the saved one)")
    pb.set_defaults(func=cmd_browse)

    pv = sub.add_parser("vocab", help="the workshop tag, type and sort vocabulary")
    pv.add_argument("--json", action="store_true")
    pv.set_defaults(func=cmd_vocab)

    pi = sub.add_parser("install", help="download a workshop item with steamcmd")
    pi.add_argument("id")
    pi.add_argument("--json", action="store_true")
    pi.add_argument("--username")
    pi.add_argument("--password")
    pi.add_argument("--guard-code")
    pi.set_defaults(func=cmd_install)

    pas = sub.add_parser("autostart", help="start-at-login entry")
    pas.add_argument("--json", action="store_true")
    pas.add_argument("--enable", help="true/false")
    pas.add_argument("--delay", help="seconds after login")
    pas.set_defaults(func=cmd_autostart)

    pss = sub.add_parser("save-steam", help="store the Steam API key and account")
    pss.add_argument("--json", action="store_true")
    pss.add_argument("--key")
    pss.add_argument("--account")
    pss.set_defaults(func=cmd_save_steam)

    pls = sub.add_parser("login-status", help="steamcmd/login readiness")
    pls.add_argument("--json", action="store_true")
    pls.set_defaults(func=cmd_login_status)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if "--daemon" in argv:
        from .daemon import main as daemon_main
        return daemon_main(verbose="-v" in argv or "--verbose" in argv)
    if "--apply" in argv:
        return cmd_apply()
    if not argv or argv[0] in ("gui", "--gui"):
        try:
            from .gui.app import main as gui_main
        except ImportError as exc:
            print(f"the Qt interface needs PySide6 ({exc}).", file=sys.stderr)
            print("  pip install 'wallpaper-picker[gui]'   # or just use the CLI",
                  file=sys.stderr)
            return 1
        return gui_main()

    p = build_parser()
    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        from .client import DaemonError
        if isinstance(e, DaemonError):
            print(f"Error: {e}", file=sys.stderr)
            return 1
        raise
