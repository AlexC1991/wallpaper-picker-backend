"""Side panel: preview, monitor target, display settings and the wallpaper's own properties."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..config import FPS_CHOICES, SPAN, Config, Slot
from ..monitors import Monitor
from ..workshop import Wallpaper
from . import theme
from .props import PropertyEditor
from .tiles import TYPE_LABEL
from .widgets import AnimatedPreview, Segmented, Switch

SCALING = [("fill", "Fill (crop to screen)"), ("fit", "Fit (letterbox)"), ("stretch", "Stretch"),
           ("default", "Wallpaper default")]


def section(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("Section")
    return lab


def switch_row(text: str, tip: str = "") -> tuple[QWidget, Switch, QLabel]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 2, 0, 2)
    lab = QLabel(text)
    lab.setToolTip(tip)
    sw = Switch()
    sw.setToolTip(tip)
    lay.addWidget(lab, 1)
    lay.addWidget(sw, 0, Qt.AlignRight)
    return w, sw, lab


class SidePanel(QFrame):
    applyRequested = Signal(str, str, dict, dict)  # wallpaper id, target, settings, overrides

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMinimumWidth(360)
        self.setMaximumWidth(460)
        self.wp: Wallpaper | None = None
        self.monitors: list[Monitor] = []
        self.cfg = Config()
        self.target = "both"
        self.dirty = False
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(10)

        self.preview = AnimatedPreview()
        lay.addWidget(self.preview)
        self.title = QLabel("Choose a wallpaper")
        self.title.setObjectName("Title")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.title)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        self.subtitle.setTextFormat(Qt.RichText)
        lay.addWidget(self.subtitle)
        self.problem = QLabel("")
        self.problem.setObjectName("Error")
        self.problem.setWordWrap(True)
        self.problem.hide()
        lay.addWidget(self.problem)

        self.controls = QWidget()
        cl = QVBoxLayout(self.controls)
        cl.setContentsMargins(0, 6, 0, 0)
        cl.setSpacing(8)
        lay.addWidget(self.controls)

        cl.addWidget(section("Monitor"))
        self.targets = Segmented()
        self.targets.changed.connect(self._target_changed)
        cl.addWidget(self.targets)
        self.target_hint = QLabel("")
        self.target_hint.setObjectName("Faint")
        self.target_hint.setWordWrap(True)
        cl.addWidget(self.target_hint)

        cl.addSpacing(6)
        cl.addWidget(section("Display"))
        row = QHBoxLayout()
        lab = QLabel("Scaling")
        self.scaling = QComboBox()
        for value, text in SCALING:
            self.scaling.addItem(text, value)
        self.scaling.currentIndexChanged.connect(self._touched)
        row.addWidget(lab, 1)
        row.addWidget(self.scaling, 1)
        cl.addLayout(row)
        row = QHBoxLayout()
        lab = QLabel("Frame rate")
        lab.setToolTip("Frames per second. Lower uses less CPU/GPU.")
        self.fps = Segmented()
        self.fps.set_options([(str(f), str(f), f"{f} fps") for f in FPS_CHOICES])
        self.fps.changed.connect(self._touched)
        row.addWidget(lab, 1)
        row.addWidget(self.fps, 1)
        cl.addLayout(row)
        w, self.mouse, self.mouse_lab = switch_row("Mouse interaction", "Let the wallpaper react to the mouse pointer")
        self.mouse.toggled.connect(self._touched)
        cl.addWidget(w)
        w, self.parallax, self.parallax_lab = switch_row("Parallax", "Layers shift slightly as the mouse moves (scenes)")
        self.parallax.toggled.connect(self._touched)
        cl.addWidget(w)
        w, self.particles, self.particles_lab = switch_row("Particles", "Particle effects such as rain, snow and sparks (scenes)")
        self.particles.toggled.connect(self._touched)
        cl.addWidget(w)
        self.audio_note = QLabel("Audio is always off.")
        self.audio_note.setObjectName("Faint")
        cl.addWidget(self.audio_note)

        cl.addSpacing(6)
        head = QHBoxLayout()
        self.props_title = section("Wallpaper settings")
        head.addWidget(self.props_title, 1)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setObjectName("Ghost")
        self.reset_btn.setToolTip("Restore this wallpaper's default settings")
        self.reset_btn.clicked.connect(self._reset_props)
        head.addWidget(self.reset_btn)
        cl.addLayout(head)
        self.props = PropertyEditor()
        self.props.changed.connect(self._touched)
        cl.addWidget(self.props)
        self.no_props = QLabel("This wallpaper has no settings of its own.")
        self.no_props.setObjectName("Faint")
        cl.addWidget(self.no_props)
        lay.addStretch(1)

        # sticky footer
        footer = QFrame()
        footer.setObjectName("Footer")
        footer.setStyleSheet(f"#Footer {{ border-top: 1px solid {theme.BORDER}; background: {theme.SURFACE}; }}")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(20, 12, 20, 16)
        fl.setSpacing(8)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.setMargin(3)
        self.result.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.result.hide()
        fl.addWidget(self.result)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn.clicked.connect(self._apply)
        fl.addWidget(self.apply_btn)
        outer.addWidget(footer)
        self.controls.setEnabled(False)
        self.apply_btn.setEnabled(False)

    # ------------------------------------------------------------------ context
    def set_context(self, monitors: list[Monitor], cfg: Config) -> None:
        self.monitors = monitors
        self.cfg = cfg
        opts = []
        for m in monitors:
            opts.append((m.name, m.position or m.pretty, f"{m.pretty} · {m.name} · {m.width}×{m.height}"))
        if len(monitors) >= 2:
            opts.append(("both", "Both" if len(monitors) == 2 else "All", "The same wallpaper on every monitor"))
            opts.append((SPAN, "Span", "One wallpaper stretched across all monitors"))
        self.targets.set_options(opts)
        if self.target not in self.targets.options():
            self.target = "both" if len(monitors) >= 2 else (monitors[0].name if monitors else "both")
        self.targets.set_value(self.target)
        self._update_target_hint()

    def target_slots(self, target: str | None = None) -> list[str]:
        t = target or self.target
        if t == "both":
            return [m.name for m in self.monitors]
        return [t]

    def _update_target_hint(self) -> None:
        t = self.target
        if t == "both":
            txt = "Same wallpaper on " + " and ".join(f"{m.pretty} ({m.name})" for m in self.monitors)
        elif t == SPAN:
            txt = "One image across " + " + ".join(m.name for m in self.monitors)
        else:
            m = next((m for m in self.monitors if m.name == t), None)
            txt = f"{m.pretty} · {m.name} · {m.width}×{m.height} @ {m.refresh:.0f} Hz" if m else t
        self.target_hint.setText(txt)
        if t == "both":
            label = "Apply to both monitors" if len(self.monitors) == 2 else "Apply to all monitors"
        elif t == SPAN:
            label = "Apply across monitors"
        else:
            m = next((m for m in self.monitors if m.name == t), None)
            label = f"Apply to {m.position} monitor" if m and m.position else f"Apply to {t}"
        self.apply_btn.setText(label if self.wp else "Apply")

    # ------------------------------------------------------------------ wallpaper
    def show_wallpaper(self, wp: Wallpaper | None, still: QImage | None = None, target: str | None = None) -> None:
        self.wp = wp
        self.dirty = False
        self.result.hide()
        if target and target in self.targets.options():
            self.target = target
        if self.target in self.targets.options():
            self.targets.set_value(self.target)
        if wp is None:
            self.title.setText("Choose a wallpaper")
            self.subtitle.setText("")
            self.preview.set_source(None)
            self.controls.setEnabled(False)
            self.apply_btn.setEnabled(False)
            return
        self.preview.set_source(wp.preview, still if (wp.preview and wp.preview.suffix.lower() != ".gif") else None)
        self.title.setText(wp.title)
        tcol = theme.TYPE_COLORS.get("preset" if wp.is_preset else wp.type, theme.FAINT)
        kind = TYPE_LABEL.get(wp.type, wp.type) + (" preset" if wp.is_preset else "")
        src = {"workshop": "Workshop", "built-in": "Built-in", "my projects": "My projects"}.get(wp.source, wp.source)
        sub = f"<span style='color:{tcol}; font-weight:600'>{kind}</span> · {src} · <span style='color:{theme.FAINT}'>{wp.id}</span>"
        if wp.is_preset:
            sub += f"<br><span style='color:{theme.FAINT}'>Preset of wallpaper {wp.dependency}</span>"
        self.subtitle.setText(sub)
        if wp.playable:
            self.problem.hide()
        else:
            self.problem.setText(wp.error or "This wallpaper can't be played by linux-wallpaperengine.")
            self.problem.show()
        self.controls.setEnabled(wp.playable)
        self.apply_btn.setEnabled(wp.playable)
        self._load_from_config(keep_props=False)
        self._update_target_hint()

    def _load_from_config(self, keep_props: bool) -> None:
        if self.wp is None:
            return
        self._loading = True
        slots = self.target_slots()
        slot = self.cfg.slots.get(slots[0]) if slots else None
        slot = slot or Slot()
        idx = self.scaling.findData(slot.scaling)
        self.scaling.setCurrentIndex(max(0, idx))
        fps = min(FPS_CHOICES, key=lambda f: abs(f - slot.fps))
        self.fps.set_value(str(fps))
        self.mouse.setChecked(slot.mouse)
        self.parallax.setChecked(slot.parallax)
        self.particles.setChecked(slot.particles)
        scene_only = self.wp.type == "video"
        for sw, lab in ((self.mouse, self.mouse_lab), (self.parallax, self.parallax_lab), (self.particles, self.particles_lab)):
            sw.setEnabled(not scene_only)
            lab.setEnabled(not scene_only)
        if not keep_props:
            overrides: dict[str, Any] = {}
            for s in slots:
                overrides = self.cfg.overrides(s, self.wp.id)
                if overrides:
                    break
            if not overrides:  # reuse what was set for this wallpaper on another monitor
                for s in self.cfg.properties:
                    overrides = self.cfg.overrides(s, self.wp.id)
                    if overrides:
                        break
            self.props.set_wallpaper(self.wp, overrides)
        has = self.props.has_properties()
        self.no_props.setVisible(not has)
        self.reset_btn.setVisible(has)
        self._loading = False

    # ------------------------------------------------------------------ events
    def _target_changed(self, value: str) -> None:
        self.target = value
        self._update_target_hint()
        self._load_from_config(keep_props=self.dirty)

    def _touched(self, *_a: Any) -> None:
        if self._loading:
            return
        self.dirty = True
        self.result.hide()

    def _reset_props(self) -> None:
        self.props.reset()

    def settings(self) -> dict:
        return {
            "scaling": self.scaling.currentData(),
            "fps": int(self.fps.value() or 30),
            "mouse": self.mouse.isChecked(),
            "parallax": self.parallax.isChecked(),
            "particles": self.particles.isChecked(),
        }

    def _apply(self) -> None:
        if self.wp is None:
            return
        self.applyRequested.emit(self.wp.id, self.target, self.settings(), self.props.overrides())

    # ------------------------------------------------------------------ feedback
    def set_busy(self, busy: bool) -> None:
        self.apply_btn.setEnabled(not busy and bool(self.wp and self.wp.playable))
        if busy:
            self.apply_btn.setText("Applying…")
            self.show_result(None, "Restarting the wallpaper engine…")
        else:
            self._update_target_hint()

    def show_result(self, ok: bool | None, text: str) -> None:
        self.result.setObjectName({True: "Success", False: "Error", None: "Info"}[ok])
        self.result.setText(text)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)
        self.result.show()
        if ok:
            self.dirty = False
