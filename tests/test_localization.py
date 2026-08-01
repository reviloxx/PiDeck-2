"""Tests for language-specific presentation formatting."""

from datetime import datetime

from pideck.presentation.qt.localization import clock_text, tr


def test_german_clock_uses_german_date_form() -> None:
    """German dates include the German punctuation and localized names."""
    value = datetime(2026, 8, 1, 14, 30)

    assert "." in clock_text("de", value)
    assert clock_text("de", value).endswith("14:30")
    assert clock_text("en", value).endswith("14:30")


def test_translation_catalog_falls_back_to_english() -> None:
    """Unknown language codes remain safe for presentation rendering."""
    assert tr("fr", "settings") == "Settings"
    assert tr("de", "settings") == "Einstellungen"