"""Tests for PulseAudio-compatible system audio operations."""

import json
import subprocess

import pytest

from pideck.application.ports.audio import AudioError, HdmiAudioMode
from pideck.infrastructure.system_audio import PulseAudioGateway


def test_inspect_surround_lists_inactive_hdmi1_51(monkeypatch) -> None:
    """Surround mode lists Digital Surround 5.1 for inactive HDMI 1."""
    responses = {
        ("--format=json", "info"): {
            "default_sink_name": "alsa_output.pci-gp108.hdmi-surround-extra1",
        },
        ("--format=json", "list", "sinks"): [{
            "name": "alsa_output.pci-gp108.hdmi-surround-extra1",
            "description": "GP108 Digital Surround 5.1 (HDMI 2)",
            "volume": {"front-left": {"value": 32768}, "front-right": {"value": 32768}},
            "properties": {
                "alsa.card": "1",
                "device.profile.name": "output:hdmi-surround-extra1",
            },
        }],
        ("--format=json", "list", "cards"): [{
            "index": 1,
            "name": "alsa_card.pci-gp108",
            "active_profile": "output:hdmi-surround-extra1",
            "profiles": {
                "output:hdmi-stereo": {"description": "Digital Stereo (HDMI)"},
                "output:hdmi-surround": {"description": "Digital Surround 5.1 (HDMI)"},
                "output:hdmi-stereo-extra1": {"description": "Digital Stereo (HDMI 2)"},
                "output:hdmi-surround-extra1": {"description": "Digital Surround 5.1 (HDMI 2)"},
            },
        }],
    }

    def run(command, **kwargs):
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(
            command, 0, json.dumps(response) if response is not None else "", "",
        )

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway().inspect(hdmi_mode=HdmiAudioMode.SURROUND)
    assert [output.identifier for output in state.outputs] == [
        "sink:alsa_output.pci-gp108.hdmi-surround-extra1",
        "profile:alsa_card.pci-gp108:output:hdmi-surround",
    ]


def test_inspect_bitstream_lists_inactive_hdmi1_stereo(monkeypatch) -> None:
    """Bitstream mode lists Digital Stereo for inactive HDMI 1."""
    responses = {
        ("--format=json", "info"): {
            "default_sink_name": "alsa_output.pci-gp108.hdmi-stereo-extra1",
        },
        ("--format=json", "list", "sinks"): [{
            "name": "alsa_output.pci-gp108.hdmi-stereo-extra1",
            "description": "GP108 Digital Stereo (HDMI 2)",
            "volume": {"a": {"value": 32768}},
            "properties": {
                "alsa.card": "1",
                "device.profile.name": "output:hdmi-stereo-extra1",
            },
        }],
        ("--format=json", "list", "cards"): [{
            "index": 1,
            "name": "alsa_card.pci-gp108",
            "active_profile": "output:hdmi-stereo-extra1",
            "profiles": {
                "output:hdmi-stereo": {"description": "Digital Stereo (HDMI)"},
                "output:hdmi-surround": {"description": "Digital Surround 5.1 (HDMI)"},
                "output:hdmi-stereo-extra1": {"description": "Digital Stereo (HDMI 2)"},
            },
        }],
    }

    def run(command, **kwargs):
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(
            command, 0, json.dumps(response) if response is not None else "", "",
        )

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway().inspect(hdmi_mode=HdmiAudioMode.BITSTREAM)
    assert state.outputs[1].identifier == "profile:alsa_card.pci-gp108:output:hdmi-stereo"


def test_surround_mode_promotes_stereo_to_51_same_port(monkeypatch) -> None:
    """Surround mode upgrades HDMI 2 stereo to surround 5.1 on the same port."""
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
            return subprocess.CompletedProcess(command, 0, "", "")
        if key == ("set-default-sink", "alsa_output.pci-hdmi.hdmi-surround-extra1"):
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    PulseAudioGateway().set_output(
        "sink:alsa_output.pci-hdmi.hdmi-stereo-extra1",
        hdmi_mode=HdmiAudioMode.SURROUND,
    )
    assert [
        "pactl", "set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-surround-extra1",
    ] in commands
    assert not any(command[1] == "set-sink-formats" for command in commands)


def test_bitstream_mode_promotes_and_sets_formats(monkeypatch) -> None:
    """Bitstream mode promotes surround to stereo and enables sink formats."""
    commands: list[list[str]] = []
    sinks = [{
        "name": "alsa_output.pci-hdmi.hdmi-surround",
        "description": "HDMI Surround",
        "volume": {"a": {"value": 65536}},
        "properties": {"alsa.card": "0", "device.profile.name": "output:hdmi-surround"},
    }]
    cards = [{
        "index": 0,
        "name": "alsa_card.pci-hdmi",
        "active_profile": "output:hdmi-surround",
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
                command, 0, json.dumps({"default_sink_name": "alsa_output.pci-hdmi.hdmi-surround"}), "",
            )
        if key == ("--format=json", "list", "sinks"):
            return subprocess.CompletedProcess(command, 0, json.dumps(sinks), "")
        if key == ("--format=json", "list", "cards"):
            return subprocess.CompletedProcess(command, 0, json.dumps(cards), "")
        if key == ("set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-stereo"):
            sinks[:] = [{
                "name": "alsa_output.pci-hdmi.hdmi-stereo",
                "description": "HDMI Stereo",
                "volume": {"a": {"value": 65536}},
                "properties": {
                    "alsa.card": "0",
                    "device.profile.name": "output:hdmi-stereo",
                },
            }]
            return subprocess.CompletedProcess(command, 0, "", "")
        if key == ("set-default-sink", "alsa_output.pci-hdmi.hdmi-stereo"):
            return subprocess.CompletedProcess(command, 0, "", "")
        if key[0] == "set-sink-formats":
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    PulseAudioGateway().ensure_hdmi(hdmi_mode=HdmiAudioMode.BITSTREAM)
    assert ["pactl", "set-card-profile", "alsa_card.pci-hdmi", "output:hdmi-stereo"] in commands
    assert any(command[1] == "set-sink-formats" for command in commands)


@pytest.mark.parametrize("volume", [-1, 101, True])
def test_audio_rejects_invalid_volume_without_running_commands(monkeypatch, volume) -> None:
    """Never pass invalid volume input to the host audio service."""
    def unexpected(*args, **kwargs):
        raise AssertionError("command must not be executed")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", unexpected)
    with pytest.raises(AudioError, match="Invalid audio volume"):
        PulseAudioGateway().set_volume(volume)
