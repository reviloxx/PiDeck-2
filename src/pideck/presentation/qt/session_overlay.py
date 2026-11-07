"""Console-style overlay shown above a running application for returning to PiDeck."""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent, QPainter, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .theme import apply_theme

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
        _LOGGER.info("Session overlay screen grab was empty or flat; using solid backdrop")
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


class SessionOverlay(QDialog):
    """Stay-on-top quick menu with a blurred app backdrop and one return action."""

    return_requested = Signal()
    dismissed = Signal()

    def __init__(self, theme: ThemeDefinition, language: str, parent: QWidget | None = None) -> None:
        """Build the overlay; call ``open_overlay`` to show it above an application."""
        super().__init__(parent)
        self._theme = theme
        self._language = language
        self._backdrop = QPixmap()
        self.setObjectName("session_overlay")
        self.setModal(True)
        self.setWindowTitle("PiDeck")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(True)
        self._backdrop_label = QLabel(self)
        self._backdrop_label.setObjectName("session_overlay_backdrop")
        self._backdrop_label.setScaledContents(True)
        self._backdrop_label.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._backdrop_label.lower()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self._card = QFrame(self)
        self._card.setObjectName("session_overlay_card")
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(36, 28, 36, 28)
        card_layout.setSpacing(0)
        self.return_button = QPushButton(tr(language, "session_overlay_return"), self._card)
        self.return_button.setObjectName("session_overlay_return")
        self.return_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.return_button.installEventFilter(self)
        card_layout.addWidget(self.return_button)
        row.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(1)
        self.return_button.clicked.connect(self._request_return)
        apply_theme(self, theme)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep the backdrop label matched to the dialog size."""
        super().resizeEvent(event)
        self._backdrop_label.setGeometry(self.rect())
        self._backdrop_label.lower()

    def open_overlay(self) -> None:
        """Capture the screen, raise the overlay, and focus the return action."""
        if self.isVisible():
            self.hide()
            QApplication.processEvents()
        fill = QColor(self._theme.colors["background"])
        self._backdrop = compose_backdrop(capture_blurred_screen(), fill)
        self._backdrop_label.setPixmap(self._backdrop)
        self.showFullScreen()
        self._backdrop_label.setGeometry(self.rect())
        self._backdrop_label.lower()
        self._backdrop_label.show()
        self.raise_()
        self.activateWindow()
        self.return_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def dismiss(self) -> None:
        """Hide the overlay without stopping the application."""
        if self.isVisible():
            self.hide()
            self._backdrop = QPixmap()
            self._backdrop_label.clear()
            self.dismissed.emit()

    def handle_input(self, event: InputEvent) -> None:
        """Activate the return control; MENU/BACK dismiss without quitting the app."""
        if not event.pressed:
            return
        if event.action in (InputAction.MENU, InputAction.HOME, InputAction.BACK):
            self.dismiss()
        elif event.action is InputAction.ACTIVATE:
            self.return_button.click()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Return activates the action; Escape must not quit the application."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.return_button.click()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Keep Return/Enter on the button activating the return action."""
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and watched is self.return_button
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
        ):
            self.return_button.click()
            return True
        return super().eventFilter(watched, event)

    def _request_return(self) -> None:
        """Hide first so the launcher can restore without the overlay on top."""
        self.hide()
        self._backdrop = QPixmap()
        self._backdrop_label.clear()
        self.return_requested.emit()
