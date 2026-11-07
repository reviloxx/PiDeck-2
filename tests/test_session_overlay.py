"""Qt tests for the in-app session overlay."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.session_overlay import (
    SessionOverlay,
    blur_pixmap,
    compose_backdrop,
    grab_is_useful,
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for the module."""
    return QApplication.instance() or QApplication([])


def test_confirm_requests_return_to_pideck(application: QApplication) -> None:
    """Confirming the dialog stops the application via return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.open_overlay()
    overlay.confirm_button.setFocus()
    overlay.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert returns == ["return"]
    assert not overlay.isVisible()


def test_cancel_button_dismisses(application: QApplication) -> None:
    """Cancel closes the overlay without return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    dismissals: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.dismissed.connect(lambda: dismissals.append("dismissed"))
    overlay.open_overlay()
    overlay.cancel_button.click()
    assert returns == []
    assert dismissals == ["dismissed"]
    assert not overlay.isVisible()


def test_menu_dismisses_without_stopping_the_application(application: QApplication) -> None:
    """Exit / Guide toggles dismiss the overlay without return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    dismissals: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.dismissed.connect(lambda: dismissals.append("dismissed"))
    overlay.open_overlay()
    overlay.handle_input(InputEvent(InputAction.MENU, "cec"))
    assert returns == []
    assert dismissals == ["dismissed"]
    assert not overlay.isVisible()


def test_back_dismisses_without_stopping_the_application(application: QApplication) -> None:
    """Return/Back dismisses the overlay without quitting the application."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    dismissals: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.dismissed.connect(lambda: dismissals.append("dismissed"))
    overlay.open_overlay()
    overlay.handle_input(InputEvent(InputAction.BACK, "cec"))
    assert returns == []
    assert dismissals == ["dismissed"]
    assert not overlay.isVisible()


def test_confirm_button_requests_return(application: QApplication) -> None:
    """Clicking Return home stops the application via return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "de")
    returns: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.open_overlay()
    overlay.confirm_button.click()
    assert returns == ["return"]
    assert not overlay.isVisible()


def test_overlay_shows_home_chrome_and_confirmation(application: QApplication) -> None:
    """The overlay mirrors the home header and asks to return home."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    overlay.open_overlay()
    assert overlay._heading.text() == "Return home?"
    assert "home screen" in overlay._message.text()
    assert overlay.confirm_button.text() == "Return home"
    assert overlay.cancel_button.text() == "Cancel"
    assert overlay._clock_label.text()
    overlay.hide()


def test_open_overlay_tolerates_empty_screen_grab(
    application: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unusable grabs fall back to a solid themed backdrop instead of crashing."""
    monkeypatch.setattr(
        "pideck.presentation.qt.session_overlay.capture_blurred_screen",
        lambda: QPixmap(),
    )
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    overlay.open_overlay()
    assert overlay.isVisible()
    assert not overlay._backdrop.isNull()
    assert not overlay._backdrop_label.pixmap().isNull()
    overlay.dismiss()


def test_blur_pixmap_returns_same_size(application: QApplication) -> None:
    """Blur preserves the source size for full-bleed painting."""
    source = _varied_pixmap()
    blurred = blur_pixmap(source, scale=2, radius=8)
    assert not blurred.isNull()
    assert blurred.size() == source.size()


def test_blur_pixmap_rejects_empty_source(application: QApplication) -> None:
    """An empty grab produces an empty blur result."""
    assert blur_pixmap(QPixmap()).isNull()


def test_grab_is_useful_rejects_flat_black(application: QApplication) -> None:
    """Solid black captures from uncomposited GL windows are rejected."""
    black = QPixmap(64, 48)
    black.fill(QColor("#000000"))
    assert grab_is_useful(black) is False
    assert grab_is_useful(_varied_pixmap()) is True


def test_compose_backdrop_always_opaque(application: QApplication) -> None:
    """The shown frame is always an opaque pixmap with the scrim applied."""
    frame = compose_backdrop(_varied_pixmap(), QColor("#112233"))
    assert not frame.isNull()
    empty = compose_backdrop(QPixmap(), QColor("#112233"))
    assert not empty.isNull()


def test_open_overlay_refreshes_backdrop_each_time(
    application: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each open captures a new backdrop instead of reusing the previous blur."""
    frames = [_varied_pixmap(QColor("#112233")), _varied_pixmap(QColor("#aabbcc"))]

    def capture() -> QPixmap:
        return frames.pop(0)

    monkeypatch.setattr(
        "pideck.presentation.qt.session_overlay.capture_blurred_screen", capture,
    )
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    overlay.open_overlay()
    first = overlay._backdrop.cacheKey()
    overlay.dismiss()
    overlay.open_overlay()
    second = overlay._backdrop.cacheKey()
    assert first != second
    overlay.dismiss()


def _varied_pixmap(base: QColor | None = None) -> QPixmap:
    """Build a non-flat pixmap that passes the useful-grab check."""
    pixmap = QPixmap(64, 48)
    color = base or QColor("#336699")
    pixmap.fill(color)
    painter = __import__("PySide6.QtGui", fromlist=["QPainter"]).QPainter(pixmap)
    painter.fillRect(0, 0, 32, 48, QColor("#99ccff"))
    painter.end()
    return pixmap
