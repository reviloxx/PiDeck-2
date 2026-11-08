"""Merge release-shipped themes into the user's saved configuration."""

from dataclasses import replace
import logging
from pathlib import Path

from pideck.domain.configuration import Configuration
from pideck.domain.errors import ConfigurationError
from pideck.domain.theme import ThemeDefinition
from pideck.infrastructure.config.parser import YamlConfigurationParser

_LOGGER = logging.getLogger(__name__)


def shipped_themes_path(asset_root: Path) -> Path:
    """Return the themes manifest next to release assets."""
    return asset_root.parent / "assets" / "themes" / "themes.yaml"


def merge_themes(
    configured: tuple[ThemeDefinition, ...],
    shipped: tuple[ThemeDefinition, ...],
) -> tuple[ThemeDefinition, ...]:
    """Prefer shipped definitions, then keep any user-only custom themes."""
    by_id = {theme.identifier: theme for theme in configured}
    for theme in shipped:
        by_id[theme.identifier] = theme
    ordered: list[ThemeDefinition] = []
    seen: set[str] = set()
    for theme in shipped:
        ordered.append(by_id[theme.identifier])
        seen.add(theme.identifier)
    for theme in configured:
        if theme.identifier not in seen:
            ordered.append(theme)
            seen.add(theme.identifier)
    return tuple(ordered)


def apply_shipped_themes(
    configuration: Configuration,
    asset_root: Path,
    *,
    parser: YamlConfigurationParser | None = None,
    logger: logging.Logger | None = None,
) -> Configuration:
    """Replace saved theme definitions with the release catalog when available."""
    log = logger or _LOGGER
    path = shipped_themes_path(asset_root)
    if not path.is_file():
        return configuration
    try:
        shipped = (parser or YamlConfigurationParser()).load_themes(path)
    except ConfigurationError as error:
        log.warning("Unable to load shipped themes path=%s error=%s", path, error)
        return configuration
    if not shipped:
        return configuration
    merged = merge_themes(configuration.themes, shipped)
    if merged == configuration.themes:
        return configuration
    log.info("Merged shipped themes count=%d path=%s", len(merged), path)
    return replace(configuration, themes=merged)
