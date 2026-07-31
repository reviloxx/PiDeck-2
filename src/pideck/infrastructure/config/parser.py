"""Strict YAML parsing into immutable domain configuration models."""

from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from pideck.domain.configuration import (
    ApplicationDefinition,
    ApplicationProfile,
    Configuration,
    HomeScreenConfiguration,
    InputConfiguration,
    PreferredInput,
    SettingsConfiguration,
)
from pideck.domain.errors import (
    ConfigurationError,
    ConfigurationValidationError,
    ThemeValidationError,
)
from pideck.domain.theme import AnimationSpec, FontSpec, ThemeDefinition, TileSpec

_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


class YamlConfigurationParser:
    """Parse and validate one versioned PiDeck YAML document."""

    def load(self, path: Path) -> Configuration:
        """Load one configuration file and return its validated model."""
        try:
            with path.open("r", encoding="utf-8") as configuration_file:
                raw_data = yaml.safe_load(configuration_file)
        except OSError as error:
            raise ConfigurationError(f"Unable to read configuration {path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigurationError(f"Unable to parse configuration {path}: {error}") from error
        return self.parse(raw_data)

    def parse(self, raw_data: Any) -> Configuration:
        """Parse already-loaded YAML data into a validated configuration."""
        document = _mapping(raw_data, "root")
        _require_keys(document, {"version", "applications", "themes", "home", "input", "settings"}, "root")
        version = _integer(document["version"], "version")
        applications = tuple(
            self._parse_application(item, index)
            for index, item in enumerate(_sequence(document["applications"], "applications"))
        )
        themes = tuple(
            self._parse_theme(item, index)
            for index, item in enumerate(_sequence(document["themes"], "themes"))
        )
        home = self._parse_home(document["home"])
        input_configuration = self._parse_input(document["input"])
        settings = self._parse_settings(document["settings"])
        try:
            return Configuration(
                version=version,
                applications=applications,
                themes=themes,
                home=home,
                input=input_configuration,
                settings=settings,
            )
        except ConfigurationValidationError:
            raise
        except ValueError as error:
            raise ConfigurationValidationError(str(error)) from error

    def _parse_application(self, raw_data: Any, index: int) -> ApplicationDefinition:
        """Parse one application definition."""
        context = f"applications[{index}]"
        data = _mapping(raw_data, context)
        _require_keys(
            data,
            {"id", "name", "executable"},
            context,
            optional={"arguments", "profiles", "icon", "preferred_input"},
        )
        profiles = tuple(
            self._parse_profile(item, f"{context}.profiles[{profile_index}]")
            for profile_index, item in enumerate(
                _sequence(data.get("profiles", []), f"{context}.profiles")
            )
        )
        try:
            preferred_input = PreferredInput(
                _string(data.get("preferred_input", "keyboard"), f"{context}.preferred_input")
            )
            icon_value = _nullable_string(data.get("icon"), f"{context}.icon")
            return ApplicationDefinition(
                identifier=_string(data["id"], f"{context}.id"),
                name=_string(data["name"], f"{context}.name"),
                executable=_string(data["executable"], f"{context}.executable"),
                arguments=tuple(_string_list(data.get("arguments", []), f"{context}.arguments")),
                profiles=profiles,
                icon=Path(icon_value) if icon_value else None,
                preferred_input=preferred_input,
            )
        except ValueError as error:
            raise ConfigurationValidationError(f"{context}: {error}") from error

    def _parse_profile(self, raw_data: Any, context: str) -> ApplicationProfile:
        """Parse one selectable application profile."""
        data = _mapping(raw_data, context)
        _require_keys(
            data,
            {"id", "name"},
            context,
            optional={"arguments", "environment", "working_directory"},
        )
        working_directory = _nullable_string(
            data.get("working_directory"), f"{context}.working_directory"
        )
        environment = _string_mapping(data.get("environment", {}), f"{context}.environment")
        return ApplicationProfile(
            identifier=_string(data["id"], f"{context}.id"),
            name=_string(data["name"], f"{context}.name"),
            arguments=tuple(_string_list(data.get("arguments", []), f"{context}.arguments")),
            environment=environment,
            working_directory=Path(working_directory) if working_directory else None,
        )

    def _parse_theme(self, raw_data: Any, index: int) -> ThemeDefinition:
        """Parse one theme definition."""
        context = f"themes[{index}]"
        data = _mapping(raw_data, context)
        _require_keys(
            data,
            {"id", "name", "colors", "fonts", "tile", "animation"},
            context,
            optional={"icons", "wallpaper"},
        )
        colors = _string_mapping(data["colors"], f"{context}.colors")
        invalid_colors = [name for name, value in colors.items() if not _COLOR_PATTERN.fullmatch(value)]
        if invalid_colors:
            names = ", ".join(sorted(invalid_colors))
            raise ConfigurationValidationError(f"{context}.colors has invalid values: {names}")
        fonts_data = _mapping(data["fonts"], f"{context}.fonts")
        _require_keys(fonts_data, {"family", "heading_size", "body_size"}, f"{context}.fonts")
        tile_data = _mapping(data["tile"], f"{context}.tile")
        _require_keys(tile_data, {"width", "height", "gap"}, f"{context}.tile")
        animation_data = _mapping(data["animation"], f"{context}.animation")
        _require_keys(
            animation_data,
            {"duration_ms", "reduced_motion_duration_ms"},
            f"{context}.animation",
        )
        wallpaper_value = _nullable_string(data.get("wallpaper"), f"{context}.wallpaper")
        try:
            return ThemeDefinition(
                identifier=_string(data["id"], f"{context}.id"),
                name=_string(data["name"], f"{context}.name"),
                colors=colors,
                fonts=FontSpec(
                    family=_string(fonts_data["family"], f"{context}.fonts.family"),
                    heading_size=_integer(fonts_data["heading_size"], f"{context}.fonts.heading_size"),
                    body_size=_integer(fonts_data["body_size"], f"{context}.fonts.body_size"),
                ),
                icons={
                    key: Path(value)
                    for key, value in _string_mapping(data.get("icons", {}), f"{context}.icons").items()
                },
                wallpaper=Path(wallpaper_value) if wallpaper_value else None,
                tile=TileSpec(
                    width=_integer(tile_data["width"], f"{context}.tile.width"),
                    height=_integer(tile_data["height"], f"{context}.tile.height"),
                    gap=_integer(tile_data["gap"], f"{context}.tile.gap"),
                ),
                animation=AnimationSpec(
                    duration_ms=_integer(animation_data["duration_ms"], f"{context}.animation.duration_ms"),
                    reduced_motion_duration_ms=_integer(
                        animation_data["reduced_motion_duration_ms"],
                        f"{context}.animation.reduced_motion_duration_ms",
                    ),
                ),
            )
        except (ValueError, ThemeValidationError) as error:
            raise ConfigurationValidationError(f"{context}: {error}") from error

    def _parse_home(self, raw_data: Any) -> HomeScreenConfiguration:
        """Parse home-screen settings."""
        data = _mapping(raw_data, "home")
        _require_keys(data, {"visible_applications", "theme"}, "home")
        return HomeScreenConfiguration(
            visible_applications=tuple(_string_list(data["visible_applications"], "home.visible_applications")),
            theme=_string(data["theme"], "home.theme"),
        )

    def _parse_input(self, raw_data: Any) -> InputConfiguration:
        """Parse input source and binding settings."""
        data = _mapping(raw_data, "input")
        _require_keys(data, {"enabled_sources", "bindings"}, "input")
        return InputConfiguration(
            enabled_sources=tuple(_string_list(data["enabled_sources"], "input.enabled_sources")),
            bindings=_string_mapping(data["bindings"], "input.bindings"),
        )

    def _parse_settings(self, raw_data: Any) -> SettingsConfiguration:
        """Parse launcher settings."""
        data = _mapping(raw_data, "settings")
        _require_keys(data, set(), "settings", optional={"reduced_motion"})
        reduced_motion = data.get("reduced_motion", False)
        if not isinstance(reduced_motion, bool):
            raise ConfigurationValidationError("settings.reduced_motion must be a boolean")
        return SettingsConfiguration(reduced_motion=reduced_motion)


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    """Require a YAML mapping at a schema location."""
    if not isinstance(value, Mapping):
        raise ConfigurationValidationError(f"{context} must be a mapping")
    return value


def _sequence(value: Any, context: str) -> list[Any]:
    """Require a YAML sequence at a schema location."""
    if not isinstance(value, list):
        raise ConfigurationValidationError(f"{context} must be a list")
    return value


def _require_keys(
    data: Mapping[str, Any],
    required: set[str],
    context: str,
    optional: set[str] | None = None,
) -> None:
    """Reject missing and unknown keys to catch configuration mistakes early."""
    allowed = required.union(optional or set())
    actual = set(data)
    missing = required.difference(actual)
    unknown = actual.difference(allowed)
    if missing:
        raise ConfigurationValidationError(f"{context} is missing keys: {', '.join(sorted(missing))}")
    if unknown:
        raise ConfigurationValidationError(f"{context} has unknown keys: {', '.join(sorted(unknown))}")


def _string(value: Any, context: str) -> str:
    """Require a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationValidationError(f"{context} must be a non-empty string")
    return value


def _nullable_string(value: Any, context: str) -> str | None:
    """Accept either a string or YAML null."""
    if value is None:
        return None
    return _string(value, context)


def _integer(value: Any, context: str) -> int:
    """Require an integer without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationValidationError(f"{context} must be an integer")
    return value


def _string_list(value: Any, context: str) -> list[str]:
    """Require a list containing only strings."""
    values = _sequence(value, context)
    return [_string(item, f"{context}[{index}]") for index, item in enumerate(values)]


def _string_mapping(value: Any, context: str) -> dict[str, str]:
    """Require a mapping containing only string keys and values."""
    data = _mapping(value, context)
    result: dict[str, str] = {}
    for key, item in data.items():
        key_string = _string(key, f"{context} key")
        result[key_string] = _string(item, f"{context}.{key_string}")
    return result
