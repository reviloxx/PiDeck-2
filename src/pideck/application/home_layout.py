"""Home-screen order of configured applications, without Qt.

Every configured application is shown on the home screen. The tile order is
``home.visible_applications`` when that list is non-empty, otherwise the order
of ``applications``. An empty visibility list is the show-all sentinel so older
releases keep reading the same settings file.
"""

from dataclasses import replace

from pideck.domain.configuration import Configuration
from pideck.domain.errors import ConfigurationValidationError


def application_order(configuration: Configuration) -> tuple[str, ...]:
    """Return configured identifiers in home-screen tile order."""
    identifiers = tuple(item.identifier for item in configuration.applications)
    visible = configuration.home.visible_applications
    if not visible:
        return identifiers
    visible_set = set(identifiers)
    ordered = [identifier for identifier in visible if identifier in visible_set]
    remaining = [identifier for identifier in identifiers if identifier not in set(ordered)]
    return tuple(ordered + remaining)


def is_visible(configuration: Configuration, identifier: str) -> bool:
    """Return whether an application is shown on the home screen."""
    return any(item.identifier == identifier for item in configuration.applications)


def normalize_home(configuration: Configuration) -> Configuration:
    """Show every configured application and keep the current tile order."""
    order = application_order(configuration)
    by_id = {item.identifier: item for item in configuration.applications}
    applications = tuple(by_id[identifier] for identifier in order)
    if applications == configuration.applications and not configuration.home.visible_applications:
        return configuration
    return replace(
        configuration,
        applications=applications,
        home=replace(configuration.home, visible_applications=()),
    )


def set_application_order(configuration: Configuration, order: tuple[str, ...]) -> Configuration:
    """Apply a complete home-screen order for every configured application."""
    current = application_order(configuration)
    if order == current:
        return configuration
    if len(order) != len(current) or set(order) != set(current):
        raise ConfigurationValidationError("Application order must list every configured application once")
    by_id = {item.identifier: item for item in configuration.applications}
    return replace(
        configuration,
        applications=tuple(by_id[identifier] for identifier in order),
        home=replace(configuration.home, visible_applications=()),
    )
