"""System audio models and operations independent of the presentation layer."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class AudioError(Exception):
    """Report an unavailable audio service or rejected audio operation."""


class HdmiAudioMode(StrEnum):
    """How HDMI audio should be prepared for the connected display/AVR."""

    SURROUND = "surround"
    """Multichannel PCM 5.1 — use with PC HDMI → AVR → TV."""

    BITSTREAM = "bitstream"
    """Stereo HDMI + AC3/DTS passthrough — use with PC → TV → ARC → AVR."""


@dataclass(frozen=True, slots=True)
class AudioOutput:
    """Describe one selectable output exposed by the audio server."""

    identifier: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class AudioState:
    """Describe available outputs and the active system default volume."""

    outputs: tuple[AudioOutput, ...]
    current: str
    volume: int


class AudioGateway(Protocol):
    """Inspect and adjust host audio through an injected platform adapter."""

    def inspect(self, *, hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND) -> AudioState:
        """Return the available outputs and current default output volume."""
        ...

    def set_output(
        self,
        identifier: str,
        *,
        hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND,
    ) -> None:
        """Select the system default output using the chosen HDMI mode."""
        ...

    def ensure_hdmi(self, *, hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND) -> None:
        """Promote the default sink for the chosen HDMI mode."""
        ...

    def set_volume(self, volume: int) -> None:
        """Set the active output volume as a percentage from zero to 100."""
        ...
