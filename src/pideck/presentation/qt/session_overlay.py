"""Console-style overlay shown above a running application for returning to PiDeck."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QHideEvent, QKeyEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .blurred_backdrop import (
    blur_pixmap,
    capture_blurred_screen,
    compose_backdrop,
    grab_is_useful,
)
from .fullscreen import cancel_fullscreen_reassert, present_fullscreen
from .localization import clock_text, tr
from .theme import apply_theme

# Re-export blur helpers for existing tests and callers.
__all__ = [
    "SessionOverlay",
    "blur_pixmap",
    "capture_blurred_screen",
    "compose_backdrop",
    "grab_is_useful",
]


class SessionOverlay(QDialog):
    """Stay-on-top home confirmation with a blurred app backdrop."""

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
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(True)
        self._backdrop_label = QLabel(self)
        self._backdrop_label.setObjectName("session_overlay_backdrop")
        self._backdrop_label.setScaledContents(True)
        self._backdrop_label.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._backdrop_label.lower()

        root = QVBoxLayout(self)
        root.setContentsMargins(56, 44, 56, 44)
        root.setSpacing(0)

        header = QHBoxLayout()
        title = QLabel("PiDeck", self)
        title.setObjectName("launcher_title")
        header.addWidget(title)
        header.addStretch()
        self._clock_label = QLabel(self)
        self._clock_label.setObjectName("launcher_clock")
        self._clock_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self._clock_label)
        root.addLayout(header)

        root.addStretch(1)
        center = QHBoxLayout()
        center.addStretch(1)
        self._card = QFrame(self)
        self._card.setObjectName("session_overlay_card")
        self._card.setMaximumWidth(760)
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(40, 34, 40, 28)
        card_layout.setSpacing(14)

        self._heading = QLabel(tr(language, "session_overlay_title"), self._card)
        self._heading.setObjectName("settings_heading")
        card_layout.addWidget(self._heading)
        self._message = QLabel(tr(language, "session_overlay_message"), self._card)
        self._message.setObjectName("settings_description")
        self._message.setWordWrap(True)
        card_layout.addWidget(self._message)
        card_layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton(tr(language, "cancel"), self._card)
        self.cancel_button.setObjectName("settings_back")
        self.confirm_button = QPushButton(tr(language, "session_overlay_confirm"), self._card)
        self.confirm_button.setObjectName("settings_footer")
        # Keep the old name for callers/tests that activate the return action.
        self.return_button = self.confirm_button
        for button in (self.cancel_button, self.confirm_button):
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.installEventFilter(self)
            buttons.addWidget(button)
        card_layout.addLayout(buttons)
        center.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)
        center.addStretch(1)
        root.addLayout(center)
        root.addStretch(1)

        self.cancel_button.clicked.connect(self.dismiss)
        self.confirm_button.clicked.connect(self._request_return)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        apply_theme(self, theme)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep the backdrop label matched to the dialog size."""
        super().resizeEvent(event)
        self._backdrop_label.setGeometry(self.rect())
        self._backdrop_label.lower()

    def open_overlay(self) -> None:
        """Capture the screen, raise the overlay, and focus cancel by default."""
        if self.isVisible():
            self.hide()
            QApplication.processEvents()
        fill = QColor(self._theme.colors["background"])
        self._backdrop = compose_backdrop(capture_blurred_screen(), fill)
        self._backdrop_label.setPixmap(self._backdrop)
        self._update_clock()
        present_fullscreen(self, stays_on_top=True)
        self._backdrop_label.setGeometry(self.rect())
        self._backdrop_label.lower()
        self._backdrop_label.show()
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._clock_timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        """Cancel pending fullscreen reasserts when the overlay hides."""
        cancel_fullscreen_reassert(self)
        super().hideEvent(event)

    def dismiss(self) -> None:
        """Hide the overlay without stopping the application."""
        if self.isVisible():
            self._clock_timer.stop()
            self.hide()
            self._backdrop = QPixmap()
            self._backdrop_label.clear()
            self.dismissed.emit()

    def handle_input(self, event: InputEvent) -> None:
        """Route remote/gamepad input; MENU/BACK abort, ACTIVATE clicks focus."""
        if not event.pressed:
            return
        if event.action in (InputAction.MENU, InputAction.HOME, InputAction.BACK):
            self.dismiss()
        elif event.action is InputAction.ACTIVATE:
            focus = self.focusWidget()
            if isinstance(focus, QPushButton):
                focus.click()
        elif event.action in (InputAction.LEFT, InputAction.RIGHT):
            target = self.confirm_button if event.action is InputAction.RIGHT else self.cancel_button
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Enter activates the focused button; Escape stays in the application."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            focus = self.focusWidget()
            if isinstance(focus, QPushButton):
                focus.click()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            target = self.confirm_button if event.key() == Qt.Key.Key_Right else self.cancel_button
            target.setFocus(Qt.FocusReason.OtherFocusReason)
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Move between cancel and confirm with left and right."""
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and watched in (self.cancel_button, self.confirm_button)
        ):
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                target = self.confirm_button if event.key() == Qt.Key.Key_Right else self.cancel_button
                target.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                watched.click()
                return True
        return super().eventFilter(watched, event)

    def _update_clock(self) -> None:
        """Refresh the date and time using the local system timezone."""
        self._clock_label.setText(clock_text(self._language))

    def _request_return(self) -> None:
        """Hide first so the launcher can restore without the overlay on top."""
        self._clock_timer.stop()
        self.hide()
        self._backdrop = QPixmap()
        self._backdrop_label.clear()
        self.return_requested.emit()
