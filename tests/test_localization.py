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

def test_every_language_defines_the_same_keys() -> None:
    """A key missing in one language would otherwise show an English or raw string."""
    from pideck.presentation.qt.localization import TRANSLATIONS

    assert set(TRANSLATIONS["de"]) == set(TRANSLATIONS["en"])


def test_missing_key_falls_back_to_english_then_key(monkeypatch) -> None:
    """A key absent from the selected language uses English before the raw key."""
    from pideck.presentation.qt import localization

    monkeypatch.setitem(localization.TRANSLATIONS, "de", {})
    assert tr("de", "settings") == "Settings"
    assert tr("de", "no_such_key") == "no_such_key"


def test_build_timestamp_is_formatted_for_people() -> None:
    """ISO timestamps are shown as readable local dates; bad values stay unchanged."""
    from pideck.presentation.qt.localization import format_timestamp

    english = format_timestamp("en", "2026-11-05T10:59:12")
    german = format_timestamp("de", "2026-11-05T10:59:12")

    assert english == "5 November 2026, 10:59"
    assert german == "05. November 2026, 10:59"
    assert format_timestamp("en", "not a date") == "not a date"
