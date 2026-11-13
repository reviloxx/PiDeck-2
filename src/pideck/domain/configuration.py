"""Immutable configuration models for PiDeck."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .backgrounds import BACKGROUND_STYLE_ID_SET, DEFAULT_BACKGROUND
from .errors import ConfigurationValidationError
from .snowflakes import DEFAULT_SNOWFLAKES, SNOWFLAKES_MODE_SET
from .theme import ThemeDefinition


class PreferredInput(StrEnum):
    """Input source displayed as the preferred control for an application."""

    CEC = "cec"
    GAMEPAD = "gamepad"
    KEYBOARD = "keyboard"
    MOUSE = "mouse"

class CecMode(StrEnum):
    """How HDMI-CEC remote input is handled while an application runs."""

    RELEASE = "release"
    """Hand the CEC adapter to the application (e.g. Kodi with its own libCEC)."""
    KEYBOARD = "keyboard"
    """Keep the adapter in PiDeck and forward remote keys as keyboard input."""


class WindowMode(StrEnum):
    """Preferred presentation for an application's main window."""

    NORMAL = "normal"
    MAXIMIZED = "maximized"
    FULLSCREEN = "fullscreen"


@dataclass(frozen=True, slots=True)
class ApplicationProfile:
    """Selectable launch parameters for one application."""

    identifier: str
    name: str
    arguments: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    working_directory: Path | None = None

    def __post_init__(self) -> None:
        """Validate profile values and freeze the environment mapping."""
        if not self.identifier.strip():
            raise ConfigurationValidationError("Application profile identifier must not be empty")
        if not self.name.strip():
            raise ConfigurationValidationError("Application profile name must not be empty")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))


@dataclass(frozen=True, slots=True)
class ApplicationDefinition:
    """Describe an application tile and its available launch profiles."""

    identifier: str
    name: str
    executable: str
    arguments: tuple[str, ...] = ()
    profiles: tuple[ApplicationProfile, ...] = ()
    icon: Path | None = None
    preferred_input: PreferredInput = PreferredInput.KEYBOARD
    window_mode: WindowMode | None = None
    cec_mode: CecMode | None = None

    def __post_init__(self) -> None:
        """Validate application identity and normalize collection values."""
        if not self.identifier.strip():
            raise ConfigurationValidationError("Application identifier must not be empty")
        if not self.name.strip():
            raise ConfigurationValidationError("Application name must not be empty")
        if not self.executable.strip():
            raise ConfigurationValidationError(
                f"Application {self.identifier!r} executable must not be empty"
            )
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "profiles", tuple(self.profiles))
        profile_ids = [profile.identifier for profile in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ConfigurationValidationError(
                f"Application {self.identifier!r} profile identifiers must be unique"
            )


@dataclass(frozen=True, slots=True)
class HomeScreenConfiguration:
    """Define home-screen tile order, theme, background, and snowflake mode.

    An empty ``visible_applications`` list means every configured application is
    shown, using the ``applications`` order.
    """

    visible_applications: tuple[str, ...]
    theme: str
    background: str = DEFAULT_BACKGROUND
    snowflakes: str = DEFAULT_SNOWFLAKES

    def __post_init__(self) -> None:
        """Validate home-screen references."""
        if not self.theme.strip():
            raise ConfigurationValidationError("Home-screen theme must not be empty")
        if not self.background.strip():
            raise ConfigurationValidationError("Home-screen background must not be empty")
        if self.background not in BACKGROUND_STYLE_ID_SET:
            raise ConfigurationValidationError(
                f"Home-screen background does not exist: {self.background!r}"
            )
        if not self.snowflakes.strip():
            raise ConfigurationValidationError("Home-screen snowflakes mode must not be empty")
        if self.snowflakes not in SNOWFLAKES_MODE_SET:
            raise ConfigurationValidationError(
                f"Home-screen snowflakes mode does not exist: {self.snowflakes!r}"
            )
        object.__setattr__(self, "visible_applications", tuple(self.visible_applications))


@dataclass(frozen=True, slots=True)
class InputConfiguration:
    """Configure available input sources and global bindings."""

    enabled_sources: tuple[str, ...]
    bindings: Mapping[str, str]

    def __post_init__(self) -> None:
        """Normalize input collections and freeze bindings."""
        object.__setattr__(self, "enabled_sources", tuple(self.enabled_sources))
        object.__setattr__(self, "bindings", MappingProxyType(dict(self.bindings)))


@dataclass(frozen=True, slots=True)
class SettingsConfiguration:
    """Store launcher-wide settings persisted in YAML."""

    language: str = "en"
    update_check_interval_hours: int = 6

    def __post_init__(self) -> None:
        """Validate the supported presentation languages."""
        if self.language not in {"en", "de"}:
            raise ConfigurationValidationError(
                f"Unsupported language: {self.language!r}"
            )
        if type(self.update_check_interval_hours) is not int or not 1 <= self.update_check_interval_hours <= 596:
            raise ConfigurationValidationError("Update check interval must be between 1 and 596 hours")


@dataclass(frozen=True, slots=True)
class Configuration:
    """Complete validated PiDeck configuration."""

    version: int
    applications: tuple[ApplicationDefinition, ...]
    themes: tuple[ThemeDefinition, ...]
    home: HomeScreenConfiguration
    input: InputConfiguration
    settings: SettingsConfiguration

    def __post_init__(self) -> None:
        """Validate schema version and duplicate identifiers."""
        if self.version != 1:
            raise ConfigurationValidationError(
                f"Unsupported configuration version: {self.version}"
            )
        object.__setattr__(self, "applications", tuple(self.applications))
        object.__setattr__(self, "themes", tuple(self.themes))
        application_ids = [application.identifier for application in self.applications]
        if len(application_ids) != len(set(application_ids)):
            raise ConfigurationValidationError("Application identifiers must be unique")
        theme_ids = [theme.identifier for theme in self.themes]
        if len(theme_ids) != len(set(theme_ids)):
            raise ConfigurationValidationError("Theme identifiers must be unique")
        if self.home.theme not in theme_ids:
            raise ConfigurationValidationError(
                f"Home-screen theme does not exist: {self.home.theme!r}"
            )
        unknown_visible = set(self.home.visible_applications).difference(application_ids)
        if unknown_visible:
            unknown = ", ".join(sorted(unknown_visible))
            raise ConfigurationValidationError(
                f"Home screen references unknown applications: {unknown}"
            )
