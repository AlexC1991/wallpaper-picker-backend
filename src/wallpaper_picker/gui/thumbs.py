"""Thumbnail cache: previews scaled/cropped to 16:9 once, stored in ~/.cache/wallpaper-picker/thumbs."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

from .. import paths

THUMB_SIZE = QSize(560, 315)  # physical pixels; tiles are ~260 logical px wide


def cover_crop(img: QImage, size: QSize) -> QImage:
    if img.isNull():
        return img
    scaled = img.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - size.width()) // 2)
    y = max(0, (scaled.height() - size.height()) // 2)
    return scaled.copy(QRect(x, y, size.width(), size.height()))


def _dark(img: QImage) -> bool:
    small = img.scaled(16, 9, Qt.IgnoreAspectRatio, Qt.FastTransformation).convertToFormat(QImage.Format_Grayscale8)
    total = sum(small.pixelColor(x, y).value() for x in range(16) for y in range(9))
    return total / (16 * 9) < 14


def cache_path(preview: Path, size: QSize = THUMB_SIZE) -> Path:
    try:
        st = preview.stat()
        stamp = f"{preview}|{int(st.st_mtime)}|{st.st_size}|{size.width()}x{size.height()}|v2"
    except OSError:
        stamp = str(preview)
    return paths.thumbs_dir() / (hashlib.sha1(stamp.encode()).hexdigest()[:20] + ".jpg")


def load_thumb(preview: Path, size: QSize = THUMB_SIZE) -> QImage:
    cp = cache_path(preview, size)
    if cp.is_file():
        img = QImage(str(cp))
        if not img.isNull():
            return img
    reader = QImageReader(str(preview))
    reader.setAutoTransform(True)
    src = reader.size()
    if src.isValid() and src.width() > size.width() * 2:
        # decode big JPEGs at reduced size (fast path in the jpeg plugin)
        reader.setScaledSize(src.scaled(size * 2, Qt.KeepAspectRatioByExpanding))
    img = reader.read()
    if img.isNull():
        return img
    if reader.supportsAnimation() and reader.imageCount() > 3 and _dark(img):
        # many animated previews fade in from black: use a frame a third of the way in
        target = reader.imageCount() // 3
        for _ in range(target - 1):
            if not reader.jumpToNextImage():
                break
        frame = reader.read()
        if not frame.isNull():
            img = frame
    img = cover_crop(img.convertToFormat(QImage.Format_RGB32), size)
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(cp), "JPG", 90)
    except OSError:
        pass
    return img


class _Emitter(QObject):
    ready = Signal(str, QImage)


class _Job(QRunnable):
    def __init__(self, key: str, preview: Path, emitter: _Emitter) -> None:
        super().__init__()
        self.key, self.preview, self.emitter = key, preview, emitter

    def run(self) -> None:
        try:
            img = load_thumb(self.preview)
        except Exception:  # noqa: BLE001
            img = QImage()
        self.emitter.ready.emit(self.key, img)


class ThumbCache(QObject):
    ready = Signal(str, QImage)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mem: dict[str, QImage] = {}
        self._pending: set[str] = set()
        self._emitter = _Emitter()
        self._emitter.ready.connect(self._done)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)

    def get(self, key: str, preview: Path | None) -> QImage | None:
        if key in self._mem:
            return self._mem[key]
        if preview is None or key in self._pending:
            return None
        self._pending.add(key)
        self._pool.start(_Job(key, preview, self._emitter))
        return None

    def _done(self, key: str, img: QImage) -> None:
        self._pending.discard(key)
        self._mem[key] = img
        self.ready.emit(key, img)
