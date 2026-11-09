"""Tests for PulseAudio-compatible system audio operations."""

import json
from pathlib import Path
import subprocess

import pytest

from pideck.application.ports.audio import AudioError
from pideck.infrastructure.system_audio import PulseAudioGateway


def _empty_proc(tmp_path: Path) -> Path:
    (tmp_path / "asound").mkdir(exist_ok=True)
    (tmp_path / "asound/cards").write_text("", encoding="utf-8")
    return tmp_path


def _xonar_digital_proc(tmp_path: Path) -> Path:
    asound = tmp_path / "asound"
    asound.mkdir()
    (asound / "cards").write_text(
        " 0 [DX             ]: AV200 - Xonar DX\n"
        "                      Asus Virtuoso 100 at 0xe000, irq 16\n",
        encoding="utf-8",
    )
    card = asound / "card0"
    (card / "pcm0p").mkdir(parents=True)
    (card / "pcm0p/info").write_text(
        "card: 0\ndevice: 0\nstream: PLAYBACK\nid: Multichannel\nname: Multichannel\n",
        encoding="utf-8",
    )
    (card / "pcm1p").mkdir(parents=True)
    (card / "pcm1p/info").write_text(
        "card: 0\ndevice: 1\nstream: PLAYBACK\nid: Digital\nname: Digital\n",
        encoding="utf-8",
    )
    return tmp_path


def test_audio_outputs_include_inactive_digital_profiles(monkeypatch, tmp_path: Path) -> None:
    """S/PDIF and other inactive output profiles appear beside active sinks."""
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
        assert kwargs["timeout"] == 10
        assert kwargs["stdin"] is subprocess.DEVNULL
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(command, 0, json.dumps(response) if response is not None else "", "")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway(proc=_empty_proc(tmp_path)).inspect()
    assert state.current == "sink:alsa_output.pci-xonar.analog-stereo"
    assert state.volume == 50
    assert [output.description for output in state.outputs] == [
        "Xonar DX Analog Stereo",
        "Digital Stereo (IEC958) Output",
    ]
    assert state.outputs[1].identifier == "profile:alsa_card.pci-xonar:output:iec958-stereo"


def test_alsa_digital_pcm_appears_when_pulse_lacks_iec958(monkeypatch, tmp_path: Path) -> None:
    """Xonar Digital PCM is offered when PulseAudio only exposes analog."""
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
                "off": {"description": "Off"},
            },
        }],
    }

    def run(command, **kwargs):
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(command, 0, json.dumps(response) if response is not None else "", "")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway(proc=_xonar_digital_proc(tmp_path)).inspect()
    assert [output.identifier for output in state.outputs] == [
        "sink:alsa_output.pci-xonar.analog-stereo",
        "alsa:hw:0,1",
    ]
    assert state.outputs[1].description == "Xonar DX Digital Stereo (S/PDIF)"


def test_alsa_digital_is_not_duplicated_when_iec958_profile_exists(
    monkeypatch, tmp_path: Path,
) -> None:
    """Do not add an ALSA fallback when Pulse already exposes IEC958."""
    responses = {
        ("--format=json", "info"): {"default_sink_name": "alsa_output.pci-xonar.analog-stereo"},
        ("--format=json", "list", "sinks"): [{
            "index": 4,
            "name": "alsa_output.pci-xonar.analog-stereo",
            "description": "Xonar DX Analog Stereo",
            "volume": {"front-left": {"value": 32768}},
            "properties": {"alsa.card": "0"},
        }],
        ("--format=json", "list", "cards"): [{
            "index": 0,
            "name": "alsa_card.pci-xonar",
            "active_profile": "output:analog-stereo",
            "profiles": {
                "output:analog-stereo": {"description": "Analog Stereo"},
                "output:iec958-stereo": {"description": "Digital Stereo (IEC958) Output"},
            },
        }],
    }

    def run(command, **kwargs):
        response = responses.get(tuple(command[1:]))
        return subprocess.CompletedProcess(command, 0, json.dumps(response) if response is not None else "", "")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    state = PulseAudioGateway(proc=_xonar_digital_proc(tmp_path)).inspect()
    assert [output.identifier for output in state.outputs] == [
        "sink:alsa_output.pci-xonar.analog-stereo",
        "profile:alsa_card.pci-xonar:output:iec958-stereo",
    ]


def test_selecting_alsa_digital_loads_module_and_sets_default(monkeypatch, tmp_path: Path) -> None:
    """Choosing the ALSA Digital PCM loads module-alsa-sink once and uses that sink."""
    commands: list[list[str]] = []
    sinks: list[dict] = []

    def run(command, **kwargs):
        commands.append(command)
        key = tuple(command[1:])
        if key[:2] == ("--format=json", "list") and key[2:] == ("sinks",):
            return subprocess.CompletedProcess(command, 0, json.dumps(sinks), "")
        if key[0] == "load-module":
            sinks.append({
                "name": "pideck.spdif.0",
                "description": "Xonar DX Digital Stereo (S/PDIF)",
                "volume": {"front-left": {"value": 65536}},
            })
            return subprocess.CompletedProcess(command, 0, "42\n", "")
        if key[0] == "set-default-sink":
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "amixer":
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", run)
    gateway = PulseAudioGateway(proc=_xonar_digital_proc(tmp_path))
    assert gateway.ensure_output("alsa:hw:0,1") == "pideck.spdif.0"
    gateway.set_output("alsa:hw:0,1")
    assert any(command[:3] == ["pactl", "load-module", "module-alsa-sink"] for command in commands)
    assert ["pactl", "set-default-sink", "pideck.spdif.0"] in commands
    assert ["amixer", "-c", "0", "set", "IEC958", "unmute"] in commands
    # Second ensure reuses the loaded sink without loading again.
    before = len(commands)
    assert gateway.ensure_output("alsa:hw:0,1") == "pideck.spdif.0"
    assert not any(command[:3] == ["pactl", "load-module", "module-alsa-sink"] for command in commands[before:])


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


def test_audio_missing_or_invalid_service_is_reported(monkeypatch, tmp_path: Path) -> None:
    """Unavailable backends and malformed output are explicit errors."""
    def missing(*args, **kwargs):
        raise FileNotFoundError("pactl")

    monkeypatch.setattr("pideck.infrastructure.system_audio.subprocess.run", missing)
    with pytest.raises(AudioError, match="unavailable"):
        PulseAudioGateway(proc=_empty_proc(tmp_path)).inspect()
    monkeypatch.setattr(
        "pideck.infrastructure.system_audio.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "not json", ""),
    )
    with pytest.raises(AudioError, match="invalid data"):
        PulseAudioGateway(proc=_empty_proc(tmp_path)).inspect()
