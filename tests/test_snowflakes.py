"""Tests for seasonal snowflake mode resolution and overlay behavior."""

from dataclasses import replace
from datetime import date
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pideck.application.launcher import LauncherController
from pideck.domain.snowflakes import (
    is_snowflakes_season,
    snowflakes_active,
)
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.launcher_window import LauncherWindow
from pideck.presentation.qt.snowflakes import SnowflakeOverlay
from tests.test_configuration import configuration_document


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2025, 11, 30), False),
        (date(2025, 12, 1), True),
        (date(2025, 12, 25), True),
        (date(2026, 1, 1), True),
        (date(2026, 1, 6), True),
        (date(2026, 1, 7), False),
        (date(2026, 6, 15), False),
    ],
)
def test_is_snowflakes_season_boundaries(day: date, expected: bool) -> None:
    """Holiday snow covers 1 December through 6 January inclusive."""
    assert is_snowflakes_season(day) is expected


@pytest.mark.parametrize(
    ("mode", "day", "expected"),
    [
        ("off", date(2025, 12, 25), False),
        ("on", date(2025, 6, 1), True),
        ("automatic", date(2025, 12, 1), True),
        ("automatic", date(2025, 11, 30), False),
        ("unknown", date(2025, 12, 25), False),
    ],
)
def test_snowflakes_active_resolves_mode(mode: str, day: date, expected: bool) -> None:
    """Off/on ignore the calendar; automatic follows the holiday window."""
    assert snowflakes_active(mode, day) is expected


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for snowflake widget tests."""
    return QApplication.instance() or QApplication([])


def test_snowflake_overlay_is_mouse_transparent(application: QApplication) -> None:
    """The overlay must never steal clicks or focus from launcher tiles."""
    overlay = SnowflakeOverlay()
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert overlay.focusPolicy() == Qt.FocusPolicy.NoFocus
    overlay.close()


def test_snowflake_overlay_animates_only_when_enabled_and_on(
    application: QApplication,
) -> None:
    """Animation starts for mode on once the launcher reports visible."""
    overlay = SnowflakeOverlay()
    overlay.resize(800, 600)
    overlay.set_mode("on")
    assert not overlay.active
    overlay.set_launcher_visible(True)
    assert overlay.active
    assert overlay.isVisible()
    overlay.set_mode("off")
    assert not overlay.active
    assert not overlay.isVisible()
    overlay.close()


def test_launcher_snowflake_overlay_follows_mode(application: QApplication) -> None:
    """Launcher wires the overlay and pauses it when hidden."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = LauncherWindow(
        LauncherController(configuration),
        DEFAULT_THEME,
        Path.cwd(),
        snowflakes="on",
    )
    window.resize(800, 600)
    window.show()
    application.processEvents()

    overlay = window._snowflake_overlay
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert overlay.active
    assert overlay.geometry() == window._root.rect()

    window.hide()
    application.processEvents()
    assert not overlay.active

    updated = replace(
        configuration,
        home=replace(configuration.home, snowflakes="off"),
    )
    window.apply_configuration(updated, DEFAULT_THEME)
    window.show()
    application.processEvents()
    assert not overlay.active
    window.close()
