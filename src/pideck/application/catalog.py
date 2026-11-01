"""Register explicitly installed catalog applications without replacing user settings."""

from dataclasses import replace
import logging
from pathlib import Path
import shlex

from pideck.domain.configuration import ApplicationDefinition, Configuration
from pideck.domain.errors import ConfigurationValidationError

_LOGGER = logging.getLogger(__name__)


def available_applications(
    configuration: Configuration, catalog: tuple[ApplicationDefinition, ...],
) -> tuple[ApplicationDefinition, ...]:
    """Exclude catalog entries already represented by a user's application."""
    identifiers = {item.identifier for item in configuration.applications}
    targets = {_flatpak_target(item) for item in configuration.applications}
    return tuple(
        item for item in catalog
        if item.identifier not in identifiers
        and (_flatpak_target(item) is None or _flatpak_target(item) not in targets)
    )


def _flatpak_target(application: ApplicationDefinition) -> str | None:
    """Identify a Flatpak run target without invoking any external command."""
    try:
        command = (*shlex.split(application.executable), *application.arguments)
    except ValueError as error:
        _LOGGER.warning("Unable to identify Flatpak application %s: %s", application.identifier, error)
        return None
    if not command or Path(command[0]).name != "flatpak" or "run" not in command:
        return None
    arguments = command[command.index("run") + 1:]
    return next((value for value in arguments if not value.startswith("-") and "." in value), None)


def register_application(configuration: Configuration, application: ApplicationDefinition) -> Configuration:
    """Add an installed application and make it visible without changing other choices."""
    if any(item.identifier == application.identifier for item in configuration.applications):
        raise ConfigurationValidationError(f"Application ID already exists: {application.identifier}")
    visible = configuration.home.visible_applications
    if visible:
        visible = (*visible, application.identifier)
    return replace(
        configuration,
        applications=(*configuration.applications, application),
        home=replace(configuration.home, visible_applications=visible),
    )
