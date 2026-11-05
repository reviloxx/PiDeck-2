"""Offscreen tests for shared presentation widgets and input-driven cursor hiding."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from pideck.presentation.qt.cursor_visibility import CursorVisibility
from pideck.presentation.qt.spinner import BusySpinner
from pideck.presentation.qt.toggle_switch import ToggleSwitch


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one offscreen Qt application."""
    return QApplication.instance() or QApplication([])


def _key_press() -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)


def _mouse(event_type: QEvent.Type) -> QMouseEvent:
    return QMouseEvent(
        event_type, QPointF(1, 1), QPointF(1, 1),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )


def test_keyboard_hides_and_mouse_click_restores_cursor(application: QApplication) -> None:
    """Non-mouse navigation hides the pointer until the mouse is used again."""
    cursor = CursorVisibility()
    target = QWidget()
    before = QGuiApplication.overrideCursor()

    assert cursor.eventFilter(target, _key_press()) is False
    assert cursor.hidden
    assert QGuiApplication.overrideCursor().shape() == Qt.CursorShape.BlankCursor

    cursor.eventFilter(target, _mouse(QEvent.Type.MouseButtonPress))
    assert not cursor.hidden
    assert QGuiApplication.overrideCursor() == before


def test_synthetic_move_without_pointer_motion_keeps_cursor_hidden(application: QApplication) -> None:
    """Windows appearing under a resting pointer must not reveal it again."""
    cursor = CursorVisibility()
    before = QGuiApplication.overrideCursor()
    cursor.hide_cursor()
    cursor.hide_cursor()

    cursor.eventFilter(QWidget(), _mouse(QEvent.Type.MouseMove))

    assert cursor.hidden
    cursor.show_cursor()
    assert QGuiApplication.overrideCursor() == before


def test_toggle_switch_names_both_states(application: QApplication) -> None:
    """The switch reports Off when unchecked instead of a permanent On label."""
    switch = ToggleSwitch()
    switch.set_state_texts("Ein", "Aus")

    assert switch.state_text() == "Aus"
    switch.toggle()
    assert switch.isChecked()
    assert switch.state_text() == "Ein"
    assert switch.sizeHint().width() > 52


def test_spinner_only_animates_while_running(application: QApplication) -> None:
    """The busy indicator stops its timer when hidden."""
    parent = QWidget()
    spinner = BusySpinner(20, parent)
    parent.show()

    spinner.set_running(True)
    assert spinner.running and spinner.isVisible()
    spinner.set_running(False)
    assert not spinner.running and not spinner.isVisible()
    parent.close()


def test_hover_moves_focus_so_only_one_control_is_highlighted(application: QApplication) -> None:
    """Pointer hover focuses a control; a stationary pointer cannot steal focus later."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout

    window = QWidget()
    layout = QVBoxLayout(window)
    first, second = QPushButton("First", window), QPushButton("Second", window)
    layout.addWidget(first)
    layout.addWidget(second)
    window.show()
    application.processEvents()
    window.activateWindow()
    second.setFocus()
    cursor = CursorVisibility()
    cursor._last_hover_at = None

    cursor.eventFilter(first, QEvent(QEvent.Type.Enter))
    assert first.hasFocus()

    second.setFocus()
    cursor.eventFilter(first, QEvent(QEvent.Type.Enter))
    assert second.hasFocus()
    window.close()


def test_hover_is_ignored_while_the_pointer_is_hidden(application: QApplication) -> None:
    """Keyboard, controller and remote navigation keep their focus target."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout

    window = QWidget()
    layout = QVBoxLayout(window)
    first, second = QPushButton("First", window), QPushButton("Second", window)
    layout.addWidget(first)
    layout.addWidget(second)
    window.show()
    application.processEvents()
    second.setFocus()
    cursor = CursorVisibility()
    cursor.hide_cursor()

    cursor.eventFilter(first, QEvent(QEvent.Type.Enter))

    assert second.hasFocus()
    cursor.show_cursor()
    window.close()


def test_theme_never_styles_hover() -> None:
    """Only focus is highlighted, so a hovered and a focused item cannot both glow."""
    from pathlib import Path

    from pideck.presentation.qt import theme

    stylesheet = Path(theme.__file__).read_text(encoding="utf-8")
    assert ":hover" not in stylesheet.split('widget.setStyleSheet(', 1)[1]


def test_hover_never_focuses_the_window_itself(application: QApplication) -> None:
    """Pointing at a window's background keeps the selected control focused."""
    from PySide6.QtCore import Qt as QtNamespace
    from PySide6.QtWidgets import QPushButton, QVBoxLayout

    window = QWidget()
    window.setFocusPolicy(QtNamespace.FocusPolicy.StrongFocus)
    background = QWidget(window)
    button = QPushButton("Tile", window)
    layout = QVBoxLayout(window)
    layout.addWidget(background)
    layout.addWidget(button)
    window.show()
    application.processEvents()
    button.setFocus()
    cursor = CursorVisibility()
    cursor._last_hover_at = None

    cursor.eventFilter(background, QEvent(QEvent.Type.Enter))
    cursor.eventFilter(window, QEvent(QEvent.Type.Enter))

    assert button.hasFocus()
    window.close()


def test_resting_pointer_at_startup_does_not_take_focus(application: QApplication) -> None:
    """The first Enter event without pointer movement leaves keyboard focus alone."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout

    window = QWidget()
    layout = QVBoxLayout(window)
    first, second = QPushButton("First", window), QPushButton("Second", window)
    layout.addWidget(first)
    layout.addWidget(second)
    window.show()
    application.processEvents()
    second.setFocus()

    CursorVisibility().eventFilter(first, QEvent(QEvent.Type.Enter))

    assert second.hasFocus()
    window.close()
