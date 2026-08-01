"""Qt tests for the Milestone 5 settings dialog."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pideck.application.settings import SettingsUpdate
from pideck.application.ports.updates import UpdateInfo, UpdateStatus
from pideck.domain.configuration import Configuration
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for settings tests."""
    return QApplication.instance() or QApplication([])


def settings_window(application: QApplication) -> SettingsWindow:
    """Build a settings dialog from representative configuration."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd())
    window.show()
    application.processEvents()
    return window


def test_settings_window_loads_current_values(application: QApplication) -> None:
    """The dialog reflects the current theme, motion, and application selection."""
    window = settings_window(application)

    assert window._theme_combo.currentData() == "default"
    assert window._reduced_motion.isChecked() is False
    assert window._applications.count() == 1
    assert window._applications.item(0).checkState() is Qt.CheckState.Checked

    window.close()


def test_settings_window_emits_typed_update(application: QApplication) -> None:
    """Saving the dialog emits a typed update instead of editing YAML directly."""
    window = settings_window(application)
    updates: list[SettingsUpdate] = []
    window.settings_submitted.connect(updates.append)
    window._reduced_motion.setChecked(True)

    window._submit()

    assert updates == [SettingsUpdate("default", True, ("browser",))]
    window.close()


def test_settings_window_emits_changes_immediately(application: QApplication) -> None:
    """Changing a value emits the update without a Save action."""
    window = settings_window(application)
    updates: list[SettingsUpdate] = []
    window.settings_changed.connect(updates.append)

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Right)
    QTest.keyClick(window._theme_row, Qt.Key.Key_Down)
    QTest.keyClick(window._motion_row, Qt.Key.Key_Space)

    assert updates
    assert updates[-1].reduced_motion is True
    assert not hasattr(window, "_save_button")
    assert not hasattr(window, "_cancel_button")
    window.close()


def test_application_visibility_change_emits_immediately(application: QApplication) -> None:
    """Toggling a home-screen application emits a persisted settings update."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two")
    ]
    document["home"]["visible_applications"] = ["one", "two"]
    configuration = YamlConfigurationParser().parse(document)
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd())
    updates: list[SettingsUpdate] = []
    window.settings_changed.connect(updates.append)
    window.show()
    application.processEvents()

    window._applications.item(0).setCheckState(Qt.CheckState.Unchecked)
    application.processEvents()

    assert updates
    assert updates[-1].visible_applications == ("two",)
    window.close()


def test_settings_window_supports_dpad_navigation(application: QApplication) -> None:
    """Arrow keys move through categories, controls, applications, and actions."""
    window = settings_window(application)

    assert window._nav_buttons[0].hasFocus()
    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Left)
    assert window._nav_buttons[0].hasFocus()
    assert window._nav_buttons[0].property("active") is True
    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Right)
    assert window._theme_row.hasFocus()
    QTest.keyClick(window._theme_row, Qt.Key.Key_Down)
    assert window._motion_row.hasFocus()
    QTest.keyClick(window._motion_row, Qt.Key.Key_Space)
    assert window._reduced_motion.isChecked() is True
    QTest.keyClick(window._motion_row, Qt.Key.Key_Return)
    assert window._reduced_motion.isChecked() is False
    QTest.keyClick(window._motion_row, Qt.Key.Key_Up)
    QTest.keyClick(window._theme_row, Qt.Key.Key_Up)
    assert window._nav_buttons[0].hasFocus()
    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    assert window._nav_buttons[1].hasFocus()
    assert window._nav_buttons[1].property("active") is True
    assert window._nav_buttons[0].property("active") is False
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Down)
    assert window._nav_buttons[2].hasFocus()
    QTest.keyClick(window._nav_buttons[2], Qt.Key.Key_Down)
    assert window._back_button.hasFocus()
    QTest.keyClick(window._back_button, Qt.Key.Key_Space)
    assert window.isVisible() is False
    return


def test_home_application_list_accepts_keyboard_navigation(application: QApplication) -> None:
    """The home-screen list moves between application rows before handing off."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two", "three")
    ]
    document["home"]["visible_applications"] = []
    configuration = YamlConfigurationParser().parse(document)
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd())
    window.show()
    application.processEvents()

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Right)
    assert window._applications.hasFocus()
    assert window._applications.currentRow() == 0
    QTest.keyClick(window._applications, Qt.Key.Key_Down)
    assert window._applications.currentRow() == 1
    QTest.keyClick(window._applications, Qt.Key.Key_Up)
    assert window._applications.currentRow() == 0
    QTest.keyClick(window._applications, Qt.Key.Key_Left)
    assert window._nav_buttons[1].hasFocus()
    assert [button.property("active") for button in window._nav_buttons] == [False, True, False]
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Down)
    assert window._nav_buttons[2].hasFocus()
    QTest.keyClick(window._nav_buttons[2], Qt.Key.Key_Down)
    assert window._back_button.hasFocus()
    assert [button.property("active") for button in window._nav_buttons] == [False, False, False]

    window.close()


def test_home_page_entry_and_exit_control_list_selection(application: QApplication) -> None:
    """Rail navigation leaves the list empty; Right enters its first item."""
    window = settings_window(application)

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    assert window._nav_buttons[1].hasFocus()
    assert window._applications.currentRow() == -1

    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Right)
    assert window._applications.hasFocus()
    assert window._applications.currentRow() == 0

    QTest.keyClick(window._applications, Qt.Key.Key_Left)
    assert window._nav_buttons[1].hasFocus()
    assert window._applications.currentRow() == -1
    assert window._nav_buttons[1].property("active") is True

    window.close()


def test_updates_page_renders_status_spinner_and_navigates(application: QApplication) -> None:
    """Updates rows show availability, spinner state, and support vertical focus."""
    window = settings_window(application)

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Down)
    QTest.keyClick(window._nav_buttons[2], Qt.Key.Key_Right)
    row = window._update_rows["browser"]
    assert row.hasFocus()

    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.AVAILABLE))
    assert row.action.isEnabled()
    assert row.action.text() == "Update"
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UPDATING))
    assert row.spinner.isVisible()
    assert row.action.text() == "Cancel"
    QTest.keyClick(row, Qt.Key.Key_Down)
    assert window._update_rows["browser"].hasFocus()

    window.close()
