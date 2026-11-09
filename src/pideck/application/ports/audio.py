"""System audio models and operations independent of the presentation layer."""

from dataclasses import dataclass
from typing import Protocol


class AudioError(Exception):
    """Report an unavailable audio service or rejected audio operation."""


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

    def inspect(self) -> AudioState:
        """Return the available outputs and current default output volume."""
        ...

    def set_output(self, identifier: str) -> None:
        """Select the system default output, activating a card profile when needed."""
        ...

    def ensure_output(self, identifier: str) -> str:
        """Make ``identifier`` usable and return its PulseAudio sink name."""
        ...

    def set_volume(self, volume: int) -> None:
        """Set the active output volume as a percentage from zero to 100."""
        ...
