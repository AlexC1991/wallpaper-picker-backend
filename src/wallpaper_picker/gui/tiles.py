"""Wallpaper grid: painted tiles (thumbnail, title, type badge, where it is applied)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QMovie, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QWidget

from ..workshop import Wallpaper
from . import theme
from .thumbs import ThumbCache
from .widgets import draw_cover, pill

TYPE_LABEL = {"video": "Video", "scene": "Scene", "web": "Web", "application": "App", "unknown": "Unknown"}


class Tile(QWidget):
    clicked = Signal(str)
    activated = Signal(str)

    def __init__(self, wp: Wallpaper, thumbs: ThumbCache, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.wp = wp
        self.thumbs = thumbs
        self.selected = False
        self.hover = False
        self.markers: list[str] = []
        self._movie: QMovie | None = None
        self._frame = QImage()
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        w = self.wp
        lines = [f"<b>{w.title}</b>", f"{TYPE_LABEL.get(w.type, w.type)} · {w.id}"]
        if w.is_preset:
            lines.append(f"Preset of {w.dependency}")
        if w.error:
            lines.append(f"<span style='color:{theme.DANGER}'>{w.error}</span>")
        return "<br>".join(lines)

    @property
    def image_rect(self) -> QRectF:
        w = self.width()
        return QRectF(0, 0, w, w * 9 / 16)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.hover = True
        pv = self.wp.preview
        if pv is not None and pv.suffix.lower() == ".gif" and self._movie is None:
            m = QMovie(str(pv))
            m.setCacheMode(QMovie.CacheNone)
            size = QImageReader(str(pv)).size()
            dpr = self.devicePixelRatioF()
            target = QSize(int(self.width() * dpr), int(self.width() * 9 / 16 * dpr))
            if size.isValid() and size.width() > target.width() > 0:
                m.setScaledSize(size.scaled(target, Qt.KeepAspectRatioByExpanding))
            m.frameChanged.connect(self._on_frame)
            self._movie = m
            m.start()
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.hover = False
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
            self._frame = QImage()
        self.update()
        super().leaveEvent(e)

    def _on_frame(self, _n: int) -> None:
        if self._movie is not None:
            self._frame = self._movie.currentImage()
            self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.wp.id)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.activated.emit(self.wp.id)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        full = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = 10.0
        card = QPainterPath()
        card.addRoundedRect(full, radius, radius)
        p.fillPath(card, QColor(theme.SURFACE_3 if self.hover else theme.SURFACE_2))

        img_r = QRectF(full.left(), full.top(), full.width(), full.width() * 9 / 16)
        p.save()
        p.setClipPath(card)  # rounded top corners; the card continues below the image
        img = self._frame if not self._frame.isNull() else self.thumbs.get(self.wp.id, self.wp.preview)
        if img is not None and not img.isNull():
            draw_cover(p, img_r, img)
            if not self.wp.playable:
                p.fillRect(img_r, QColor(0, 0, 0, 150))
        else:
            p.fillRect(img_r, QColor(theme.SURFACE))
        p.restore()

        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() * 0.8))
        small.setWeight(QFont.DemiBold)
        p.setFont(small)
        t = "preset" if self.wp.is_preset and self.wp.playable else self.wp.type
        label = TYPE_LABEL.get(self.wp.type, self.wp.type)
        if self.wp.is_preset and self.wp.playable:
            label = f"{label} preset"
        badge = theme.type_color(t)
        badge.setAlpha(235)
        pill(p, QPointF(img_r.right() - 8, img_r.top() + 8), label, badge, align_right=True)
        x = img_r.left() + 8
        for m in self.markers:
            r = pill(p, QPointF(x, img_r.top() + 8), m, QColor(theme.ACCENT), QColor(theme.ACCENT_TEXT))
            x = r.right() + 4
        if not self.wp.playable:
            pill(p, QPointF(img_r.left() + 8, img_r.bottom() - 26), "Not supported", QColor(theme.DANGER))

        title_f = QFont(self.font())
        title_f.setWeight(QFont.DemiBold)
        p.setFont(title_f)
        p.setPen(QColor(theme.TEXT if self.wp.playable else theme.MUTED))
        tr = QRectF(full.left() + 10, img_r.bottom() + 7, full.width() - 20, p.fontMetrics().height())
        p.drawText(tr, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.wp.title, Qt.ElideRight, int(tr.width())))
        p.setFont(small)
        small.setWeight(QFont.Normal)
        p.setFont(small)
        p.setPen(QColor(theme.MUTED))
        sub = self.wp.source.capitalize() if self.wp.source != "workshop" else "Workshop"
        n = sum(1 for pr in self.wp.properties if pr.editable and pr.key != "schemecolor")
        if n:
            sub += f" · {n} setting{'s' if n != 1 else ''}"
        sr = QRectF(tr.left(), tr.bottom() + 2, tr.width(), p.fontMetrics().height())
        p.drawText(sr, Qt.AlignLeft | Qt.AlignVCenter, p.fontMetrics().elidedText(sub, Qt.ElideRight, int(sr.width())))

        if self.selected or self.hover:
            pen = QPen(QColor(theme.ACCENT) if self.selected else QColor("#4d505c"), 2 if self.selected else 1)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(full.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class WallpaperGrid(QScrollArea):
    selected = Signal(str)
    activated = Signal(str)

    MIN_TILE = 210
    GAP = 14
    MARGIN = 18

    def __init__(self, thumbs: ThumbCache, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.canvas = QWidget()
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setWidget(self.canvas)
        self.tiles: dict[str, Tile] = {}
        self.visible_ids: list[str] = []
        self.current: str | None = None
        self.thumbs.ready.connect(self._thumb_ready)
        self._relayout_timer = QTimer(self, singleShot=True, interval=0, timeout=self.relayout)

    def set_wallpapers(self, wps: list[Wallpaper]) -> None:
        for t in self.tiles.values():
            t.deleteLater()
        self.tiles.clear()
        for wp in wps:
            t = Tile(wp, self.thumbs, self.canvas)
            t.clicked.connect(self.select)
            t.activated.connect(self.activated)
            self.tiles[wp.id] = t
        self.visible_ids = [w.id for w in wps]
        if self.current in self.tiles:
            self.tiles[self.current].selected = True
        self.relayout()

    def set_markers(self, markers: dict[str, list[str]]) -> None:
        for wid, t in self.tiles.items():
            m = markers.get(wid, [])
            if m != t.markers:
                t.markers = m
                t.update()

    def apply_filter(self, query: str, wtype: str) -> int:
        vis = []
        for wid, t in self.tiles.items():
            ok = t.wp.matches(query) and (wtype == "all" or t.wp.type == wtype)
            t.setVisible(ok)
            if ok:
                vis.append(wid)
        self.visible_ids = vis
        self.relayout()
        return len(vis)

    def select(self, wid: str) -> None:
        if self.current in self.tiles:
            self.tiles[self.current].selected = False
            self.tiles[self.current].update()
        self.current = wid
        if wid in self.tiles:
            t = self.tiles[wid]
            t.selected = True
            t.update()
            self.ensureWidgetVisible(t, 0, 40)
        self.selected.emit(wid)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._relayout_timer.start()

    def relayout(self) -> None:
        avail = self.viewport().width() - 2 * self.MARGIN
        cols = max(1, (avail + self.GAP) // (self.MIN_TILE + self.GAP))
        tw = (avail - self.GAP * (cols - 1)) / cols
        th = tw * 9 / 16 + 50
        for i, wid in enumerate(self.visible_ids):
            r, c = divmod(i, cols)
            x = self.MARGIN + c * (tw + self.GAP)
            y = self.MARGIN + r * (th + self.GAP)
            self.tiles[wid].setGeometry(int(round(x)), int(round(y)), int(tw), int(th))
        rows = (len(self.visible_ids) + cols - 1) // cols
        self.canvas.setMinimumHeight(int(2 * self.MARGIN + rows * th + max(0, rows - 1) * self.GAP))

    def _thumb_ready(self, key: str, _img: QImage) -> None:
        t = self.tiles.get(key)
        if t is not None:
            t.update()
