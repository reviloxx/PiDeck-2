"""PulseAudio-compatible audio operations for Linux desktop sessions."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from pideck.application.ports.audio import AudioError, AudioOutput, AudioState

_SINK_PREFIX = "sink:"


class PulseAudioGateway:
    """Operate a PulseAudio or PipeWire PulseAudio-compatible server."""

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
        """Read active sinks and the default output volume."""
        info = self._load_json("info")
        sinks = self._load_json("list", "sinks")
        if not isinstance(info, dict) or not isinstance(sinks, list):
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
        if current is None or current_volume is None:
            raise AudioError("No active audio output is available")
        return AudioState(tuple(outputs), current, current_volume)

    def set_output(self, identifier: str) -> None:
        """Change the default sink, promoting HDMI cards to surround 5.1 when available."""
        sink_name = self._resolve_sink(identifier)
        sink_name = self._promote_hdmi_surround(sink_name) or sink_name
        self._run("set-default-sink", sink_name)

    def ensure_hdmi_surround(self) -> None:
        """Promote the default sink to Digital Surround 5.1 (HDMI) when the card supports it."""
        info = self._load_json("info")
        if not isinstance(info, dict):
            raise AudioError("The audio service returned invalid data")
        default = info.get("default_sink_name")
        if not isinstance(default, str) or not default:
            raise AudioError("No active audio output is available")
        promoted = self._promote_hdmi_surround(default)
        if promoted is not None and promoted != default:
            self._run("set-default-sink", promoted)

    def set_volume(self, volume: int) -> None:
        """Set the default output volume without raising it above 100 percent."""
        if type(volume) is not int or not 0 <= volume <= 100:
            raise AudioError("Invalid audio volume")
        self._run("set-sink-volume", "@DEFAULT_SINK@", f"{volume}%")

    def _resolve_sink(self, identifier: str) -> str:
        if not isinstance(identifier, str) or not identifier.startswith(_SINK_PREFIX):
            raise AudioError("Invalid audio output")
        sink_name = identifier[len(_SINK_PREFIX) :]
        if not sink_name or not self._sink_exists(sink_name):
            raise AudioError("The selected audio output is unavailable")
        return sink_name

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

    def _sink_exists(self, sink_name: str) -> bool:
        sinks = self._load_json("list", "sinks")
        if not isinstance(sinks, list):
            raise AudioError("The audio service returned invalid data")
        for sink in sinks:
            if isinstance(sink, dict) and sink.get("name") == sink_name:
                return True
        return False

    def _promote_hdmi_surround(self, sink_name: str) -> str | None:
        """Switch the sink's card to HDMI surround 5.1 when available.

        Returns the new sink name after a profile change, ``sink_name`` when already
        surround, or ``None`` when the card has no HDMI surround profile.
        """
        card = self._card_for_sink(sink_name)
        if card is None:
            return None
        card_name, active, profiles = card
        if self._is_hdmi_surround_profile(active):
            return sink_name
        profile = self._best_hdmi_surround_profile(profiles)
        if profile is None:
            return None
        self._run("set-card-profile", card_name, profile)
        found = self._find_sink_for_card(card_name, profile)
        return found or sink_name

    def _card_for_sink(self, sink_name: str) -> tuple[str, str | None, dict[str, object]] | None:
        sinks = self._load_json("list", "sinks")
        cards = self._load_json("list", "cards")
        if not isinstance(sinks, list) or not isinstance(cards, list):
            raise AudioError("The audio service returned invalid data")
        sink_props: dict[str, object] = {}
        for sink in sinks:
            if isinstance(sink, dict) and sink.get("name") == sink_name:
                props = sink.get("properties")
                if isinstance(props, dict):
                    sink_props = props
                break
        else:
            return None
        alsa_card = sink_props.get("alsa.card")
        for card in cards:
            if not isinstance(card, dict):
                continue
            card_name = card.get("name")
            profiles = card.get("profiles")
            active = card.get("active_profile")
            if not isinstance(card_name, str) or not isinstance(profiles, dict):
                continue
            index = card.get("index")
            if str(alsa_card).isdigit() and type(index) is int and int(alsa_card) == index:
                return card_name, active if isinstance(active, str) else None, profiles
            if card_name.startswith("alsa_card."):
                suffix = card_name.removeprefix("alsa_card.")
                if sink_name.startswith(f"alsa_output.{suffix}"):
                    return card_name, active if isinstance(active, str) else None, profiles
        return None

    @staticmethod
    def _is_hdmi_surround_profile(profile: str | None) -> bool:
        if not isinstance(profile, str):
            return False
        lower = profile.lower()
        return "hdmi" in lower and "surround" in lower

    @staticmethod
    def _best_hdmi_surround_profile(profiles: dict[str, object]) -> str | None:
        """Prefer Digital Surround 5.1 (HDMI) over 7.1 when both exist."""
        candidates: list[tuple[int, str]] = []
        for name in profiles:
            if not isinstance(name, str) or name == "off":
                continue
            lower = name.lower()
            if "hdmi" not in lower or "surround" not in lower:
                continue
            # Prefer 5.1 (hdmi-surround) over 7.1 (hdmi-surround71).
            priority = 1 if ("71" in lower or "7.1" in lower) else 0
            candidates.append((priority, name))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1]

    def _find_sink_for_card(self, card_name: str, profile_name: str) -> str | None:
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
