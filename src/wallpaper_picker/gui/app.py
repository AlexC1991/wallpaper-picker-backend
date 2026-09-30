"""Main window."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .. import APP_ID, config, monitors, paths, procs, workshop
from ..config import SPAN
from . import theme
from .panel import SidePanel
from .thumbs import ThumbCache
from .tiles import WallpaperGrid
from .widgets import AnimatedPreview, ElidedLabel


class Worker(QThread):
    done = Signal(object, object)  # result, error

    def __init__(self, fn: Callable[[], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn(), None)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, e)


class SetupCard(QFrame):
    clicked = Signal(str, str)  # wallpaper id, target
    clearRequested = Signal(str)

    def __init__(self, slot_key: str, heading: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.slot_key = slot_key
        self.wid: str | None = None
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 12, 10)
        lay.setSpacing(12)
        self.thumb = AnimatedPreview(radius=7)
        self.thumb.setMinimumHeight(0)
        self.thumb.setFixedSize(QSize(112, 63))
        lay.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.heading = QLabel(heading)
        self.heading.setObjectName("Section")
        self.title = ElidedLabel("")
        self.title.setStyleSheet("font-weight: 600;")
        self.state = QLabel("")
        self.state.setObjectName("Faint")
        self.state.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(self.heading)
        col.addWidget(self.title)
        col.addWidget(self.state)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("Danger")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setToolTip("Remove the wallpaper from this monitor")
        self.clear_btn.clicked.connect(lambda: self.clearRequested.emit(self.slot_key))
        lay.addWidget(self.clear_btn, 0, Qt.AlignVCenter)

    def set_content(self, wp: workshop.Wallpaper | None, still: QImage | None, state_html: str, full_title: str = "") -> None:
        self.wid = wp.id if wp else None
        if wp is None:
            self.title.setText("No wallpaper")
            self.title.setStyleSheet(f"font-weight: 600; color: {theme.MUTED};")
            self.thumb.set_source(None)
        else:
            self.title.setText(wp.title)
            self.title.setToolTip(full_title or wp.title)
            self.title.setStyleSheet("font-weight: 600;")
            self.thumb.set_source(None, still)
        self.state.setText(state_html)
        self.clear_btn.setVisible(wp is not None)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton and self.wid:
            self.clicked.emit(self.wid, self.slot_key)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wallpaper Picker")
        self.resize(1440, 900)
        self.thumbs = ThumbCache(self)
        self.wallpapers: list[workshop.Wallpaper] = []
        self.by_id: dict[str, workshop.Wallpaper] = {}
        self.monitors: list[monitors.Monitor] = []
        self.cfg = config.load()
        self.status: dict = {}
        self.workers: list[Worker] = []
        self.type_filter = "all"

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ---- current setup strip
        strip = QFrame()
        strip.setObjectName("Strip")
        sl = QVBoxLayout(strip)
        sl.setContentsMargins(20, 14, 20, 14)
        sl.setSpacing(10)
        top = QHBoxLayout()
        name = QLabel("Wallpaper Picker")
        name.setObjectName("AppTitle")
        top.addWidget(name)
        top.addSpacing(12)
        cur = QLabel("CURRENT SETUP")
        cur.setObjectName("Section")
        top.addWidget(cur, 0, Qt.AlignBottom)
        top.addStretch(1)
        self.daemon_label = QLabel("")
        self.daemon_label.setObjectName("Faint")
        self.daemon_label.setTextFormat(Qt.RichText)
        top.addWidget(self.daemon_label)
        self.power_btn = QPushButton("Stop")
        self.power_btn.setObjectName("Ghost")
        self.power_btn.setCursor(Qt.PointingHandCursor)
        self.power_btn.clicked.connect(self._toggle_power)
        top.addWidget(self.power_btn)
        sl.addLayout(top)
        self.cards_row = QHBoxLayout()
        self.cards_row.setSpacing(12)
        sl.addLayout(self.cards_row)
        self.cards: dict[str, SetupCard] = {}
        v.addWidget(strip)

        # ---- toolbar
        bar = QHBoxLayout()
        bar.setContentsMargins(20, 12, 20, 0)
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search wallpapers…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        bar.addWidget(self.search, 2)
        bar.addSpacing(8)
        self.chips = QButtonGroup(self)
        for value, label in (("all", "All"), ("video", "Video"), ("scene", "Scene"), ("web", "Web")):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("value", value)
            b.setChecked(value == "all")
            self.chips.addButton(b)
            bar.addWidget(b)
        self.chips.buttonClicked.connect(self._chip)
        bar.addStretch(1)
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        refresh = QPushButton("Rescan")
        refresh.setObjectName("Ghost")
        refresh.setToolTip("Look for new or updated wallpapers")
        refresh.clicked.connect(self.reload_library)
        bar.addWidget(refresh)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        left.addLayout(bar)
        self.grid = WallpaperGrid(self.thumbs)
        self.grid.selected.connect(self._selected)
        self.grid.activated.connect(self._activated)
        left.addWidget(self.grid, 1)
        body.addLayout(left, 1)
        self.panel = SidePanel()
        self.panel.setFixedWidth(400)
        self.panel.applyRequested.connect(self._apply)
        body.addWidget(self.panel)
        v.addLayout(body, 1)

        self.thumbs.ready.connect(self._thumb_ready)
        QShortcut(QKeySequence.Find, self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence("Escape"), self.search, activated=self.search.clear)

        self.status_timer = QTimer(self, interval=2000, timeout=self.poll_status)
        self.monitor_timer = QTimer(self, interval=5000, timeout=self._check_monitors)
        self.reload_monitors()
        self.reload_library()
        self.poll_status()
        self.status_timer.start()
        self.monitor_timer.start()
        self._select_initial()

    # ------------------------------------------------------------------ data
    def reload_library(self) -> None:
        self.wallpapers = sorted(workshop.scan(),
                                 key=lambda w: (not w.playable, w.source != "workshop", w.title.lower()))
        self.by_id = {w.id: w for w in self.wallpapers}
        self.grid.set_wallpapers(self.wallpapers)
        self._filter()
        self._update_markers()
        self._rebuild_cards()

    def reload_monitors(self) -> None:
        mons = monitors.list_monitors()
        if mons:
            self.monitors = mons
        self.panel.set_context(self.monitors, self.cfg)
        self._rebuild_cards()

    def _check_monitors(self) -> None:
        mons = monitors.list_monitors()
        if mons and monitors.signature(mons) != monitors.signature(self.monitors):
            self.reload_monitors()

    def _select_initial(self) -> None:
        for key, slot, _outs in self.cfg.active_assignments(self.monitors):
            if slot.wallpaper in self.by_id:
                target = key if key != SPAN else SPAN
                self.panel.target = target
                self.grid.select(slot.wallpaper)
                return
        if self.grid.visible_ids:
            self.grid.select(self.grid.visible_ids[0])

    # ------------------------------------------------------------------ filtering
    def _chip(self, b: QPushButton) -> None:
        self.type_filter = b.property("value")
        self._filter()

    def _filter(self, *_a: Any) -> None:
        n = self.grid.apply_filter(self.search.text(), self.type_filter)
        total = len(self.wallpapers)
        self.count.setText(f"{n} of {total}" if n != total else f"{total} wallpapers")

    # ------------------------------------------------------------------ selection
    def _selected(self, wid: str) -> None:
        wp = self.by_id.get(wid)
        still = self.thumbs.get(wid, wp.preview if wp else None)
        self.panel.show_wallpaper(wp, still)

    def _activated(self, wid: str) -> None:
        """Double-click: apply straight away to the chosen monitor(s)."""
        if self.panel.wp and self.panel.wp.id == wid and self.panel.wp.playable:
            self.panel._apply()

    def _thumb_ready(self, key: str, img: QImage) -> None:
        for card in self.cards.values():
            if card.wid == key:
                self._rebuild_cards()
                break

    # ------------------------------------------------------------------ apply
    def _run(self, fn: Callable[[], Any], done: Callable[[Any, Any], None]) -> None:
        w = Worker(fn, self)
        self.workers.append(w)

        def finished(res: Any, err: Any) -> None:
            self.workers.remove(w)
            w.deleteLater()
            done(res, err)

        w.done.connect(finished)
        w.start()

    def _apply(self, wid: str, target: str, settings: dict, overrides: dict) -> None:
        cfg = config.load()
        slots = [m.name for m in self.monitors] if target == "both" else [target]
        cfg.assign(slots, wid, settings, self.monitors)
        for s in slots:
            cfg.set_overrides(s, wid, overrides)
        cfg.enabled = True
        config.save(cfg)
        self.cfg = cfg
        self.panel.cfg = cfg
        self._update_markers()
        self.panel.set_busy(True)
        self._apply_now()

    def _apply_now(self, after: Callable[[], None] | None = None) -> None:
        from .. import client

        def done(res: Any, err: Any) -> None:
            self.panel.set_busy(False)
            if err is not None:
                self.panel.show_result(False, f"Could not apply: {err}")
            else:
                ok = bool(res.get("ok"))
                msg = res.get("message") or ""
                if ok:
                    started = res.get("started") or []
                    msg = "Applied." if started else "Applied (nothing needed restarting)."
                    if res.get("took_over"):
                        msg += f" Replaced engine pid {', '.join(map(str, res['took_over']))}."
                    warn = [w for e in (res.get("status") or {}).get("engines", []) for w in e.get("warnings", [])]
                    if warn:
                        msg += "\nEngine warnings:\n• " + "\n• ".join(warn[:4])
                self.panel.show_result(ok, msg)
                if res.get("status"):
                    self._set_status(res["status"])
            if after:
                after()

        self._run(client.apply, done)

    def _clear(self, slot: str) -> None:
        cfg = config.load()
        cfg.clear(slot)
        if slot != SPAN and cfg.span_active and slot in cfg.slots[SPAN].outputs:
            cfg.clear(SPAN)
        config.save(cfg)
        self.cfg = cfg
        self.panel.cfg = cfg
        self._update_markers()
        self._rebuild_cards()
        self._apply_now()

    def _toggle_power(self) -> None:
        from .. import client

        cfg = config.load()
        if cfg.enabled:
            cfg.enabled = False
            config.save(cfg)
            self.cfg = cfg

            def stop() -> Any:
                if client.daemon_running():
                    return client.stop()
                for pid, _ in procs.find_screen_engines():
                    procs.terminate(pid, procs.starttime(pid))
                return {"ok": True}

            self._run(stop, lambda res, err: self.poll_status())
        else:
            cfg.enabled = True
            config.save(cfg)
            self.cfg = cfg
            self._apply_now()
        self._update_power()

    # ------------------------------------------------------------------ status
    def poll_status(self) -> None:
        try:
            st = json.loads(paths.status_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        pid = st.get("daemon_pid")
        if not pid or not procs.alive(int(pid)):
            st["daemon_pid"] = None
        self._set_status(st)

    def _set_status(self, st: dict) -> None:
        self.status = st
        self.cfg = config.load()
        self.panel.cfg = self.cfg
        if st.get("daemon_pid"):
            pause = ("full-screen pause on" if st.get("winbar") else
                     "full-screen pause off (winbar not running)")
            dot = theme.OK
            txt = f"Daemon running · {pause}"
            if st.get("paused"):
                txt = f"Paused while “{', '.join(st.get('fullscreen') or [])}” is full screen"
                dot = theme.WARN
        else:
            dot, txt = theme.FAINT, "Daemon not running (starts on Apply)"
        self.daemon_label.setText(f"<span style='color:{dot}'>●</span> {txt}")
        self._update_power()
        self._update_markers()
        self._rebuild_cards()

    def _update_power(self) -> None:
        self.power_btn.setText("Stop wallpapers" if self.cfg.enabled else "Start wallpapers")

    def _update_markers(self) -> None:
        markers: dict[str, list[str]] = {}
        assigned = self.cfg.active_assignments(self.monitors) if self.cfg.enabled else []
        names = {m.name: (m.position or m.name) for m in self.monitors}
        for key, slot, _outs in assigned:
            label = "Span" if key == SPAN else names.get(key, key)
            markers.setdefault(slot.wallpaper or "", []).append(label)
        for wid, labels in markers.items():
            if len(self.monitors) >= 2 and len(labels) == len(self.monitors) and "Span" not in labels:
                markers[wid] = ["Both" if len(labels) == 2 else "All"]
        self.grid.set_markers(markers)

    def _engine_for(self, outputs: list[str]) -> dict | None:
        for e in self.status.get("engines", []):
            if set(outputs) & set(e.get("outputs", [])):
                return e
        return None

    def _rebuild_cards(self) -> None:
        if not self.monitors:
            return
        cfg = self.cfg
        wanted: list[tuple[str, str, list[str]]] = []
        span = cfg.slots.get(SPAN)
        if cfg.enabled and cfg.span_active and span is not None:
            outs = [m.name for m in self.monitors if m.name in span.outputs]
            wanted.append((SPAN, "Span · " + " + ".join(
                (m.position or m.name) for m in self.monitors if m.name in outs), outs))
            rest = [m for m in self.monitors if m.name not in outs]
        else:
            rest = list(self.monitors)
        for m in rest:
            wanted.append((m.name, f"{m.position + ' · ' if m.position else ''}{m.pretty} · {m.name}", [m.name]))
        keys = [w[0] for w in wanted]
        if list(self.cards) != keys:
            for c in self.cards.values():
                self.cards_row.removeWidget(c)
                c.deleteLater()
            self.cards = {}
            for key, heading, _outs in wanted:
                card = SetupCard(key, heading)
                card.clicked.connect(self._card_clicked)
                card.clearRequested.connect(self._clear)
                self.cards_row.addWidget(card, 2 if key == SPAN else 1)
                self.cards[key] = card
        for key, heading, outs in wanted:
            card = self.cards[key]
            card.heading.setText(heading.upper())
            slot = cfg.slots.get(key)
            wid = slot.wallpaper if (slot and cfg.enabled) else None
            wp = self.by_id.get(wid or "")
            still = self.thumbs.get(wp.id, wp.preview) if wp else None
            eng = self._engine_for(outs) if wp else None
            card.set_content(wp, still, self._state_html(wp, wid, eng))

    def _state_html(self, wp: workshop.Wallpaper | None, wid: str | None, eng: dict | None) -> str:
        if not self.cfg.enabled:
            return "Stopped"
        if wid and wp is None:
            return f"<span style='color:{theme.DANGER}'>Wallpaper {wid} not installed</span>"
        if wp is None:
            return "Desktop background shows through"
        if not self.status.get("daemon_pid"):
            return "Not running"
        if eng is None:
            return f"<span style='color:{theme.WARN}'>Not running — press Apply</span>"
        st = eng.get("state")
        if st == "running":
            shared = " (shared engine)" if len(eng.get("outputs", [])) > 1 else ""
            return (f"<span style='color:{theme.OK}'>●</span> Running · "
                    f"{eng.get('cpu_percent', 0):.0f}% CPU · {eng.get('mem_mb', 0):.0f} MB{shared}")
        if st == "paused":
            return f"<span style='color:{theme.WARN}'>❚❚</span> Paused (full-screen window)"
        if st in ("starting", "restarting"):
            return f"<span style='color:{theme.WARN}'>●</span> {st.capitalize()}…"
        err = "; ".join(eng.get("errors") or [])[:160]
        return f"<span style='color:{theme.DANGER}'>● Failed</span> {err}"

    def _card_clicked(self, wid: str, slot: str) -> None:
        if wid in self.by_id:
            self.panel.target = slot
            self.grid.select(wid)
            self.panel.show_wallpaper(self.by_id[wid], self.thumbs.get(wid, self.by_id[wid].preview), target=slot)


def main() -> int:
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
    paths.ensure_dirs()
    app = QApplication(sys.argv)
    app.setApplicationName("Wallpaper Picker")
    app.setDesktopFileName(APP_ID)
    theme.apply_theme(app)
    QIcon.setThemeName(os.environ.get("WALLPAPER_PICKER_ICON_THEME", "Fluent-dark"))
    QIcon.setFallbackThemeName("Pop")
    app.setWindowIcon(QIcon.fromTheme("preferences-desktop-wallpaper"))
    win = MainWindow()
    win.show()
    return app.exec()
