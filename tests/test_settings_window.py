"""Qt tests for the Milestone 5 settings dialog."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pideck.application.settings import SettingsUpdate
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
