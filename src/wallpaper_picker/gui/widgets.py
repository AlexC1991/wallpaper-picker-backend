"""Small painted widgets: toggle switch, segmented control, colour button, animated preview."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QImage, QImageReader, QMovie, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from . import theme


class Switch(QAbstractButton):
    """An on/off toggle switch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(38, 22)

    def _get(self) -> float:
        return self._pos

    def _set(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get, _set)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        super().setChecked(on)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 2, 36, 18)
        off, on = QColor(theme.SURFACE_3), QColor(theme.ACCENT)
        t = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                     int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            col.setAlpha(90)
        p.setPen(QPen(QColor(theme.BORDER) if t < 0.5 else col, 1))
        p.setBrush(col)
        p.drawRoundedRect(r, 9, 9)
        x = r.left() + 3 + (r.width() - 18) * t
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff") if self.isEnabled() else QColor(theme.FAINT))
        p.drawEllipse(QRectF(x, r.top() + 3, 12, 12))


class Segmented(QWidget):
    """Row of exclusive toggle buttons."""

    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._group.buttonClicked.connect(lambda b: self.changed.emit(b.property("value")))

    def set_options(self, options: list[tuple[str, str, str]]) -> None:
        """options: (value, label, tooltip)."""
        current = self.value()
        for b in list(self._buttons.values()):
            self._group.removeButton(b)
            b.deleteLater()
        self._buttons.clear()
        for i, (value, label, tip) in enumerate(options):
            b = QPushButton(label)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("value", value)
            b.setProperty("first", i == 0)
            b.setProperty("last", i == len(options) - 1)
            b.setToolTip(tip)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._group.addButton(b)
            self._lay.addWidget(b)
            self._buttons[value] = b
        if current in self._buttons:
            self._buttons[current].setChecked(True)

    def value(self) -> str | None:
        b = self._group.checkedButton()
        return b.property("value") if b else None

    def set_value(self, value: str) -> None:
        if value in self._buttons:
            self._buttons[value].setChecked(True)

    def options(self) -> list[str]:
        return list(self._buttons)


class ColorButton(QPushButton):
    colorChanged = Signal(QColor)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor("#ffffff")
        self.setFixedSize(56, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._pick)

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, c: QColor) -> None:
        self._color = QColor(c)
        self.setToolTip(c.name().upper())
        self.update()

    def _pick(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        c = QColorDialog.getColor(self._color, self.window(), "Choose colour")
        if c.isValid():
            self.set_color(c)
            self.colorChanged.emit(c)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.ACCENT if self.underMouse() else theme.BORDER), 1))
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(r, 7, 7)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawRoundedRect(r.adjusted(4, 4, -4, -4), 4, 4)


def rounded_path(r: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    return path


def draw_cover(p: QPainter, target: QRectF, img: QImage) -> None:
    """Draw ``img`` to fill ``target`` (centre crop), smoothly."""
    if img.isNull():
        return
    tw, th = target.width(), target.height()
    iw, ih = img.width(), img.height()
    if iw <= 0 or ih <= 0 or tw <= 0 or th <= 0:
        return
    s = max(tw / iw, th / ih)
    sw, sh = tw / s, th / s
    src = QRectF((iw - sw) / 2, (ih - sh) / 2, sw, sh)
    p.drawImage(target, img, src)


class AnimatedPreview(QWidget):
    """16:9 preview with rounded corners; animates GIFs."""

    def __init__(self, parent: QWidget | None = None, radius: float = 10) -> None:
        super().__init__(parent)
        self._img = QImage()
        self._movie: QMovie | None = None
        self._radius = radius
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(120)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return int(w * 9 / 16)

    def sizeHint(self) -> QSize:
        return QSize(360, 203)

    def set_source(self, path: Path | None, still: QImage | None = None) -> None:
        if self._movie:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
        self._img = still if still is not None else QImage()
        if path and path.suffix.lower() == ".gif":
            m = QMovie(str(path))
            m.setCacheMode(QMovie.CacheNone)
            size = QImageReader(str(path)).size()
            dpr = self.devicePixelRatioF()
            target = QSize(int(self.width() * dpr * 1.1), int(self.width() * 9 / 16 * dpr * 1.1))
            if size.isValid() and size.width() > target.width() > 0:
                m.setScaledSize(size.scaled(target, Qt.KeepAspectRatioByExpanding))
            m.frameChanged.connect(self._frame)
            self._movie = m
            m.start()
        elif path and still is None:
            r = QImageReader(str(path))
            r.setAutoTransform(True)
            self._img = r.read()
        self.update()

    def _frame(self, _n: int) -> None:
        if self._movie:
            self._img = self._movie.currentImage()
            self.update()

    def hideEvent(self, e) -> None:  # noqa: N802
        if self._movie:
            self._movie.setPaused(True)
        super().hideEvent(e)

    def showEvent(self, e) -> None:  # noqa: N802
        if self._movie:
            self._movie.setPaused(False)
        super().showEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        h = min(r.height(), r.width() * 9 / 16)
        r = QRectF(r.left(), r.top(), r.width(), h)
        path = rounded_path(r, self._radius)
        p.fillPath(path, QColor(theme.SURFACE_2))
        if not self._img.isNull():
            p.setClipPath(path)
            draw_cover(p, r, self._img)
            p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


def pill(p: QPainter, pos: QPointF, text: str, bg: QColor, fg: QColor = QColor("#ffffff"),
         align_right: bool = False) -> QRectF:
    fm = p.fontMetrics()
    w = fm.horizontalAdvance(text) + 14
    h = fm.height() + 4
    x = pos.x() - w if align_right else pos.x()
    r = QRectF(x, pos.y(), w, h)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(r, h / 2, h / 2)
    p.setPen(fg)
    p.drawText(r, Qt.AlignCenter, text)
    return r


class ElidedLabel(QLabel):
    """Single-line label that elides with an ellipsis instead of growing the layout."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(20)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        super().setText(text)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        fm = self.fontMetrics()
        p.drawText(self.contentsRect(), int(self.alignment()) | Qt.AlignVCenter,
                   fm.elidedText(self._full, Qt.ElideRight, self.contentsRect().width()))
