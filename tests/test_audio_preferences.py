"""Tests for persisted system audio output selection."""

from pathlib import Path

import pytest
import yaml

from pideck.application.ports.audio import AudioError
from pideck.infrastructure.audio_preferences import AudioPreferencesStore


def test_missing_file_means_no_override(tmp_path: Path) -> None:
    """Absent audio.yaml is treated as an empty preference set."""
    store = AudioPreferencesStore(tmp_path / "audio.yaml")
    assert store.load().default is None


def test_round_trip_default(tmp_path: Path) -> None:
    """System default token survives save/load."""
    path = tmp_path / "audio.yaml"
    store = AudioPreferencesStore(path)
    store.save(default="sink:hdmi")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document == {"version": 1, "default": "sink:hdmi"}
    assert store.load().default == "sink:hdmi"
    store.set_default(None)
    assert store.load().default is None
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"version": 1}


def test_invalid_documents_are_rejected(tmp_path: Path) -> None:
    """Corrupt preference files surface as AudioError."""
    path = tmp_path / "audio.yaml"
    path.write_text("version: 2\n", encoding="utf-8")
    with pytest.raises(AudioError, match="Invalid saved audio settings"):
        AudioPreferencesStore(path).load()
