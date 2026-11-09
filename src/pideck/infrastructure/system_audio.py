"""PulseAudio-compatible audio operations for Linux desktop sessions."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from pideck.application.ports.audio import AudioError, AudioOutput, AudioState

_SINK_PREFIX = "sink:"
_PROFILE_PREFIX = "profile:"
_ALSA_PREFIX = "alsa:"
_SPDIF_SINK_PREFIX = "pideck.spdif."
_CARD = re.compile(r"^\s*(\d+)\s+\[[^\]]*\]:\s*.*?\s-\s(.+?)\s*$")
_PCM_PLAYBACK = re.compile(r"^pcm(\d+)p$")


class PulseAudioGateway:
    """Operate a PulseAudio or PipeWire PulseAudio-compatible server."""

    def __init__(self, *, proc: Path | None = None) -> None:
        """Create a gateway; ``proc`` overrides ``/proc`` for ALSA discovery tests."""
        self._proc = proc or Path("/proc")

    def _run(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                ["pactl", *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AudioError("The audio service is unavailable or timed out") from error
        if result.returncode:
            raise AudioError(result.stderr.strip() or "The audio service rejected the request")
        return result.stdout

    def _load_json(self, *arguments: str) -> Any:
        try:
            return json.loads(self._run("--format=json", *arguments))
        except json.JSONDecodeError as error:
            raise AudioError("The audio service returned invalid data") from error

    def inspect(self) -> AudioState:
        """Read sinks, inactive output profiles, ALSA Digital PCMs, and volume."""
        info = self._load_json("info")
        sinks = self._load_json("list", "sinks")
        cards = self._load_json("list", "cards")
        if not isinstance(info, dict) or not isinstance(sinks, list) or not isinstance(cards, list):
            raise AudioError("The audio service returned invalid data")
        default = info.get("default_sink_name")
        outputs: list[AudioOutput] = []
        current: str | None = None
        current_volume: int | None = None
        for sink in sinks:
            output, volume = self._parse_sink(sink)
            outputs.append(output)
            if output.name == default:
                current = output.identifier
                current_volume = volume
        for card in cards:
            outputs.extend(self._inactive_output_profiles(card))
        outputs.extend(self._alsa_digital_outputs(sinks, cards, outputs))
        if current is None or current_volume is None:
            raise AudioError("No active audio output is available")
        return AudioState(tuple(outputs), current, current_volume)

    def set_output(self, identifier: str) -> None:
        """Change the default output, activating a card profile when needed."""
        sink_name = self.ensure_output(identifier)
        self._run("set-default-sink", sink_name)

    def ensure_output(self, identifier: str) -> str:
        """Return a usable sink name for ``identifier``, activating profiles as needed."""
        if not isinstance(identifier, str) or not identifier:
            raise AudioError("Invalid audio output")
        if identifier.startswith(_SINK_PREFIX):
            sink_name = identifier[len(_SINK_PREFIX) :]
            if not sink_name:
                raise AudioError("Invalid audio output")
            if self._sink_exists(sink_name):
                return sink_name
            self._activate_profile_for_sink(sink_name)
            if self._sink_exists(sink_name):
                return sink_name
            raise AudioError("The selected audio output is unavailable")
        if identifier.startswith(_PROFILE_PREFIX):
            card_name, profile_name = self._parse_profile_identifier(identifier)
            self._run("set-card-profile", card_name, profile_name)
            sink_name = self._find_sink_for_profile(card_name, profile_name)
            if sink_name is None:
                raise AudioError("The selected audio output is unavailable")
            return sink_name
        if identifier.startswith(_ALSA_PREFIX):
            return self._ensure_alsa_digital(identifier)
        raise AudioError("Invalid audio output")

    def set_volume(self, volume: int) -> None:
        """Set the default output volume without raising it above 100 percent."""
        if type(volume) is not int or not 0 <= volume <= 100:
            raise AudioError("Invalid audio volume")
        self._run("set-sink-volume", "@DEFAULT_SINK@", f"{volume}%")

    def _parse_sink(self, sink: object) -> tuple[AudioOutput, int | None]:
        if not isinstance(sink, dict):
            raise AudioError("The audio service returned invalid output data")
        name = sink.get("name")
        description = sink.get("description")
        if not isinstance(name, str) or not name:
            raise AudioError("The audio service returned invalid output data")
        if not isinstance(description, str) or not description:
            description = name
        volume = self._average_volume(sink.get("volume"))
        return AudioOutput(f"{_SINK_PREFIX}{name}", name, description), volume

    def _average_volume(self, volume: object) -> int | None:
        if not isinstance(volume, dict) or not volume:
            return None
        channels: list[int] = []
        for item in volume.values():
            if not isinstance(item, dict):
                raise AudioError("The audio service returned invalid volume data")
            value = item.get("value")
            if type(value) is not int or value < 0:
                raise AudioError("The audio service returned invalid volume data")
            channels.append(value)
        if not channels:
            return None
        return min(100, round(sum(channels) / len(channels) * 100 / 65536))

    def _inactive_output_profiles(self, card: object) -> list[AudioOutput]:
        if not isinstance(card, dict):
            raise AudioError("The audio service returned invalid output data")
        card_name = card.get("name")
        active = card.get("active_profile")
        profiles = card.get("profiles")
        if not isinstance(card_name, str) or not card_name:
            raise AudioError("The audio service returned invalid output data")
        if not isinstance(profiles, dict):
            raise AudioError("The audio service returned invalid output data")
        outputs: list[AudioOutput] = []
        for profile_name, details in profiles.items():
            if not isinstance(profile_name, str) or not profile_name:
                raise AudioError("The audio service returned invalid output data")
            if profile_name == "off" or "output:" not in profile_name:
                continue
            if profile_name == active:
                continue
            description = profile_name
            if isinstance(details, dict):
                label = details.get("description")
                if isinstance(label, str) and label:
                    description = label
            identifier = f"{_PROFILE_PREFIX}{card_name}:{profile_name}"
            outputs.append(AudioOutput(identifier, profile_name, description))
        return outputs

    def _alsa_digital_outputs(
        self,
        sinks: list[object],
        cards: list[object],
        existing: list[AudioOutput],
    ) -> list[AudioOutput]:
        """Expose ALSA Digital PCMs when PulseAudio has no IEC958 route for the card."""
        covered_cards = self._cards_with_iec958(sinks, cards)
        for output in existing:
            if output.name.startswith(_SPDIF_SINK_PREFIX):
                suffix = output.name.removeprefix(_SPDIF_SINK_PREFIX)
                if suffix.isdigit():
                    covered_cards.add(int(suffix))
            if output.identifier.startswith(_ALSA_PREFIX):
                card_index, _device = self._parse_alsa_identifier(output.identifier)
                covered_cards.add(card_index)
        outputs: list[AudioOutput] = []
        for card_index, device, card_name in self._read_digital_pcms():
            if card_index in covered_cards:
                continue
            identifier = f"{_ALSA_PREFIX}hw:{card_index},{device}"
            description = f"{card_name} Digital Stereo (S/PDIF)"
            outputs.append(AudioOutput(identifier, f"hw:{card_index},{device}", description))
        return outputs

    def _cards_with_iec958(self, sinks: list[object], cards: list[object]) -> set[int]:
        covered: set[int] = set()
        for sink in sinks:
            if not isinstance(sink, dict):
                continue
            name = sink.get("name")
            properties = sink.get("properties") if isinstance(sink.get("properties"), dict) else {}
            if isinstance(name, str) and "iec958" in name.lower():
                alsa_card = properties.get("alsa.card")
                if str(alsa_card).isdigit():
                    covered.add(int(alsa_card))
            profile = properties.get("device.profile.name")
            if isinstance(profile, str) and "iec958" in profile.lower():
                alsa_card = properties.get("alsa.card")
                if str(alsa_card).isdigit():
                    covered.add(int(alsa_card))
        for card in cards:
            if not isinstance(card, dict):
                continue
            index = card.get("index")
            profiles = card.get("profiles")
            if type(index) is not int or not isinstance(profiles, dict):
                continue
            for profile_name in profiles:
                if isinstance(profile_name, str) and "iec958" in profile_name.lower():
                    covered.add(index)
                    break
        return covered

    def _read_digital_pcms(self) -> list[tuple[int, int, str]]:
        cards_path = self._proc / "asound" / "cards"
        try:
            cards_text = cards_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        card_names = {index: name for index, name in self._parse_cards(cards_text)}
        found: list[tuple[int, int, str]] = []
        for card_index, card_name in sorted(card_names.items()):
            card_dir = self._proc / "asound" / f"card{card_index}"
            try:
                entries = list(card_dir.iterdir())
            except OSError:
                continue
            for entry in entries:
                match = _PCM_PLAYBACK.match(entry.name)
                if match is None:
                    continue
                device = int(match.group(1))
                if not self._pcm_is_digital(entry / "info"):
                    continue
                found.append((card_index, device, card_name))
        return found

    def _pcm_is_digital(self, info_path: Path) -> bool:
        try:
            text = info_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        values: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                values[key.strip().lower()] = value.strip()
        stream = values.get("stream", "").lower()
        if stream and stream != "playback":
            return False
        for key in ("id", "name"):
            if values.get(key, "").lower() == "digital":
                return True
        return False

    @staticmethod
    def _parse_cards(text: str) -> list[tuple[int, str]]:
        cards: list[tuple[int, str]] = []
        for line in text.splitlines():
            match = _CARD.match(line)
            if match:
                cards.append((int(match.group(1)), match.group(2)))
        return cards

    def _ensure_alsa_digital(self, identifier: str) -> str:
        card_index, device = self._parse_alsa_identifier(identifier)
        sink_name = f"{_SPDIF_SINK_PREFIX}{card_index}"
        if self._sink_exists(sink_name):
            self._unmute_iec958(card_index)
            return sink_name
        description = f"Digital Stereo (S/PDIF) card {card_index}"
        for card_index_name, device_name, card_name in self._read_digital_pcms():
            if card_index_name == card_index and device_name == device:
                description = f"{card_name} Digital Stereo (S/PDIF)"
                break
        self._run(
            "load-module",
            "module-alsa-sink",
            f"device=hw:{card_index},{device}",
            f"sink_name={sink_name}",
            f"sink_properties=device.description={description}",
        )
        if not self._sink_exists(sink_name):
            raise AudioError("The selected audio output is unavailable")
        self._unmute_iec958(card_index)
        return sink_name

    def _parse_alsa_identifier(self, identifier: str) -> tuple[int, int]:
        remainder = identifier[len(_ALSA_PREFIX) :]
        if not remainder.startswith("hw:"):
            raise AudioError("Invalid audio output")
        card_text, separator, device_text = remainder.removeprefix("hw:").partition(",")
        if not separator or not card_text.isdigit() or not device_text.isdigit():
            raise AudioError("Invalid audio output")
        return int(card_text), int(device_text)

    def _unmute_iec958(self, card_index: int) -> None:
        try:
            subprocess.run(
                ["amixer", "-c", str(card_index), "set", "IEC958", "unmute"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError):
            return

    def _parse_profile_identifier(self, identifier: str) -> tuple[str, str]:
        remainder = identifier[len(_PROFILE_PREFIX) :]
        card_name, separator, profile_name = remainder.partition(":")
        if not separator or not card_name or not profile_name:
            raise AudioError("Invalid audio output")
        return card_name, profile_name

    def _sink_exists(self, sink_name: str) -> bool:
        sinks = self._load_json("list", "sinks")
        if not isinstance(sinks, list):
            raise AudioError("The audio service returned invalid data")
        for sink in sinks:
            if isinstance(sink, dict) and sink.get("name") == sink_name:
                return True
        return False

    def _find_sink_for_profile(self, card_name: str, profile_name: str) -> str | None:
        sinks = self._load_json("list", "sinks")
        if not isinstance(sinks, list):
            raise AudioError("The audio service returned invalid data")
        card_index = self._card_index(card_name)
        preferred: str | None = None
        fallback: str | None = None
        for sink in sinks:
            if not isinstance(sink, dict):
                continue
            name = sink.get("name")
            if not isinstance(name, str) or not name:
                continue
            properties = sink.get("properties") if isinstance(sink.get("properties"), dict) else {}
            alsa_card = properties.get("alsa.card")
            profile = properties.get("device.profile.name")
            card_match = False
            if card_index is not None and str(alsa_card) == str(card_index):
                card_match = True
            elif card_name.startswith("alsa_card."):
                suffix = card_name.removeprefix("alsa_card.")
                if name.startswith(f"alsa_output.{suffix}"):
                    card_match = True
            if not card_match:
                continue
            if profile == profile_name:
                return name
            if preferred is None and isinstance(profile, str) and profile in profile_name:
                preferred = name
            if fallback is None:
                fallback = name
        return preferred or fallback

    def _card_index(self, card_name: str) -> int | None:
        cards = self._load_json("list", "cards")
        if not isinstance(cards, list):
            raise AudioError("The audio service returned invalid data")
        for card in cards:
            if isinstance(card, dict) and card.get("name") == card_name:
                index = card.get("index")
                if type(index) is int:
                    return index
        return None

    def _activate_profile_for_sink(self, sink_name: str) -> None:
        """Best-effort: enable a card profile that might recreate ``sink_name``."""
        cards = self._load_json("list", "cards")
        if not isinstance(cards, list):
            raise AudioError("The audio service returned invalid data")
        for card in cards:
            if not isinstance(card, dict):
                continue
            card_name = card.get("name")
            profiles = card.get("profiles")
            active = card.get("active_profile")
            if not isinstance(card_name, str) or not isinstance(profiles, dict):
                continue
            if not card_name.startswith("alsa_card."):
                continue
            suffix = card_name.removeprefix("alsa_card.")
            if not sink_name.startswith(f"alsa_output.{suffix}"):
                continue
            candidates: list[str] = []
            for profile_name in profiles:
                if not isinstance(profile_name, str) or profile_name == "off":
                    continue
                if "output:" not in profile_name or profile_name == active:
                    continue
                short = profile_name.split("output:", 1)[-1].split("+", 1)[0]
                if short and short in sink_name:
                    candidates.insert(0, profile_name)
                else:
                    candidates.append(profile_name)
            for profile_name in candidates:
                self._run("set-card-profile", card_name, profile_name)
                if self._sink_exists(sink_name):
                    return
