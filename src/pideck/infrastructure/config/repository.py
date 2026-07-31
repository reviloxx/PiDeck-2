"""Filesystem-backed YAML configuration repository."""

import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import yaml

from pideck.domain.configuration import Configuration
from pideck.domain.errors import ConfigurationError
from pideck.infrastructure.config.defaults import default_configuration
from pideck.infrastructure.config.parser import YamlConfigurationParser

_LOGGER = logging.getLogger(__name__)


class FileConfigurationRepository:
    """Load configuration from YAML and persist updates atomically."""

    def __init__(self, path: Path, parser: YamlConfigurationParser | None = None) -> None:
        """Create a repository for one writable configuration path."""
        self._path = path
        self._parser = parser or YamlConfigurationParser()

    @property
    def path(self) -> Path:
        """Return the configured YAML path."""
        return self._path

    def load(self) -> Configuration:
        """Load validated configuration or recover to safe defaults."""
        if not self._path.exists():
            _LOGGER.info("Configuration file does not exist; using defaults path=%s", self._path)
            return default_configuration()
        try:
            return self._parser.load(self._path)
        except ConfigurationError as error:
            _LOGGER.warning("Invalid configuration path=%s error=%s", self._path, error)
            backup_path = self._backup_path
            if backup_path.exists():
                try:
                    _LOGGER.info("Loading last-known-good configuration path=%s", backup_path)
                    return self._parser.load(backup_path)
                except ConfigurationError as backup_error:
                    _LOGGER.warning(
                        "Last-known-good configuration is invalid path=%s error=%s",
                        backup_path,
                        backup_error,
                    )
            _LOGGER.warning("Using default configuration path=%s", self._path)
            return default_configuration()

    @property
    def _backup_path(self) -> Path:
        """Return the path used for the last-known-good configuration."""
        return self._path.with_name(f"{self._path.name}.bak")

    def save(self, configuration: Configuration) -> None:
        """Serialize configuration and replace the target atomically."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = _serialize_configuration(configuration)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                yaml.safe_dump(serialized, temporary_file, sort_keys=False, allow_unicode=False)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            if self._path.exists():
                shutil.copy2(self._path, self._backup_path)
            os.replace(temporary_path, self._path)
        except (OSError, yaml.YAMLError) as error:
            raise ConfigurationError(f"Unable to save configuration {self._path}: {error}") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _serialize_configuration(configuration: Configuration) -> dict[str, Any]:
    """Convert immutable domain models to safe YAML-compatible values."""
    return {
        "version": configuration.version,
        "applications": [
            {
                "id": application.identifier,
                "name": application.name,
                "executable": application.executable,
                "arguments": list(application.arguments),
                "profiles": [
                    {
                        "id": profile.identifier,
                        "name": profile.name,
                        "arguments": list(profile.arguments),
                        "environment": dict(profile.environment),
                        "working_directory": (
                            str(profile.working_directory)
                            if profile.working_directory is not None
                            else None
                        ),
                    }
                    for profile in application.profiles
                ],
                "icon": str(application.icon) if application.icon is not None else None,
                "preferred_input": application.preferred_input.value,
            }
            for application in configuration.applications
        ],
        "themes": [
            {
                "id": theme.identifier,
                "name": theme.name,
                "colors": dict(theme.colors),
                "fonts": {
                    "family": theme.fonts.family,
                    "heading_size": theme.fonts.heading_size,
                    "body_size": theme.fonts.body_size,
                },
                "icons": {key: str(path) for key, path in theme.icons.items()},
                "wallpaper": str(theme.wallpaper) if theme.wallpaper is not None else None,
                "tile": {
                    "width": theme.tile.width,
                    "height": theme.tile.height,
                    "gap": theme.tile.gap,
                },
                "animation": {
                    "duration_ms": theme.animation.duration_ms,
                    "reduced_motion_duration_ms": theme.animation.reduced_motion_duration_ms,
                },
            }
            for theme in configuration.themes
        ],
        "home": {
            "visible_applications": list(configuration.home.visible_applications),
            "theme": configuration.home.theme,
        },
        "input": {
            "enabled_sources": list(configuration.input.enabled_sources),
            "bindings": dict(configuration.input.bindings),
        },
        "settings": {"reduced_motion": configuration.settings.reduced_motion},
    }
