"""Qt tests for the fullscreen launcher presentation."""

import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from pideck.application.launcher import LauncherController
from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.launcher_window import LauncherWindow, calculate_tile_columns
from tests.test_configuration import configuration_document


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for the module."""
    return QApplication.instance() or QApplication([])


def launcher_window(application: QApplication) -> LauncherWindow:
    """Build a launcher with three configurable application tiles."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two", "three")
    ]
    document["home"]["visible_applications"] = []
    configuration = YamlConfigurationParser().parse(document)
    window = LauncherWindow(
        LauncherController(configuration),
        DEFAULT_THEME,
        Path.cwd(),
    )
    window.resize(800, 600)
    window.show()
    application.processEvents()
    return window


def test_launcher_renders_tiles_and_focuses_first(application: QApplication) -> None:
    """The first application tile receives focus when the window is shown."""
    window = launcher_window(application)

    assert len(window.findChildren(type(window._tiles[0]))) == 3
    assert window._tiles[0].hasFocus()
    assert all(tile.height() == DEFAULT_THEME.tile.height for tile in window._tiles)

    window.close()


def test_clock_is_visible_and_formatted_by_default(application: QApplication) -> None:
    """The launcher shows a live date/time label by default."""
    window = launcher_window(application)

    assert window._clock_label.isVisible()
    assert len(window._clock_label.text()) >= 16

    window.set_clock_visible(False)
    assert window._clock_label.isVisible() is False
    window.close()


def test_german_launcher_localizes_controls_and_clock(application: QApplication) -> None:
    """German language changes launcher controls and date presentation."""
    window = launcher_window(application)
    window.set_language("de")

    assert window._settings_button.text() == "Einstellungen"
    assert window._shutdown_button.text() == "Herunterfahren"
    assert "." in window._clock_label.text()
    window.close()


def test_show_launcher_enters_fullscreen(application: QApplication) -> None:
    """The launcher entry point requests fullscreen presentation."""
    window = launcher_window(application)

    window.show_launcher()
    application.processEvents()

    assert window.isFullScreen()
    assert window.geometry() == window.screen().geometry()

    window.close()


def test_show_launcher_selects_first_application(application: QApplication) -> None:
    """The initial launcher presentation always selects the first tile."""
    window = launcher_window(application)
    QTest.keyClick(window._tiles[0], Qt.Key.Key_Right)

    window.show_launcher()
    application.processEvents()

    assert window._controller.state.focused_index == 0
    assert window._tiles[0].hasFocus()

    window.close()


def test_theme_animation_and_running_state_are_applied(application: QApplication) -> None:
    """Theme timing and running state reach the rendered tile."""
    window = launcher_window(application)
    window.setStyleSheet(
        window.styleSheet()
        + " QWidget#launcher_root { background-image: url('assets/themes/default/wallpaper.svg'); }"
    )

    window.set_running_application("two")
    animation = window._tiles[1].property("focus_animation")

    assert window._tiles[1].property("running") is True
    assert window._tiles[0].property("running") is False
    assert animation.duration() == 180
    assert "wallpaper" in window.styleSheet()

    window.close()


def test_reduced_motion_disables_focus_animation(application: QApplication) -> None:
    """Reduced-motion configuration uses the theme's zero-duration timing."""
    window = launcher_window(application)
    reduced_theme = replace(
        DEFAULT_THEME,
        wallpaper=Path("assets/themes/default/wallpaper.svg"),
    )
    reduced_window = LauncherWindow(
        window._controller,
        reduced_theme,
        Path.cwd(),
        reduced_motion=True,
    )
    reduced_window.show()
    application.processEvents()

    animation = reduced_window._tiles[0].property("focus_animation")
    assert animation.duration() == 0

    reduced_window.close()
    window.close()


def test_arrow_keys_move_focus_and_enter_emits_application(application: QApplication) -> None:
    """Keyboard navigation updates the controller and activation emits intent."""
    window = launcher_window(application)
    requested: list[str] = []
    window.application_requested.connect(lambda app: requested.append(app.identifier))

    QTest.keyClick(window, Qt.Key.Key_Right)
    QTest.keyClick(window, Qt.Key.Key_Return)

    assert requested == ["two"]
    assert window._tiles[1].hasFocus()

    window.close()


def test_launcher_waits_for_visibility_before_hiding(application: QApplication) -> None:
    """Process start shows a spinner; visibility then hides the launcher."""
    window = launcher_window(application)
    target = window._tiles[0].application

    window.notify_session_started(target, None)
    application.processEvents()

    assert window.isVisible()
    spinner = window._tiles[0].findChild(type(window._status_label), "tile_spinner")
    assert spinner.isVisible()
    assert spinner.geometry().left() == window._tiles[0].width() - 32
    assert spinner.geometry().top() == 8

    window.notify_session_visible(target)
    application.processEvents()

    assert window.isVisible() is False
    assert spinner.isVisible() is False
    window.close()


def test_arrow_keys_on_tile_use_grid_navigation(application: QApplication) -> None:
    """Arrow keys delivered to a focused tile move by grid coordinates."""
    window = launcher_window(application)

    QTest.keyClick(window._tiles[0], Qt.Key.Key_Down)

    assert window._controller.state.focused_index == 2
    assert window._tiles[2].hasFocus()

    window.close()


def test_gamepad_dpad_and_activate_navigate_launcher(application: QApplication) -> None:
    """Normalized gamepad actions move and activate launcher tiles."""
    window = launcher_window(application)
    requested: list[str] = []
    window.application_requested.connect(lambda app: requested.append(app.identifier))

    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))

    assert window._controller.state.focused_index == 2
    assert requested == ["three"]
    window.close()


def test_gamepad_down_from_last_tile_row_focuses_settings(application: QApplication) -> None:
    """Gamepad Down uses the same footer boundary as tile keyboard navigation."""
    window = launcher_window(application)

    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))

    assert window._settings_button.hasFocus()
    window.close()


def test_arrow_keys_reach_settings_and_shutdown(application: QApplication) -> None:
    """The last tile row leads to Settings, then horizontally to Shutdown."""
    window = launcher_window(application)

    QTest.keyClick(window._tiles[0], Qt.Key.Key_Down)
    QTest.keyClick(window._tiles[2], Qt.Key.Key_Down)
    assert window._settings_button.hasFocus()

    QTest.keyClick(window._settings_button, Qt.Key.Key_Right)
    assert window._shutdown_button.hasFocus()

    QTest.keyClick(window._shutdown_button, Qt.Key.Key_Left)
    assert window._settings_button.hasFocus()

    QTest.keyClick(window._settings_button, Qt.Key.Key_Up)
    assert window._tiles[2].hasFocus()

    window.close()


def test_grid_shape_balances_application_count_and_screen_space() -> None:
    """The grid uses multiple rows for six apps and narrows on small screens."""
    assert calculate_tile_columns(6, 1886, 935, 240, 135, 16) == 3
    assert calculate_tile_columns(6, 704, 420, 240, 135, 16) == 2


def test_settings_and_shutdown_are_intents(application: QApplication) -> None:
    """Footer controls emit intents without performing system operations."""
    window = launcher_window(application)
    settings_requested: list[bool] = []
    shutdown_requested: list[bool] = []
    window.settings_requested.connect(lambda: settings_requested.append(True))
    window.shutdown_requested.connect(lambda: shutdown_requested.append(True))

    buttons = window.findChildren(QPushButton)
    action_buttons = [button for button in buttons if button.objectName() == "launcher_action"]
    action_buttons[0].click()
    action_buttons[1].click()

    assert settings_requested == [True]
    assert shutdown_requested == [True]

    window.close()
