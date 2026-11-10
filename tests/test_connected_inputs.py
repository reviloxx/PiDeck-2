"""Tests for the connected-input footer strip."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.connected_inputs import ConnectedInputs, ConnectedInputsStrip


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for the module."""
    return QApplication.instance() or QApplication([])


def test_connected_inputs_strip_shows_only_live_sources(application: QApplication) -> None:
    """Icons appear only while their source is connected."""
    del application
    strip = ConnectedInputsStrip(DEFAULT_THEME, "en")
    assert strip.isVisible() is False

    strip.set_inputs(ConnectedInputs(cec=True))
    assert strip.isVisible() is True
    assert strip._cec.isVisible() is True
    assert strip._gamepad.isVisible() is False
    assert strip._keyboard.isVisible() is False

    strip.set_inputs(ConnectedInputs(cec=True, gamepad=True, keyboard=True))
    assert strip._cec.isVisible() is True
    assert strip._gamepad.isVisible() is True
    assert strip._keyboard.isVisible() is True

    strip.set_inputs(ConnectedInputs())
    assert strip.isVisible() is False
