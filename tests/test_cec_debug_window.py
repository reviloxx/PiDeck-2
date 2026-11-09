"""Tests for the CEC remote debug window."""

from PySide6.QtWidgets import QApplication, QLabel, QWidget
import pytest

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.input_cec import CecKeyObservation
from pideck.presentation.qt.cec_debug_window import CecDebugWindow, format_cec_observation


@pytest.fixture
def application() -> QApplication:
    """Reuse a process-wide QApplication for widget tests."""
    return QApplication.instance() or QApplication([])


def test_format_cec_observation_shows_mapped_and_unmapped() -> None:
    """Debug lines include the hex code, libCEC label, and mapping result."""
    mapped = CecKeyObservation(0x64, "stop function", InputAction.STOP)
    unmapped = CecKeyObservation(0xA5, "unknown", None)
    assert format_cec_observation(mapped, "en") == "0x64  stop function  →  Stop"
    assert format_cec_observation(unmapped, "en") == "0xa5  unknown  →  Unmapped"
    assert "Nicht zugeordnet" in format_cec_observation(unmapped, "de")


def test_cec_debug_window_lists_history_newest_first(application: QApplication) -> None:
    """History and live appends render with the newest key at the top."""
    parent = QWidget()
    window = CecDebugWindow(DEFAULT_THEME, "en", parent)
    window.set_history((
        CecKeyObservation(0x44, "play", InputAction.PLAY),
        CecKeyObservation(0xA5, "unknown", None),
    ))
    labels = [
        label.text()
        for label in window.findChildren(QLabel)
        if label.property("cec_debug_line")
    ]
    assert labels[0].startswith("0xa5")
    assert labels[1].startswith("0x44")
    window.append(CecKeyObservation(0x64, "stop function", InputAction.STOP))
    labels = [
        label.text()
        for label in window.findChildren(QLabel)
        if label.property("cec_debug_line")
    ]
    assert labels[0].startswith("0x64")
    window.handle_input(InputEvent(InputAction.STOP, "cec"))
    assert window.result() == 0
    window.close()
