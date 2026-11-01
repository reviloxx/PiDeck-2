"""System audio models and operations independent of the presentation layer."""

from dataclasses import dataclass
from typing import Protocol


class AudioError(Exception):
    """Report an unavailable audio service or rejected audio operation."""


@dataclass(frozen=True, slots=True)
class AudioOutput:
    """Describe one selectable output exposed by the audio server."""

    identifier: int
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class AudioState:
    """Describe the active output and its current volume."""

    outputs: tuple[AudioOutput, ...]
    current: int
    volume: int


class AudioGateway(Protocol):
    """Inspect and adjust host audio through an injected platform adapter."""

    def inspect(self) -> AudioState:
        """Return the available outputs and current output volume."""
        ...

    def set_output(self, identifier: int) -> None:
        """Select an available output."""
        ...

    def set_volume(self, volume: int) -> None:
        """Set the active output volume as a percentage from zero to 100."""
        ...
