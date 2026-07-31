"""Composition root and Milestone 1 validation entry point."""

import argparse
import logging
import os
from pathlib import Path
from typing import Sequence

from pideck.application.dependencies import ApplicationDependencies
from pideck.infrastructure.config import FileConfigurationRepository
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.logging import configure_logging
from pideck.infrastructure.theme import FileThemeRepository

_LOGGER = logging.getLogger(__name__)


def default_configuration_path() -> Path:
    """Return the platform-appropriate writable configuration path."""
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_root / "pideck" / "pideck.yaml"


def build_dependencies(
    configuration_path: Path,
    asset_root: Path | None = None,
    log_file: Path | None = None,
    log_level: str = "INFO",
) -> ApplicationDependencies:
    """Build the typed runtime graph without creating a Qt application."""
    logger = configure_logging(level=log_level, log_file=log_file)
    configuration_repository = FileConfigurationRepository(configuration_path)
    configuration = configuration_repository.load()
    theme_repository = FileThemeRepository(
        themes=configuration.themes,
        asset_root=asset_root or configuration_path.parent,
        fallback=DEFAULT_THEME,
    )
    logger.info(
        "PiDeck foundation initialized applications=%d themes=%d configuration=%s",
        len(configuration.applications),
        len(configuration.themes),
        configuration_path,
    )
    return ApplicationDependencies(
        configuration=configuration,
        configuration_repository=configuration_repository,
        theme_repository=theme_repository,
        logger=logger,
    )


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate configuration and initialize Milestone 1 dependencies."""
    parser = argparse.ArgumentParser(description="PiDeck foundation bootstrap")
    parser.add_argument("--config", type=Path, default=default_configuration_path())
    parser.add_argument("--asset-root", type=Path, default=None)
    parser.add_argument("--log-file", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    parsed_arguments = parser.parse_args(arguments)
    build_dependencies(
        configuration_path=parsed_arguments.config,
        asset_root=parsed_arguments.asset_root,
        log_file=parsed_arguments.log_file,
        log_level=parsed_arguments.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
