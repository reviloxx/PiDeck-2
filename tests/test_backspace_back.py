"""Qt tests for Backspace as Back and for the settings rail's lower end."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QVBoxLayout

from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.backspace_back import BackspaceAsBack
from pideck.presentation.qt.network_window import WirelessKeyDialog
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document


@pytest.fixture(scope="module")
def application():
    """Create one offscreen Qt application with the Backspace filter installed."""
    application = QApplication.instance() or QApplication([])
    backspace = BackspaceAsBack(application)
    application.installEventFilter(backspace)
    yield application
    application.removeEventFilter(backspace)


def settings(application: QApplication) -> SettingsWindow:
    """Open settings with focus on the category rail."""
    window = SettingsWindow(YamlConfigurationParser().parse(configuration_document()), DEFAULT_THEME, Path.cwd())
    window.show()
    application.processEvents()
    window._focus_nav(0)
    return window


def test_backspace_closes_settings_from_rail_and_rows(application: QApplication) -> None:
    """Backspace leaves settings like Escape, from the rail and from page rows."""
    for focus_row in (False, True):
        window = settings(application)
        if focus_row:
            window._select_page(1)
            window._focus_first_page_control()
            assert window.focusWidget() in window._page_controls[1]
        QTest.keyClick(window.focusWidget(), Qt.Key.Key_Backspace)
        assert window.result() == QDialog.DialogCode.Rejected and not window.isVisible()


def test_backspace_closes_only_the_topmost_menu(application: QApplication) -> None:
    """In the application menu Backspace closes the menu and keeps settings open."""
    window = settings(application)
    window._select_page(1)
    window._focus_first_page_control()
    QTest.keyClick(window.focusWidget(), Qt.Key.Key_Return)
    application.processEvents()
    menu = window._apps.dialog
    assert menu is not None
    QTest.keyClick(menu.focusWidget(), Qt.Key.Key_Backspace)
    application.processEvents()
    assert window._apps.dialog is None and window.isVisible()
    window.close()


def test_backspace_edits_text_fields_and_ignores_repeats(application: QApplication) -> None:
    """Text fields and the on-screen keyboard delete; a held key does not go back repeatedly."""
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    entry = QLineEdit("ab", dialog)
    button = QPushButton("ok", dialog)
    layout.addWidget(entry)
    layout.addWidget(button)
    dialog.show()
    entry.setFocus()
    QTest.keyClick(entry, Qt.Key.Key_Backspace)
    assert entry.text() == "a" and dialog.isVisible()
    button.setFocus()
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier, "", True)
    QApplication.sendEvent(button, repeat)
    assert dialog.isVisible()
    QTest.keyClick(button, Qt.Key.Key_Backspace)
    assert not dialog.isVisible()

    keys = WirelessKeyDialog(DEFAULT_THEME, "en", dialog)
    keys.show()
    application.processEvents()
    keys.entry.setText("secret")
    QTest.keyClick(keys.focusWidget(), Qt.Key.Key_Backspace)
    assert keys.entry.text() == "secre" and keys.isVisible()
    keys.close()


def test_moving_past_back_keeps_a_visible_focus(application: QApplication) -> None:
    """Down, Left and Right on Back keep focus there instead of losing the highlight."""
    window = settings(application)
    for _ in window._nav_buttons:
        QTest.keyClick(window.focusWidget(), Qt.Key.Key_Down)
    assert window.focusWidget() is window._back_button
    for key in (Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
        QTest.keyClick(window.focusWidget(), key)
        assert window.focusWidget() is window._back_button
    QTest.keyClick(window.focusWidget(), Qt.Key.Key_Up)
    assert window.focusWidget() is window._nav_buttons[-1]
    window.close()
