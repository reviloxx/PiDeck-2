"""PulseAudio-compatible audio operations for Linux desktop sessions."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from pideck.application.ports.audio import AudioError, AudioOutput, AudioState

_SINK_PREFIX = "sink:"
_PROFILE_PREFIX = "profile:"
_HDMI_PORT = re.compile(r"hdmi-(?:stereo|surround71|surround)(?:-(extra\d+))?", re.IGNORECASE)
_PASSTHROUGH_FORMATS = "pcm;ac3-iec61937;dts-iec61937;eac3-iec61937"


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
        """Read HDMI sinks and inactive HDMI port profiles, plus default volume."""
        info = self._load_json("info")
        sinks = self._load_json("list", "sinks")
        cards = self._load_json("list", "cards")
        if not isinstance(info, dict) or not isinstance(sinks, list) or not isinstance(cards, list):
            raise AudioError("The audio service returned invalid data")
        default = info.get("default_sink_name")
        all_sinks: list[AudioOutput] = []
        hdmi_sinks: list[AudioOutput] = []
        covered_ports: set[tuple[str, str]] = set()
        current: str | None = None
        current_volume: int | None = None
        for sink in sinks:
            output, volume = self._parse_sink(sink)
            all_sinks.append(output)
            if output.name == default:
                current = output.identifier
                current_volume = volume
            if not self._sink_is_hdmi(sink):
                continue
            hdmi_sinks.append(output)
            port = self._sink_hdmi_port(sink)
            card_name = self._sink_card_name(sink, cards)
            if port is not None and card_name is not None:
                covered_ports.add((card_name, port))
        profile_outputs = self._inactive_hdmi_profiles(cards, covered_ports)
        if hdmi_sinks or profile_outputs:
            outputs = hdmi_sinks + profile_outputs
        else:
            outputs = all_sinks
        if current_volume is None or current is None:
            raise AudioError("No active audio output is available")
        identifiers = {output.identifier for output in outputs}
        if current not in identifiers:
            if hdmi_sinks:
                current = hdmi_sinks[0].identifier
            elif outputs:
                current = outputs[0].identifier
        return AudioState(tuple(outputs), current, current_volume)

    def set_output(self, identifier: str) -> None:
        """Change the default sink, activating an HDMI stereo profile when needed."""
        if not isinstance(identifier, str) or not identifier:
            raise AudioError("Invalid audio output")
        if identifier.startswith(_PROFILE_PREFIX):
            sink_name = self._activate_profile(identifier)
        elif identifier.startswith(_SINK_PREFIX):
            sink_name = self._resolve_sink(identifier)
        else:
            raise AudioError("Invalid audio output")
        sink_name = self._promote_hdmi_stereo(sink_name) or sink_name
        self._run("set-default-sink", sink_name)
        self._enable_passthrough_formats(sink_name)

    def ensure_hdmi_passthrough(self) -> None:
        """Promote the default sink to Digital Stereo (HDMI) and enable bitstream formats."""
        info = self._load_json("info")
        if not isinstance(info, dict):
            raise AudioError("The audio service returned invalid data")
        default = info.get("default_sink_name")
        if not isinstance(default, str) or not default:
            raise AudioError("No active audio output is available")
        promoted = self._promote_hdmi_stereo(default)
        sink_name = promoted or default
        if promoted is not None and promoted != default:
            self._run("set-default-sink", promoted)
        self._enable_passthrough_formats(sink_name)

    def set_volume(self, volume: int) -> None:
        """Set the default output volume without raising it above 100 percent."""
        if type(volume) is not int or not 0 <= volume <= 100:
            raise AudioError("Invalid audio volume")
        self._run("set-sink-volume", "@DEFAULT_SINK@", f"{volume}%")

    def _resolve_sink(self, identifier: str) -> str:
        sink_name = identifier[len(_SINK_PREFIX) :]
        if not sink_name or not self._sink_exists(sink_name):
            raise AudioError("The selected audio output is unavailable")
        return sink_name

    def _activate_profile(self, identifier: str) -> str:
        card_name, profile_name = self._parse_profile_identifier(identifier)
        self._run("set-card-profile", card_name, profile_name)
        sink_name = self._find_sink_for_card(card_name, profile_name)
        if sink_name is None:
            raise AudioError("The selected audio output is unavailable")
        return sink_name

    def _parse_profile_identifier(self, identifier: str) -> tuple[str, str]:
        remainder = identifier[len(_PROFILE_PREFIX) :]
        card_name, separator, profile_name = remainder.partition(":")
        if not separator or not card_name or not profile_name:
            raise AudioError("Invalid audio output")
        return card_name, profile_name

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

    def _enable_passthrough_formats(self, sink_name: str) -> None:
        """Allow AC3/DTS/EAC3 bitstream on the sink when the server supports it."""
        try:
            self._run("set-sink-formats", sink_name, _PASSTHROUGH_FORMATS)
        except AudioError:
            return

    @staticmethod
    def _sink_is_hdmi(sink: object) -> bool:
        if not isinstance(sink, dict):
            return False
        name = sink.get("name") if isinstance(sink.get("name"), str) else ""
        description = sink.get("description") if isinstance(sink.get("description"), str) else ""
        properties = sink.get("properties") if isinstance(sink.get("properties"), dict) else {}
        profile = properties.get("device.profile.name") if isinstance(properties.get("device.profile.name"), str) else ""
        return "hdmi" in f"{name} {description} {profile}".lower()

    @classmethod
    def _hdmi_port_key(cls, text: str) -> str | None:
        match = _HDMI_PORT.search(text)
        if match is None:
            return None
        return match.group(1) or ""

    @classmethod
    def _sink_hdmi_port(cls, sink: object) -> str | None:
        if not isinstance(sink, dict):
            return None
        properties = sink.get("properties") if isinstance(sink.get("properties"), dict) else {}
        profile = properties.get("device.profile.name")
        if isinstance(profile, str):
            port = cls._hdmi_port_key(profile)
            if port is not None:
                return port
        name = sink.get("name")
        if isinstance(name, str):
            return cls._hdmi_port_key(name)
        return None

    @staticmethod
    def _sink_card_name(sink: object, cards: list[object]) -> str | None:
        if not isinstance(sink, dict):
            return None
        properties = sink.get("properties") if isinstance(sink.get("properties"), dict) else {}
        alsa_card = properties.get("alsa.card")
        name = sink.get("name") if isinstance(sink.get("name"), str) else ""
        for card in cards:
            if not isinstance(card, dict):
                continue
            card_name = card.get("name")
            if not isinstance(card_name, str):
                continue
            index = card.get("index")
            if str(alsa_card).isdigit() and type(index) is int and int(alsa_card) == index:
                return card_name
            if card_name.startswith("alsa_card."):
                suffix = card_name.removeprefix("alsa_card.")
                if name.startswith(f"alsa_output.{suffix}"):
                    return card_name
        return None

    def _inactive_hdmi_profiles(
        self,
        cards: list[object],
        covered_ports: set[tuple[str, str]],
    ) -> list[AudioOutput]:
        """One inactive HDMI profile per port, preferring stereo for ARC passthrough."""
        best: dict[tuple[str, str], tuple[int, str, str]] = {}
        for card in cards:
            if not isinstance(card, dict):
                raise AudioError("The audio service returned invalid output data")
            card_name = card.get("name")
            active = card.get("active_profile")
            profiles = card.get("profiles")
            if not isinstance(card_name, str) or not card_name:
                raise AudioError("The audio service returned invalid output data")
            if not isinstance(profiles, dict):
                raise AudioError("The audio service returned invalid output data")
            for profile_name, details in profiles.items():
                if not isinstance(profile_name, str) or not profile_name or profile_name == "off":
                    continue
                if profile_name == active:
                    continue
                port = self._hdmi_port_key(profile_name)
                if port is None:
                    continue
                score = self._hdmi_profile_score(profile_name)
                if score is None:
                    continue
                key = (card_name, port)
                if key in covered_ports:
                    continue
                description = profile_name
                if isinstance(details, dict):
                    label = details.get("description")
                    if isinstance(label, str) and label:
                        description = label
                previous = best.get(key)
                if previous is None or score < previous[0]:
                    best[key] = (score, profile_name, description)
        outputs: list[AudioOutput] = []
        for card_name, port in sorted(best, key=lambda item: (item[0], item[1])):
            _score, profile_name, description = best[(card_name, port)]
            outputs.append(
                AudioOutput(
                    f"{_PROFILE_PREFIX}{card_name}:{profile_name}",
                    profile_name,
                    description,
                )
            )
        return outputs

    @staticmethod
    def _hdmi_profile_score(profile_name: str) -> int | None:
        """Lower is better: stereo (passthrough), then 5.1, then 7.1."""
        lower = profile_name.lower()
        if "hdmi" not in lower:
            return None
        if "surround71" in lower or "7.1" in lower:
            return 2
        if "surround" in lower:
            return 1
        if "stereo" in lower:
            return 0
        return None

    def _promote_hdmi_stereo(self, sink_name: str) -> str | None:
        """Switch the sink's card to Digital Stereo (HDMI) on the same port when available."""
        card = self._card_for_sink(sink_name)
        if card is None:
            return None
        card_name, active, profiles = card
        if self._is_hdmi_stereo_profile(active):
            return sink_name
        port = self._hdmi_port_key(active or "") or self._hdmi_port_key(sink_name)
        profile = self._best_hdmi_stereo_profile(profiles, port_key=port)
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
    def _is_hdmi_stereo_profile(profile: str | None) -> bool:
        if not isinstance(profile, str):
            return False
        lower = profile.lower()
        return "hdmi" in lower and "stereo" in lower and "surround" not in lower

    @classmethod
    def _best_hdmi_stereo_profile(
        cls,
        profiles: dict[str, object],
        *,
        port_key: str | None,
    ) -> str | None:
        """Pick Digital Stereo (HDMI) on the given port."""
        for name in profiles:
            if not isinstance(name, str) or name == "off":
                continue
            if not cls._is_hdmi_stereo_profile(name):
                continue
            this_port = cls._hdmi_port_key(name)
            if port_key is not None and this_port != port_key:
                continue
            return name
        return None

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
