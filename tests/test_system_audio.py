"""Tests for PulseAudio-compatible system audio operations."""

import json
import subprocess

import pytest

from pideck.application.ports.audio import AudioError
from pideck.infrastructure.system_audio import PulseAudioGateway


def test_audio_outputs_include_inactive_digital_profiles(monkeypatch) -> None:
    """S/PDIF and other inactive output profiles appear beside active sinks."""
    commands: list[list[str]] = []
    responses = {
        ("--format=json", "info"): {"default_sink_name": "alsa_output.pci-xonar.analog-stereo"},
        ("--format=json", "list", "sinks"): [{
            "index": 4,
            "name": "alsa_output.pci-xonar.analog-stereo",
            "description": "Xonar DX Analog Stereo",
            "volume": {"front-left": {"value": 32768}, "front-right": {"value": 32768}},
            "properties": {"alsa.card": "0", "device.profile.name": "output:analog-stereo"},
        }],
        ("--format=json", "list", "cards"): [{
            "index": 0,
            "name": "alsa_card.pci-xonar",
            "active_profile": "output:analog-stereo",
            "profiles": {
                "output:analog-stereo": {"description": "Analog Stereo"},
                "output:iec958-stereo": {"description": "Digital Stereo (IEC958) Output"},
                "off": {"description": "Off"},
                "input:analog-stereo": {"description": "Analog Stereo Input"},
            },
        }],
    }

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == 10
        assert kwargs["stdin"] is subprocess.DEVNULL
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(command, 0, json.dumps(response) if response is not None else "", "")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway().inspect()
    assert state.current == "sink:alsa_output.pci-xonar.analog-stereo"
    assert state.volume == 50
    assert [output.description for output in state.outputs] == [
        "Xonar DX Analog Stereo",
        "Digital Stereo (IEC958) Output",
    ]
    assert state.outputs[1].identifier == "profile:alsa_card.pci-xonar:output:iec958-stereo"


def test_selecting_digital_profile_activates_sink_and_sets_default(monkeypatch) -> None:
    """Choosing IEC958 switches the card profile then sets the new default sink."""
    commands: list[list[str]] = []
    sinks_after_profile = [{
        "index": 5,
        "name": "alsa_output.pci-xonar.iec958-stereo",
        "description": "Xonar DX Digital Stereo (IEC958)",
        "volume": {"front-left": {"value": 65536}},
        "properties": {
            "alsa.card": "0",
            "device.profile.name": "output:iec958-stereo",
        },
    }]
    cards = [{
        "index": 0,
        "name": "alsa_card.pci-xonar",
        "active_profile": "output:analog-stereo",
        "profiles": {
            "output:analog-stereo": {"description": "Analog Stereo"},
            "output:iec958-stereo": {"description": "Digital Stereo (IEC958) Output"},
        },
    }]
    list_sinks_calls = {"count": 0}

    def run(command, **kwargs):
        commands.append(command)
        key = tuple(command[1:])
        if key == ("set-card-profile", "alsa_card.pci-xonar", "output:iec958-stereo"):
            return subprocess.CompletedProcess(command, 0, "", "")
        if key == ("set-default-sink", "alsa_output.pci-xonar.iec958-stereo"):
            return subprocess.CompletedProcess(command, 0, "", "")
        if key == ("--format=json", "list", "cards"):
            return subprocess.CompletedProcess(command, 0, json.dumps(cards), "")
        if key == ("--format=json", "list", "sinks"):
            list_sinks_calls["count"] += 1
            return subprocess.CompletedProcess(command, 0, json.dumps(sinks_after_profile), "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    gateway = PulseAudioGateway()
    assert gateway.ensure_output("profile:alsa_card.pci-xonar:output:iec958-stereo") == (
        "alsa_output.pci-xonar.iec958-stereo"
    )
    gateway.set_output("profile:alsa_card.pci-xonar:output:iec958-stereo")
    assert ["pactl", "set-card-profile", "alsa_card.pci-xonar", "output:iec958-stereo"] in commands
    assert ["pactl", "set-default-sink", "alsa_output.pci-xonar.iec958-stereo"] in commands


def test_set_output_by_sink_name(monkeypatch) -> None:
    """Existing sinks are selected by stable name without profile switching."""
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        if tuple(command[1:]) == ("--format=json", "list", "sinks"):
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps([{"name": "hdmi", "description": "HDMI", "volume": {"a": {"value": 1}}}]),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    PulseAudioGateway().set_output("sink:hdmi")
    assert commands[-1] == ["pactl", "set-default-sink", "hdmi"]


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
