"""Editor for a wallpaper's own properties (project.json ``general.properties``)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget)

from ..engine import IGNORED_PROPERTIES
from ..workshop import Property, Wallpaper, eval_condition, format_color, parse_color
from .widgets import ColorButton, Switch

SLIDER_STEPS = 1000


class _Row(QWidget):
    def __init__(self, prop: Property, label: QLabel) -> None:
        super().__init__()
        self.prop = prop
        self.label = label


class PropertyEditor(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(6)
        self.wp: Wallpaper | None = None
        self.values: dict[str, Any] = {}
        self.rows: list[_Row] = []
        self._setters: dict[str, Any] = {}
        self._loading = False

    # ------------------------------------------------------------------ public
    def set_wallpaper(self, wp: Wallpaper | None, overrides: dict[str, Any]) -> None:
        self._loading = True
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows.clear()
        self._setters.clear()
        self.wp = wp
        self.values = {}
        if wp is None:
            self._loading = False
            return
        for p in wp.properties:
            if p.editable:
                v = overrides.get(p.key, p.default)
                self.values[p.key] = p.normalize(v)
        unsupported = 0
        for p in wp.properties:
            if p.key in IGNORED_PROPERTIES:
                continue
            if p.type == "group":
                lab = QLabel(p.label)
                lab.setObjectName("GroupLabel")
                row = _Row(p, lab)
                QVBoxLayout(row).addWidget(lab)
                row.layout().setContentsMargins(0, 4, 0, 0)
                self._add(row)
            elif p.type == "text":
                if not p.label.strip():
                    continue
                lab = QLabel(p.label)
                lab.setObjectName("Faint")
                lab.setWordWrap(True)
                lab.setTextFormat(Qt.PlainText)
                row = _Row(p, lab)
                l = QVBoxLayout(row)
                l.setContentsMargins(0, 0, 0, 0)
                l.addWidget(lab)
                self._add(row)
            elif p.editable:
                self._add(self._editor(p))
            else:
                unsupported += 1
        if unsupported:
            note = QLabel(f"{unsupported} file/texture setting{'s' if unsupported != 1 else ''} can't be edited here.")
            note.setObjectName("Faint")
            note.setWordWrap(True)
            self._lay.addWidget(note)
        self._loading = False
        self._refresh()

    def overrides(self) -> dict[str, Any]:
        """Only the values that differ from the wallpaper's defaults."""
        if self.wp is None:
            return {}
        out = {}
        for p in self.wp.properties:
            if p.editable and p.key in self.values and not _same(p, self.values[p.key], p.default):
                out[p.key] = self.values[p.key]
        return out

    def reset(self) -> None:
        if self.wp is None:
            return
        self.set_wallpaper(self.wp, {})
        self.changed.emit()

    def has_properties(self) -> bool:
        return bool(self.rows)

    # ------------------------------------------------------------------ building
    def _add(self, row: _Row) -> None:
        self.rows.append(row)
        self._lay.addWidget(row)

    def _label(self, p: Property) -> QLabel:
        lab = QLabel(p.label)
        lab.setObjectName("PropLabel")
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.PlainText)
        lab.setToolTip(f"{p.key} ({p.type})")
        lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return lab

    def _editor(self, p: Property) -> _Row:
        lab = self._label(p)
        row = _Row(p, lab)
        v = self.values[p.key]
        if p.type == "bool":
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 2, 0, 2)
            sw = Switch()
            sw.setChecked(bool(v))
            sw.toggled.connect(lambda on, k=p.key: self._set(k, on))
            lay.addWidget(lab, 1)
            lay.addWidget(sw, 0, Qt.AlignRight | Qt.AlignVCenter)
            self._setters[p.key] = lambda val, w=sw: w.setChecked(bool(val))
        elif p.type == "color":
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 2, 0, 2)
            btn = ColorButton()
            btn.set_color(_qcolor(v))
            btn.colorChanged.connect(lambda c, k=p.key: self._set(k, format_color((c.redF(), c.greenF(), c.blueF()))))
            lay.addWidget(lab, 1)
            lay.addWidget(btn, 0, Qt.AlignRight | Qt.AlignVCenter)
            self._setters[p.key] = lambda val, w=btn: w.set_color(_qcolor(val))
        elif p.type == "combo":
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 2, 0, 2)
            cb = QComboBox()
            cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            cb.setMinimumWidth(120)
            for text, value in p.options:
                cb.addItem(text, value)
            idx = cb.findData(str(v))
            if idx < 0 and v not in (None, ""):
                cb.addItem(str(v), str(v))
                idx = cb.count() - 1
            cb.setCurrentIndex(max(idx, 0))
            cb.currentIndexChanged.connect(lambda _i, k=p.key, w=cb: self._set(k, w.currentData()))
            lay.addWidget(lab, 1)
            lay.addWidget(cb, 1)
            self._setters[p.key] = lambda val, w=cb: w.setCurrentIndex(max(0, w.findData(str(val))))
        elif p.type == "slider":
            grid = QGridLayout(row)
            grid.setContentsMargins(0, 2, 0, 2)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(2)
            lo, hi = float(p.min or 0.0), float(p.max if p.max is not None else 1.0)
            if hi <= lo:
                hi = lo + 1
            integer = not p.fraction and (p.step is None or float(p.step).is_integer()) and lo.is_integer() and hi.is_integer()
            steps = int(hi - lo) if integer and 0 < hi - lo <= 2000 else SLIDER_STEPS
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, steps)
            spin = QDoubleSpinBox()
            decimals = 0 if integer else (p.precision if p.precision is not None else 2)
            spin.setDecimals(max(0, min(4, decimals)))
            spin.setRange(lo, hi)
            spin.setSingleStep(p.step or (1 if integer else (hi - lo) / 100))
            spin.setFixedWidth(70)
            spin.setAlignment(Qt.AlignRight)
            spin.setButtonSymbols(QDoubleSpinBox.NoButtons)

            def to_pos(val: float) -> int:
                return int(round((float(val) - lo) / (hi - lo) * steps))

            def from_pos(pos: int) -> float:
                val = lo + (hi - lo) * pos / steps
                if p.step:
                    val = lo + round((val - lo) / p.step) * p.step
                return round(val, 6)

            def slider_moved(pos: int, k=p.key) -> None:
                val = from_pos(pos)
                spin.blockSignals(True)
                spin.setValue(val)
                spin.blockSignals(False)
                self._set(k, val)

            def spin_changed(val: float, k=p.key) -> None:
                sl.blockSignals(True)
                sl.setValue(to_pos(val))
                sl.blockSignals(False)
                self._set(k, val)

            def setter(val: Any) -> None:
                for w in (sl, spin):
                    w.blockSignals(True)
                sl.setValue(to_pos(float(val)))
                spin.setValue(float(val))
                for w in (sl, spin):
                    w.blockSignals(False)

            setter(v)
            sl.valueChanged.connect(slider_moved)
            spin.valueChanged.connect(spin_changed)
            grid.addWidget(lab, 0, 0, 1, 2)
            grid.addWidget(sl, 1, 0)
            grid.addWidget(spin, 1, 1)
            self._setters[p.key] = setter
        elif p.type == "textinput":
            lay = QVBoxLayout(row)
            lay.setContentsMargins(0, 2, 0, 2)
            lay.setSpacing(4)
            le = QLineEdit(str(v))
            le.editingFinished.connect(lambda k=p.key, w=le: self._set(k, w.text()))
            lay.addWidget(lab)
            lay.addWidget(le)
            self._setters[p.key] = lambda val, w=le: w.setText(str(val))
        return row

    # ------------------------------------------------------------------ state
    def _set(self, key: str, value: Any) -> None:
        if self._loading or self.wp is None:
            return
        p = self.wp.prop(key)
        if p is None:
            return
        self.values[key] = p.normalize(value)
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        if self.wp is None:
            return
        for row in self.rows:
            p = row.prop
            row.setVisible(eval_condition(p.condition, self.values))
            if p.editable:
                modified = not _same(p, self.values.get(p.key), p.default)
                if row.label.property("modified") != modified:
                    row.label.setProperty("modified", modified)
                    row.label.style().unpolish(row.label)
                    row.label.style().polish(row.label)


def _qcolor(v: Any) -> QColor:
    r, g, b = parse_color(v)
    return QColor.fromRgbF(r, g, b)


def _same(p: Property, a: Any, b: Any) -> bool:
    try:
        return p.cli_value(a) == p.cli_value(b)
    except (TypeError, ValueError):
        return a == b
