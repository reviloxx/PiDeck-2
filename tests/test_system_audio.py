"""Tests for PulseAudio-compatible system audio operations."""

import json
import subprocess

import pytest

from pideck.application.ports.audio import AudioError
from pideck.infrastructure.system_audio import PulseAudioGateway


def test_inspect_lists_active_sinks_only(monkeypatch) -> None:
    """Inactive card profiles are not listed in Settings."""
    responses = {
        ("--format=json", "info"): {"default_sink_name": "alsa_output.pci-hdmi.hdmi-stereo"},
        ("--format=json", "list", "sinks"): [{
            "index": 1,
            "name": "alsa_output.pci-hdmi.hdmi-stereo",
            "description": "HDMI / DisplayPort Stereo",
            "volume": {"front-left": {"value": 32768}, "front-right": {"value": 32768}},
            "properties": {"alsa.card": "0", "device.profile.name": "output:hdmi-stereo"},
        }],
    }

    def run(command, **kwargs):
        assert kwargs["timeout"] == 10
        assert kwargs["stdin"] is subprocess.DEVNULL
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(command, 0, json.dumps(response) if response is not None else "", "")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway().inspect()
    assert state.current == "sink:alsa_output.pci-hdmi.hdmi-stereo"
    assert state.volume == 50
    assert [output.identifier for output in state.outputs] == [
        "sink:alsa_output.pci-hdmi.hdmi-stereo",
    ]


def test_set_output_promotes_hdmi_stereo_to_surround_51(monkeypatch) -> None:
    """Selecting an HDMI sink switches the card to Digital Surround 5.1 when available."""
    commands: list[list[str]] = []
    sinks = [{
        "name": "alsa_output.pci-hdmi.hdmi-stereo",
        "description": "HDMI Stereo",
        "volume": {"a": {"value": 65536}},
        "properties": {"alsa.card": "0", "device.profile.name": "output:hdmi-stereo"},
    }]
    cards = [{
        "index": 0,
        "name": "alsa_card.pci-hdmi",
        "active_profile": "output:hdmi-stereo",
        "profiles": {
            "output:hdmi-stereo": {"description": "Digital Stereo (HDMI)"},
            "output:hdmi-surround": {"description": "Digital Surround 5.1 (HDMI)"},
            "output:hdmi-surround71": {"description": "Digital Surround 7.1 (HDMI)"},
            "off": {"description": "Off"},
        },
    }]

    def run(command, **kwargs):
        commands.append(command)
        key = tuple(command[1:])
        if key == ("--format=json", "list", "sinks"):
            return subprocess.CompletedProcess(command, 0, json.dumps(sinks), "")
        if key == ("--format=json", "list", "cards"):
            return subprocess.CompletedProcess(command, 0, json.dumps(cards), "")
        if key == ("set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround"):
            sinks[:] = [{
                "name": "alsa_output.pci-hdmi.hdmi-surround",
                "description": "HDMI Surround",
                "volume": {"a": {"value": 65536}},
                "properties": {
                    "alsa.card": "0",
                    "device.profile.name": "output:hdmi-surround",
                },
            }]
            cards[0]["active_profile"] = "output:hdmi-surround"
            return subprocess.CompletedProcess(command, 0, "", "")
        if key == ("set-default-sink", "alsa_output.pci-hdmi.hdmi-surround"):
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    PulseAudioGateway().set_output("sink:alsa_output.pci-hdmi.hdmi-stereo")
    assert ["pactl", "set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround"] in commands
    assert ["pactl", "set-default-sink", "alsa_output.pci-hdmi.hdmi-surround"] in commands


def test_ensure_hdmi_surround_upgrades_default_sink(monkeypatch) -> None:
    """Kodi launch promotes the current default HDMI stereo sink to surround 5.1."""
    commands: list[list[str]] = []
    sinks = [{
        "name": "alsa_output.pci-hdmi.hdmi-stereo",
        "description": "HDMI Stereo",
        "volume": {"a": {"value": 65536}},
        "properties": {"alsa.card": "0", "device.profile.name": "output:hdmi-stereo"},
    }]
    cards = [{
        "index": 0,
        "name": "alsa_card.pci-hdmi",
        "active_profile": "output:hdmi-stereo",
        "profiles": {
            "output:hdmi-stereo": {"description": "Digital Stereo (HDMI)"},
            "output:hdmi-surround": {"description": "Digital Surround 5.1 (HDMI)"},
        },
    }]

    def run(command, **kwargs):
        commands.append(command)
        key = tuple(command[1:])
        if key == ("--format=json", "info"):
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"default_sink_name": "alsa_output.pci-hdmi.hdmi-stereo"}), "",
            )
        if key == ("--format=json", "list", "sinks"):
            return subprocess.CompletedProcess(command, 0, json.dumps(sinks), "")
        if key == ("--format=json", "list", "cards"):
            return subprocess.CompletedProcess(command, 0, json.dumps(cards), "")
        if key == ("set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround"):
            sinks[:] = [{
                "name": "alsa_output.pci-hdmi.hdmi-surround",
                "description": "HDMI Surround",
                "volume": {"a": {"value": 65536}},
                "properties": {
                    "alsa.card": "0",
                    "device.profile.name": "output:hdmi-surround",
                },
            }]
            return subprocess.CompletedProcess(command, 0, "", "")
        if key == ("set-default-sink", "alsa_output.pci-hdmi.hdmi-surround"):
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    PulseAudioGateway().ensure_hdmi_surround()
    assert ["pactl", "set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround"] in commands
    assert ["pactl", "set-default-sink", "alsa_output.pci-hdmi.hdmi-surround"] in commands


def test_set_output_by_sink_name_without_hdmi_profile(monkeypatch) -> None:
    """Non-HDMI sinks are selected by name without profile switching."""
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        key = tuple(command[1:])
        if key == ("--format=json", "list", "sinks"):
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps([{
                    "name": "speakers",
                    "description": "Speakers",
                    "volume": {"a": {"value": 1}},
                    "properties": {"alsa.card": "1", "device.profile.name": "output:analog-stereo"},
                }]),
                "",
            )
        if key == ("--format=json", "list", "cards"):
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps([{
                    "index": 1,
                    "name": "alsa_card.speakers",
                    "active_profile": "output:analog-stereo",
                    "profiles": {"output:analog-stereo": {"description": "Analog Stereo"}},
                }]),
                "",
            )
        if key == ("set-default-sink", "speakers"):
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    PulseAudioGateway().set_output("sink:speakers")
    assert not any(command[1:2] == ["set-card-profile"] for command in commands)
    assert commands[-1] == ["pactl", "set-default-sink", "speakers"]


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
