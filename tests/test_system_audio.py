"""Tests for PulseAudio-compatible system audio operations."""

import json
import subprocess

import pytest

from pideck.application.ports.audio import AudioError
from pideck.infrastructure.system_audio import PulseAudioGateway


def test_audio_outputs_and_volume_are_read_and_changed(monkeypatch) -> None:
    """Inspect and adjust devices using bounded, non-shell pactl commands."""
    commands: list[list[str]] = []
    responses = {
        ("--format=json", "info"): {"default_sink_name": "sink.one"},
        ("--format=json", "list", "sinks"): [{
            "index": 4, "name": "sink.one", "description": "HDMI",
            "volume": {"front-left": {"value": 32768}, "front-right": {"value": 32768}},
        }],
    }

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == 10
        assert kwargs["stdin"] is subprocess.DEVNULL
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(command, 0, json.dumps(response) if response else "", "")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    gateway = PulseAudioGateway()
    state = gateway.inspect()
    assert state.current == 4
    assert state.volume == 50
    assert state.outputs[0].description == "HDMI"
    gateway.set_output(4)
    gateway.set_volume(70)
    assert commands[-2:] == [
        ["pactl", "set-default-sink", "4"],
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "70%"],
    ]


@pytest.mark.parametrize("volume", [-1, 101, True])
def test_audio_rejects_invalid_volume_without_running_commands(monkeypatch, volume) -> None:
    """Never pass invalid volume input to the host audio service."""
    def unexpected(*args, **kwargs):
        raise AssertionError("command must not be executed")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", unexpected)
    with pytest.raises(AudioError, match="Invalid audio volume"):
        PulseAudioGateway().set_volume(volume)


def test_audio_missing_or_invalid_service_is_reported(monkeypatch) -> None:
    """Unavailable backends and malformed output are explicit errors."""
    def missing(*args, **kwargs):
        raise FileNotFoundError("pactl")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", missing)
    with pytest.raises(AudioError, match="unavailable"):
        PulseAudioGateway().inspect()
    monkeypatch.setattr(
        "pideck.infrastructure.system_audio.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "not json", ""),
    )
    with pytest.raises(AudioError, match="invalid data"):
        PulseAudioGateway().inspect()
