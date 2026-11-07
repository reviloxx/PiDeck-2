"""Console-style overlay shown above a running application for returning to PiDeck."""

from __future__ import annotations

import logging
import re
import subprocess

from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QPaintEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
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
_BLUR_RADIUS = 28
_SCRIM = QColor(0, 0, 0, 168)
_ACTIVE_WINDOW = re.compile(r"window id # (0x[0-9a-fA-F]+|\d+)")


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
    item = QGraphicsPixmapItem(small)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.PerformanceHint)
    item.setGraphicsEffect(effect)
    scene = QGraphicsScene()
    scene.addItem(item)
    blurred = QPixmap(small.size())
    blurred.fill(Qt.GlobalColor.transparent)
    painter = QPainter(blurred)
    scene.render(painter)
    painter.end()
    return blurred.scaled(
        source.size(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def active_window_id() -> int:
    """Return the focused X11 window id, or ``0`` when it cannot be determined.

    Openbox has no compositor, so grabbing the root window often returns a
    stale root pixmap. Grabbing ``_NET_ACTIVE_WINDOW`` captures the running app.
    """
    try:
        result = subprocess.run(
            ["xprop", "-root", "-notype", "_NET_ACTIVE_WINDOW"],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _LOGGER.debug("Unable to read the active X11 window: %s", error)
        return 0
    if result.returncode != 0:
        return 0
    match = _ACTIVE_WINDOW.search(result.stdout)
    if match is None:
        return 0
    try:
        return int(match.group(1), 0)
    except ValueError:
        return 0


def capture_blurred_screen() -> QPixmap:
    """Grab the active application window and return a blurred copy."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QPixmap()
    window_id = active_window_id()
    # Copy detaches from any shared/server-side buffer so later opens stay fresh.
    grab = screen.grabWindow(window_id).copy()
    if grab.isNull() and window_id != 0:
        grab = screen.grabWindow(0).copy()
    return blur_pixmap(grab)


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
        self.setAutoFillBackground(False)
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
        self._opacity = QGraphicsOpacityEffect(self._card)
        self._opacity.setOpacity(1.0)
        self._card.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(theme.animation.duration_ms)
        apply_theme(self, theme)

    def open_overlay(self) -> None:
        """Capture the screen, raise the overlay, and focus the return action."""
        self._backdrop = capture_blurred_screen()
        if self._backdrop.isNull():
            fallback = QPixmap(1, 1)
            fallback.fill(QColor(self._theme.colors["background"]))
            self._backdrop = fallback
        self._opacity.setOpacity(0.0)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.return_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self.update()

    def dismiss(self) -> None:
        """Hide the overlay without stopping the application."""
        if self.isVisible():
            self._fade.stop()
            self.hide()
            self._backdrop = QPixmap()
            self.dismissed.emit()

    def handle_input(self, event: InputEvent) -> None:
        """Activate the return control; MENU/BACK dismiss without quitting the app."""
        if not event.pressed:
            return
        if event.action in (InputAction.MENU, InputAction.HOME, InputAction.BACK):
            self.dismiss()
        elif event.action is InputAction.ACTIVATE:
            self.return_button.click()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the blurred app capture and a dark scrim behind the card."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self._backdrop.isNull():
            painter.drawPixmap(self.rect(), self._backdrop)
        else:
            painter.fillRect(self.rect(), QColor(self._theme.colors["background"]))
        painter.fillRect(self.rect(), _SCRIM)

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
        self._fade.stop()
        self.hide()
        self._backdrop = QPixmap()
        self.return_requested.emit()
