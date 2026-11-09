"""Tests for persisted system and per-application audio routing."""

from pathlib import Path

import pytest
import yaml

from pideck.application.ports.audio import AudioError
from pideck.infrastructure.audio_preferences import AudioPreferencesStore


def test_missing_file_means_no_overrides(tmp_path: Path) -> None:
    """Absent audio.yaml is treated as an empty preference set."""
    store = AudioPreferencesStore(tmp_path / "audio.yaml")
    saved = store.load()
    assert saved.default is None
    assert saved.applications == {}
    assert store.resolve("kodi") is None


def test_round_trip_default_and_applications(tmp_path: Path) -> None:
    """System default and per-app tokens survive save/load."""
    path = tmp_path / "audio.yaml"
    store = AudioPreferencesStore(path)
    store.save(
        default="sink:hdmi",
        applications={"kodi": "profile:alsa_card.pci-xonar:output:iec958-stereo", "youtube": "sink:hdmi"},
    )
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert document["default"] == "sink:hdmi"
    assert document["applications"]["kodi"].endswith("output:iec958-stereo")
    saved = store.load()
    assert saved.default == "sink:hdmi"
    assert store.resolve("kodi") == "profile:alsa_card.pci-xonar:output:iec958-stereo"
    assert store.resolve("steam-link") == "sink:hdmi"
    store.set_application("kodi", None)
    assert store.resolve("kodi") == "sink:hdmi"
    store.set_default(None)
    assert store.load().default is None
    assert store.resolve("youtube") == "sink:hdmi"


def test_invalid_documents_are_rejected(tmp_path: Path) -> None:
    """Corrupt preference files surface as AudioError."""
    path = tmp_path / "audio.yaml"
    path.write_text("version: 2\n", encoding="utf-8")
    with pytest.raises(AudioError, match="Invalid saved audio settings"):
        AudioPreferencesStore(path).load()
