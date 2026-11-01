"""Load bundled installable application definitions separately from user settings."""

from pathlib import Path

import yaml

from pideck.domain.configuration import ApplicationDefinition
from pideck.domain.errors import ConfigurationError
from pideck.infrastructure.config.parser import YamlConfigurationParser


def load_catalog(path: Path) -> tuple[ApplicationDefinition, ...]:
    """Load the catalog using the same validation as configured applications."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Unable to load application catalog {path}: {error}") from error
    if not isinstance(data, dict) or set(data) != {"applications"} or not isinstance(data["applications"], list):
        raise ConfigurationError(f"Invalid application catalog {path}")
    parser = YamlConfigurationParser()
    applications = tuple(
        parser.parse_application(item, index)
        for index, item in enumerate(data["applications"])
    )
    if len({item.identifier for item in applications}) != len(applications):
        raise ConfigurationError(f"Duplicate application identifier in {path}")
    return applications
