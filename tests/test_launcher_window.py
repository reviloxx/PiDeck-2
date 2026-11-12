"""Qt tests for the fullscreen launcher presentation."""

import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtGui import QColor

from pideck.application.launcher import LauncherController
from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.launcher_window import LauncherWindow, calculate_tile_columns
from pideck.presentation.qt.power_dialog import ConfirmationDialog, PowerDialog
from pideck.presentation.qt.spinner import BusySpinner
from tests.test_configuration import configuration_document


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for the module."""
    return QApplication.instance() or QApplication([])


def three_application_configuration():
    """Return a configuration with three visible applications."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two", "three")
    ]
    document["home"]["visible_applications"] = []
    return YamlConfigurationParser().parse(document)


def launcher_window(application: QApplication) -> LauncherWindow:
    """Build a launcher with three configurable application tiles."""
    window = LauncherWindow(
        LauncherController(three_application_configuration()),
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
    """The launcher always shows a live date/time label."""
    window = launcher_window(application)

    assert window._clock_label.isVisible()
    assert len(window._clock_label.text()) >= 16
    assert window._clock_timer.isActive()
    window.close()


def test_german_launcher_localizes_controls_and_clock(application: QApplication) -> None:
    """German language changes launcher controls and date presentation."""
    window = launcher_window(application)
    window.set_language("de")

    assert window._settings_button.text() == "Einstellungen"
    assert window._shutdown_button.text() == "Ausschalten"
    assert "." in window._clock_label.text()
    window.close()


def test_power_error_keeps_launcher_open(application: QApplication) -> None:
    """A rejected power-off keeps the launcher open and explains the failure."""
    from PySide6.QtWidgets import QLabel

    window = launcher_window(application)
    window.set_language("de")

    window.show_power_error("Not authorized")

    assert window.isVisible()
    dialog = window.findChild(ConfirmationDialog)
    assert dialog is not None
    assert any("Not authorized" in label.text() for label in dialog.findChildren(QLabel))
    dialog.reject()
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
    assert animation.duration() == DEFAULT_THEME.animation.reduced_motion_duration_ms
    assert "wallpaper" in window.styleSheet()

    window.close()


def test_focus_animation_always_uses_reduced_motion_timing(application: QApplication) -> None:
    """Focus glow always uses the theme's reduced-motion duration."""
    window = launcher_window(application)
    animation = window._tiles[0].property("focus_animation")
    assert animation.duration() == DEFAULT_THEME.animation.reduced_motion_duration_ms
    window.close()


def test_wallpaper_covers_launcher_root_at_wqhd(
    application: QApplication, tmp_path: Path,
) -> None:
    """The actual launcher root scales wallpaper beyond its native SVG size."""
    path = tmp_path / "wallpaper.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
        '<rect width="100" height="50" fill="#ab4512"/></svg>',
        encoding="utf-8",
    )
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = LauncherWindow(
        LauncherController(configuration), replace(DEFAULT_THEME, wallpaper=path), tmp_path,
    )
    window.resize(2560, 1440)
    window.show()
    application.processEvents()
    root = window._root.grab().toImage()
    for x, y in ((0, 0), (root.width() - 1, 0), (0, root.height() - 1)):
        assert root.pixelColor(x, y) == QColor("#ab4512")
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
    spinner = window._tiles[0].findChild(BusySpinner, "tile_spinner")
    assert spinner.isVisible()
    assert spinner.geometry().left() == window._tiles[0].width() - 32
    assert spinner.geometry().top() == 8

    window.notify_session_visible(target)
    application.processEvents()

    assert window.isVisible() is False
    assert spinner.isVisible() is False
    window.close()


def test_visibility_timeout_hides_launcher(application: QApplication) -> None:
    """An unverified window still clears the launcher so it cannot restack."""
    window = launcher_window(application)
    target = window._tiles[0].application

    window.notify_session_started(target, None)
    application.processEvents()
    window.notify_visibility_timeout(target)
    application.processEvents()

    assert window.isVisible() is False
    assert window._running_identifier == target.identifier
    window.close()


def test_hide_cancels_pending_fullscreen_reassert(application: QApplication) -> None:
    """Pending cold-start reassert timers are dropped when the launcher hides."""
    window = launcher_window(application)
    window.schedule_fullscreen_reassert(60_000)
    assert len(window._fullscreen_timers) == 1

    window.hide()
    application.processEvents()

    assert window._fullscreen_timers == []
    window.close()


def test_enforce_fullscreen_skips_when_application_running(application: QApplication) -> None:
    """Fullscreen reassert must not raise the launcher over a running app."""
    window = launcher_window(application)
    window.show_launcher()
    application.processEvents()
    window.set_running_application("one")
    window.hide()
    application.processEvents()

    window._enforce_fullscreen()

    assert window.isVisible() is False
    window.close()


def test_arrow_keys_on_tile_use_grid_navigation(application: QApplication) -> None:
    """Arrow keys delivered to a focused tile move by grid coordinates."""
    window = launcher_window(application)

    QTest.keyClick(window._tiles[0], Qt.Key.Key_Down)

    assert window._controller.state.focused_index == 2
    assert window._tiles[2].hasFocus()

    window.close()


def test_tab_focused_tile_is_activated_and_used_for_navigation(application: QApplication) -> None:
    """Tab and Enter act on the visibly focused tile, not a stale grid selection."""
    window = launcher_window(application)
    requested: list[str] = []
    window.application_requested.connect(lambda app: requested.append(app.identifier))
    QTest.keyClick(window._tiles[0], Qt.Key.Key_Tab)
    assert window._tiles[1].hasFocus()
    assert window._controller.state.focused_index == 1
    QTest.keyClick(window._tiles[1], Qt.Key.Key_Return)
    assert requested == ["two"]
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requested == ["two", "two"]
    QTest.keyClick(window._tiles[1], Qt.Key.Key_Right)
    assert window._tiles[1].hasFocus()
    QTest.keyClick(window._tiles[1], Qt.Key.Key_Left)
    QTest.keyClick(window._tiles[0], Qt.Key.Key_Down)
    assert window._tiles[2].hasFocus()
    window.close()


def test_mouse_focused_tile_updates_controller_selection(application: QApplication) -> None:
    """Mouse activation and subsequent directional input use the same selection."""
    window = launcher_window(application)
    QTest.mouseClick(window._tiles[1], Qt.MouseButton.LeftButton)
    assert window._controller.state.focused_index == 1
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert window._tiles[1].hasFocus()
    window.handle_input(InputEvent(InputAction.LEFT, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
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
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert window._shutdown_button.hasFocus()
    activated: list[bool] = []
    window.shutdown_requested.connect(lambda: activated.append(True))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert activated == []
    dialog = window.findChild(PowerDialog)
    assert dialog is not None and dialog.cancel.hasFocus()
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert dialog.confirm.hasFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert activated == [True]
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


def test_enter_activates_focused_footer_without_launching_an_application(
    application: QApplication,
) -> None:
    """Return and keypad Enter activate the selected footer action only."""
    window = launcher_window(application)
    requested: list[str] = []
    window.settings_requested.connect(lambda: requested.append("settings"))
    window.shutdown_requested.connect(lambda: requested.append("shutdown"))
    window.application_requested.connect(lambda app: requested.append(app.identifier))

    QTest.keyClick(window._tiles[0], Qt.Key.Key_Down)
    QTest.keyClick(window._tiles[2], Qt.Key.Key_Down)
    QTest.keyClick(window._settings_button, Qt.Key.Key_Return)
    QTest.keyClick(window._settings_button, Qt.Key.Key_Right)
    QTest.keyClick(window._shutdown_button, Qt.Key.Key_Enter)
    application.processEvents()
    assert requested == ["settings"]
    window.findChild(PowerDialog).confirm.click()
    application.processEvents()

    assert requested == ["settings", "shutdown"]
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
    application.processEvents()
    assert shutdown_requested == []
    window.findChild(PowerDialog).confirm.click()
    application.processEvents()

    assert settings_requested == [True]
    assert shutdown_requested == [True]

    window.close()


def test_escape_and_back_never_shut_down_from_home(application: QApplication) -> None:
    """Back is a no-op on the home screen, so a remote cannot power off by accident."""
    window = launcher_window(application)
    shutdown_requested: list[bool] = []
    window.shutdown_requested.connect(lambda: shutdown_requested.append(True))

    QTest.keyClick(window._tiles[0], Qt.Key.Key_Escape)
    window.handle_input(InputEvent(InputAction.BACK, "cec"))
    application.processEvents()

    assert shutdown_requested == []
    assert window.findChild(PowerDialog) is None
    window.close()


def test_cancelled_shutdown_returns_focus_to_shutdown(application: QApplication) -> None:
    """Cancelling the confirmation keeps PiDeck running and restores footer focus."""
    window = launcher_window(application)
    shutdown_requested: list[bool] = []
    window.shutdown_requested.connect(lambda: shutdown_requested.append(True))

    window.request_shutdown()
    application.processEvents()
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    application.processEvents()

    assert shutdown_requested == []
    assert window.focusWidget() is window._shutdown_button
    window.close()


def test_reapplying_configuration_keeps_launcher_fonts(application: QApplication) -> None:
    """Saving any setting must not change the launcher's typography."""
    window = launcher_window(application)
    labels = (window._settings_button, window._shutdown_button)
    before = [(label.font().family(), label.fontInfo().pixelSize()) for label in labels]

    window.apply_configuration(three_application_configuration(), DEFAULT_THEME)
    application.processEvents()

    after = [(label.font().family(), label.fontInfo().pixelSize()) for label in labels]
    assert after == before
    assert before[0][1] == DEFAULT_THEME.fonts.body_size
    window.close()
