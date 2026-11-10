"""Tests for persisted system audio output and HDMI mode."""

from pathlib import Path

import pytest
import yaml

from pideck.application.ports.audio import AudioError, HdmiAudioMode
from pideck.infrastructure.audio_preferences import AudioPreferencesStore


def test_missing_file_means_surround_defaults(tmp_path: Path) -> None:
    """Absent audio.yaml uses surround mode and no output override."""
    store = AudioPreferencesStore(tmp_path / "audio.yaml")
    saved = store.load()
    assert saved.default is None
    assert saved.hdmi_mode is HdmiAudioMode.SURROUND


def test_round_trip_default_and_mode(tmp_path: Path) -> None:
    """System default and HDMI mode survive save/load."""
    path = tmp_path / "audio.yaml"
    store = AudioPreferencesStore(path)
    store.save(default="sink:hdmi", hdmi_mode=HdmiAudioMode.BITSTREAM)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document == {
        "version": 1,
        "hdmi_mode": "bitstream",
        "default": "sink:hdmi",
    }
    saved = store.load()
    assert saved.default == "sink:hdmi"
    assert saved.hdmi_mode is HdmiAudioMode.BITSTREAM
    store.set_hdmi_mode(HdmiAudioMode.SURROUND)
    assert store.load().hdmi_mode is HdmiAudioMode.SURROUND
    assert store.load().default == "sink:hdmi"
    store.set_default(None)
    assert store.load().default is None
    assert store.load().hdmi_mode is HdmiAudioMode.SURROUND


def test_invalid_documents_are_rejected(tmp_path: Path) -> None:
    """Corrupt preference files surface as AudioError."""
    path = tmp_path / "audio.yaml"
    path.write_text("version: 2\n", encoding="utf-8")
    with pytest.raises(AudioError, match="Invalid saved audio settings"):
        AudioPreferencesStore(path).load()
    path.write_text("version: 1\nhdmi_mode: mono\n", encoding="utf-8")
    with pytest.raises(AudioError, match="Invalid saved audio settings"):
        AudioPreferencesStore(path).load()
