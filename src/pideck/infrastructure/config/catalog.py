"""Load bundled installable application definitions separately from user settings."""

from pathlib import Path, PurePosixPath
import re
from typing import Any

import yaml

from pideck.domain.catalog import CatalogEntry, InstallMethod, InstallSpec, UserDefault
from pideck.domain.configuration import WindowMode
from pideck.domain.errors import ConfigurationError
from pideck.infrastructure.config.parser import YamlConfigurationParser

_CATALOG_KEYS = {"install", "user_defaults", "window"}
_SPEC_KEYS = {"method", "packages", "dependencies", "flatpak", "url", "sha256", "executable", "arguments"}
_ARCHITECTURE = re.compile(r"[a-z0-9]+")


def load_catalog(path: Path) -> tuple[CatalogEntry, ...]:
    """Load the catalog using the same validation as configured applications."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Unable to load application catalog {path}: {error}") from error
    if not isinstance(data, dict) or set(data) != {"applications"} or not isinstance(data["applications"], list):
        raise ConfigurationError(f"Invalid application catalog {path}")
    parser = YamlConfigurationParser()
    try:
        entries = tuple(
            _parse_entry(parser, item, index, path.parent)
            for index, item in enumerate(data["applications"])
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"Invalid application catalog {path}: {error}") from error
    if len({entry.identifier for entry in entries}) != len(entries):
        raise ConfigurationError(f"Duplicate application identifier in {path}")
    return entries


def _parse_entry(parser: YamlConfigurationParser, raw: Any, index: int, base: Path) -> CatalogEntry:
    """Split catalog-only keys from the shared application definition."""
    if not isinstance(raw, dict):
        raise ConfigurationError(f"applications[{index}] must be a mapping")
    application = parser.parse_application(
        {key: value for key, value in raw.items() if key not in _CATALOG_KEYS}, index,
    )
    install_data = raw.get("install", {})
    if not isinstance(install_data, dict):
        raise ConfigurationError(f"applications[{index}].install must be a mapping")
    install = {
        architecture: _parse_spec(spec, f"applications[{index}].install.{architecture}")
        for architecture, spec in install_data.items()
        if isinstance(architecture, str) and _ARCHITECTURE.fullmatch(architecture)
    }
    if len(install) != len(install_data):
        raise ConfigurationError(f"applications[{index}].install has an invalid architecture")
    defaults = tuple(
        _parse_user_default(item, base, f"applications[{index}].user_defaults")
        for item in _list(raw.get("user_defaults", []), f"applications[{index}].user_defaults")
    )
    window = raw.get("window")
    return CatalogEntry(
        application=application,
        install=install,
        user_defaults=defaults,
        window_mode=WindowMode(window) if window is not None else None,
    )


def _parse_spec(raw: Any, context: str) -> InstallSpec:
    """Parse one architecture's install instructions."""
    if not isinstance(raw, dict) or not set(raw) <= _SPEC_KEYS or "method" not in raw:
        raise ConfigurationError(f"{context} must define a method and only known keys")
    arguments = raw.get("arguments")
    return InstallSpec(
        method=InstallMethod(raw["method"]),
        packages=tuple(_strings(raw.get("packages", []), f"{context}.packages")),
        dependencies=tuple(_strings(raw.get("dependencies", []), f"{context}.dependencies")),
        flatpak=_optional_string(raw.get("flatpak"), f"{context}.flatpak"),
        url=_optional_string(raw.get("url"), f"{context}.url"),
        sha256=_optional_string(raw.get("sha256"), f"{context}.sha256"),
        executable=_optional_string(raw.get("executable"), f"{context}.executable"),
        arguments=tuple(_strings(arguments, f"{context}.arguments")) if arguments is not None else None,
    )


def _parse_user_default(raw: Any, base: Path, context: str) -> UserDefault:
    """Resolve a bundled first-run file relative to the catalog directory."""
    if not isinstance(raw, dict) or set(raw) != {"source", "target"}:
        raise ConfigurationError(f"{context} entries require source and target")
    source = _optional_string(raw["source"], f"{context}.source")
    target = _optional_string(raw["target"], f"{context}.target")
    if not source or not target:
        raise ConfigurationError(f"{context} entries require source and target")
    return UserDefault(source=(base / source).resolve(), target=PurePosixPath(target))


def _list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{context} must be a list")
    return value


def _strings(value: Any, context: str) -> list[str]:
    items = _list(value, context)
    if not all(isinstance(item, str) and item for item in items):
        raise ConfigurationError(f"{context} must contain non-empty strings")
    return items


def _optional_string(value: Any, context: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ConfigurationError(f"{context} must be a string")
    return value
