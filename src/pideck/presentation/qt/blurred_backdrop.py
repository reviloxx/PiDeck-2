"""Shared screen capture and blur helpers for overlays and dialogs."""

from __future__ import annotations

import logging

from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene
from PySide6.QtCore import Qt

_LOGGER = logging.getLogger(__name__)
_BLUR_SCALE = 4
_BLUR_RADIUS = 24
_SCRIM = QColor(0, 0, 0, 168)
# Grabs of uncomposited OpenGL windows are often nearly solid black.
_MIN_USEFUL_VARIANCE = 12.0


def blur_pixmap(source: QPixmap, *, scale: int = _BLUR_SCALE, radius: int = _BLUR_RADIUS) -> QPixmap:
    """Return a soft blur of ``source``, downscaling first for speed on appliances."""
    if source.isNull() or source.width() <= 0 or source.height() <= 0:
        return QPixmap()
    factor = max(1, scale)
    small = source.scaled(
        max(1, source.width() // factor),
        max(1, source.height() // factor),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    # Render onto an opaque image; a transparent QPixmap often yields a black blur.
    image = small.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(
        QGraphicsBlurEffect.BlurHint.QualityHint | QGraphicsBlurEffect.BlurHint.AnimationHint
    )
    item.setGraphicsEffect(effect)
    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, image.width(), image.height())
    scene.addItem(item)
    blurred = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    blurred.fill(QColor(0, 0, 0, 255))
    painter = QPainter(blurred)
    scene.render(painter)
    painter.end()
    return QPixmap.fromImage(blurred).scaled(
        source.size(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def grab_is_useful(pixmap: QPixmap) -> bool:
    """Return whether ``pixmap`` looks like real screen content, not a black GL stub."""
    if pixmap.isNull() or pixmap.width() < 2 or pixmap.height() < 2:
        return False
    sample = pixmap.scaled(
        64, 64, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation,
    ).toImage().convertToFormat(QImage.Format.Format_RGB32)
    values: list[int] = []
    for y in range(0, sample.height(), 4):
        for x in range(0, sample.width(), 4):
            pixel = sample.pixelColor(x, y)
            values.append((pixel.red() + pixel.green() + pixel.blue()) // 3)
    if not values:
        return False
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    # Reject near-black and near-flat captures that look broken when blurred.
    return mean >= 8.0 and variance >= _MIN_USEFUL_VARIANCE


def capture_screen() -> QPixmap:
    """Grab the visible screen contents.

    Openbox without a compositor cannot expose fullscreen OpenGL clients to
    ``grabWindow``. With picom running, a root grab captures what the user sees.
    """
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QPixmap()
    # Flush pending X paints so the grab is not a frame behind.
    QApplication.processEvents()
    return screen.grabWindow(0).copy()


def capture_blurred_screen() -> QPixmap:
    """Return a blurred screen grab, or an empty pixmap when capture is unusable."""
    grab = capture_screen()
    if not grab_is_useful(grab):
        _LOGGER.info("Screen grab was empty or flat; using solid backdrop")
        return QPixmap()
    return blur_pixmap(grab)


def compose_backdrop(pixmap: QPixmap, fill: QColor) -> QPixmap:
    """Build one opaque frame: blurred capture (or solid fill) plus the dark scrim."""
    if pixmap.isNull():
        screen = QGuiApplication.primaryScreen()
        if screen is None or screen.size().width() <= 0:
            frame = QPixmap(64, 64)
        else:
            frame = QPixmap(screen.size())
        frame.fill(fill)
    else:
        frame = QPixmap(pixmap.size())
        frame.fill(fill)
        painter = QPainter(frame)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
    painter = QPainter(frame)
    painter.fillRect(frame.rect(), _SCRIM)
    painter.end()
    return frame
