"""Configuration repository ports."""

from pathlib import Path
from typing import Protocol

from pideck.domain.configuration import Configuration


class ConfigurationRepository(Protocol):
    """Load and persist the effective PiDeck configuration."""

    def load(self) -> Configuration:
        """Load validated configuration data."""
        ...

    def save(self, configuration: Configuration) -> None:
        """Persist validated configuration data atomically."""
        ...


class ConfigurationPathResolver(Protocol):
    """Resolve configuration locations for a runtime environment."""

    def resolve(self) -> Path:
        """Return the writable configuration path."""
        ...
