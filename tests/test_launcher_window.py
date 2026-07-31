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
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.launcher_window import LauncherWindow
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


def test_show_launcher_enters_fullscreen(application: QApplication) -> None:
    """The launcher entry point requests fullscreen presentation."""
    window = launcher_window(application)

    window.show_launcher()
    application.processEvents()

    assert window.isFullScreen()
    assert window.geometry() == window.screen().geometry()

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


def test_arrow_keys_on_tile_use_grid_navigation(application: QApplication) -> None:
    """Arrow keys delivered to a focused tile move by grid coordinates."""
    window = launcher_window(application)

    QTest.keyClick(window._tiles[0], Qt.Key.Key_Down)

    assert window._controller.state.focused_index == 2
    assert window._tiles[2].hasFocus()

    window.close()


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
