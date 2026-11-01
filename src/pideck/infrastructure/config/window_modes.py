"""Load per-application window policies without changing user settings."""

from pathlib import Path

import yaml

from pideck.domain.configuration import WindowMode
from pideck.domain.errors import ConfigurationError


def load_window_modes(path: Path) -> dict[str, WindowMode]:
    """Read and validate a mapping of application identifiers to window modes."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Unable to load window modes from {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigurationError(f"Window modes in {path} must be a mapping")
    result: dict[str, WindowMode] = {}
    for identifier, value in data.items():
        if not isinstance(identifier, str) or not identifier.strip() or not isinstance(value, str):
            raise ConfigurationError(f"Invalid application window mode in {path}")
        try:
            result[identifier] = WindowMode(value)
        except ValueError as error:
            raise ConfigurationError(f"Invalid window mode for {identifier!r} in {path}: {value!r}") from error
    return result
