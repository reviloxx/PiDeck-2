"""Qt tests for the in-app session overlay."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.session_overlay import SessionOverlay


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for the module."""
    return QApplication.instance() or QApplication([])


def test_activate_requests_return_to_pideck(application: QApplication) -> None:
    """OK / Activate clicks Back to PiDeck and emits return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.open_overlay()
    overlay.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert returns == ["return"]
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


def test_back_does_not_dismiss_or_return(application: QApplication) -> None:
    """Return/Back on the overlay must not quit the application or dismiss it."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.open_overlay()
    overlay.handle_input(InputEvent(InputAction.BACK, "cec"))
    assert returns == []
    assert overlay.isVisible()
    overlay.hide()


def test_button_click_requests_return(application: QApplication) -> None:
    """Clicking the primary button stops the application via return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "de")
    returns: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.open_overlay()
    overlay.return_button.click()
    assert returns == ["return"]
    assert not overlay.isVisible()
