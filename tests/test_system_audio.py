"""Tests for PulseAudio-compatible system audio operations."""

import json
import subprocess

import pytest

from pideck.application.ports.audio import AudioError
from pideck.infrastructure.system_audio import PulseAudioGateway


def test_inspect_lists_inactive_hdmi1_beside_active_hdmi2(monkeypatch) -> None:
    """NVIDIA HDMI 1 appears as an inactive profile while HDMI 2 is the active sink."""
    responses = {
        ("--format=json", "info"): {
            "default_sink_name": "alsa_output.pci-gp108.hdmi-surround-extra1",
        },
        ("--format=json", "list", "sinks"): [
            {
                "index": 1,
                "name": "alsa_output.pci-gp108.hdmi-surround-extra1",
                "description": "GP108 Digital Surround 5.1 (HDMI 2)",
                "volume": {"front-left": {"value": 32768}, "front-right": {"value": 32768}},
                "properties": {
                    "alsa.card": "1",
                    "device.profile.name": "output:hdmi-surround-extra1",
                },
            },
            {
                "index": 2,
                "name": "alsa_output.pci-xonar.iec958-stereo",
                "description": "CMI8788 Digital Stereo (IEC958)",
                "volume": {"front-left": {"value": 32768}},
                "properties": {
                    "alsa.card": "0",
                    "device.profile.name": "output:iec958-stereo",
                },
            },
        ],
        ("--format=json", "list", "cards"): [
            {
                "index": 1,
                "name": "alsa_card.pci-gp108",
                "active_profile": "output:hdmi-surround-extra1",
                "profiles": {
                    "output:hdmi-stereo": {"description": "Digital Stereo (HDMI)"},
                    "output:hdmi-surround": {"description": "Digital Surround 5.1 (HDMI)"},
                    "output:hdmi-surround71": {"description": "Digital Surround 7.1 (HDMI)"},
                    "output:hdmi-stereo-extra1": {"description": "Digital Stereo (HDMI 2)"},
                    "output:hdmi-surround-extra1": {
                        "description": "Digital Surround 5.1 (HDMI 2)",
                    },
                    "off": {"description": "Off"},
                },
            },
            {
                "index": 0,
                "name": "alsa_card.pci-xonar",
                "active_profile": "output:iec958-stereo",
                "profiles": {
                    "output:iec958-stereo": {"description": "Digital Stereo (IEC958)"},
                    "output:analog-stereo": {"description": "Analog Stereo"},
                },
            },
        ],
    }

    def run(command, **kwargs):
        assert kwargs["timeout"] == 10
        assert kwargs["stdin"] is subprocess.DEVNULL
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(
            command, 0, json.dumps(response) if response is not None else "", "",
        )

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway().inspect()
    assert state.current == "sink:alsa_output.pci-gp108.hdmi-surround-extra1"
    assert state.volume == 50
    assert [output.identifier for output in state.outputs] == [
        "sink:alsa_output.pci-gp108.hdmi-surround-extra1",
        "profile:alsa_card.pci-gp108:output:hdmi-surround",
    ]
    assert state.outputs[1].description == "Digital Surround 5.1 (HDMI)"


def test_inspect_falls_back_to_active_sinks_without_hdmi(monkeypatch) -> None:
    """Machines without HDMI still list active sinks."""
    responses = {
        ("--format=json", "info"): {"default_sink_name": "speakers"},
        ("--format=json", "list", "sinks"): [{
            "name": "speakers",
            "description": "Speakers",
            "volume": {"a": {"value": 32768}},
            "properties": {"alsa.card": "0", "device.profile.name": "output:analog-stereo"},
        }],
        ("--format=json", "list", "cards"): [{
            "index": 0,
            "name": "alsa_card.speakers",
            "active_profile": "output:analog-stereo",
            "profiles": {"output:analog-stereo": {"description": "Analog Stereo"}},
        }],
    }

    def run(command, **kwargs):
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(
            command, 0, json.dumps(response) if response is not None else "", "",
        )

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway().inspect()
    assert [output.identifier for output in state.outputs] == ["sink:speakers"]


def test_set_output_promotes_hdmi_stereo_to_surround_51_same_port(monkeypatch) -> None:
    """Selecting HDMI 2 stereo promotes surround on HDMI 2, not HDMI 1."""
    commands: list[list[str]] = []
    sinks = [{
        "name": "alsa_output.pci-hdmi.hdmi-stereo-extra1",
        "description": "HDMI 2 Stereo",
        "volume": {"a": {"value": 65536}},
        "properties": {
            "alsa.card": "0",
            "device.profile.name": "output:hdmi-stereo-extra1",
        },
    }]
    cards = [{
        "index": 0,
        "name": "alsa_card.pci-hdmi",
        "active_profile": "output:hdmi-stereo-extra1",
        "profiles": {
            "output:hdmi-stereo": {"description": "Digital Stereo (HDMI)"},
            "output:hdmi-surround": {"description": "Digital Surround 5.1 (HDMI)"},
            "output:hdmi-stereo-extra1": {"description": "Digital Stereo (HDMI 2)"},
            "output:hdmi-surround-extra1": {"description": "Digital Surround 5.1 (HDMI 2)"},
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
        if key == ("set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround-extra1"):
            sinks[:] = [{
                "name": "alsa_output.pci-hdmi.hdmi-surround-extra1",
                "description": "HDMI 2 Surround",
                "volume": {"a": {"value": 65536}},
                "properties": {
                    "alsa.card": "0",
                    "device.profile.name": "output:hdmi-surround-extra1",
                },
            }]
            cards[0]["active_profile"] = "output:hdmi-surround-extra1"
            return subprocess.CompletedProcess(command, 0, "", "")
        if key == ("set-default-sink", "alsa_output.pci-hdmi.hdmi-surround-extra1"):
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    PulseAudioGateway().set_output("sink:alsa_output.pci-hdmi.hdmi-stereo-extra1")
    assert [
        "pactl", "set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround-extra1",
    ] in commands
    assert ["pactl", "set-default-sink", "alsa_output.pci-hdmi.hdmi-surround-extra1"] in commands
    assert not any(
        command[1:] == ("set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround")
        for command in commands
    )


def test_selecting_hdmi1_profile_activates_that_port(monkeypatch) -> None:
    """Choosing inactive HDMI 1 switches the card profile then sets the new default sink."""
    commands: list[list[str]] = []
    sinks = [{
        "name": "alsa_output.pci-hdmi.hdmi-surround-extra1",
        "description": "HDMI 2",
        "volume": {"a": {"value": 65536}},
        "properties": {
            "alsa.card": "0",
            "device.profile.name": "output:hdmi-surround-extra1",
        },
    }]
    cards = [{
        "index": 0,
        "name": "alsa_card.pci-hdmi",
        "active_profile": "output:hdmi-surround-extra1",
        "profiles": {
            "output:hdmi-surround": {"description": "Digital Surround 5.1 (HDMI)"},
            "output:hdmi-surround-extra1": {"description": "Digital Surround 5.1 (HDMI 2)"},
        },
    }]

    def run(command, **kwargs):
        commands.append(command)
        key = tuple(command[1:])
        if key == ("set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround"):
            sinks[:] = [{
                "name": "alsa_output.pci-hdmi.hdmi-surround",
                "description": "HDMI 1",
                "volume": {"a": {"value": 65536}},
                "properties": {
                    "alsa.card": "0",
                    "device.profile.name": "output:hdmi-surround",
                },
            }]
            cards[0]["active_profile"] = "output:hdmi-surround"
            return subprocess.CompletedProcess(command, 0, "", "")
        if key == ("--format=json", "list", "sinks"):
            return subprocess.CompletedProcess(command, 0, json.dumps(sinks), "")
        if key == ("--format=json", "list", "cards"):
            return subprocess.CompletedProcess(command, 0, json.dumps(cards), "")
        if key == ("set-default-sink", "alsa_output.pci-hdmi.hdmi-surround"):
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    PulseAudioGateway().set_output("profile:alsa_card.pci-hdmi:output:hdmi-surround")
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
            "output:hdmi-surround-extra1": {"description": "Digital Surround 5.1 (HDMI 2)"},
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
