"""Home-screen order and visibility of configured applications, without Qt.

The tile order is the order of ``home.visible_applications``. Hidden
applications keep their place in ``applications`` so that showing them again
restores their position. Only existing configuration keys are rewritten, so
saved settings stay readable by older releases.
"""

from dataclasses import replace

from pideck.domain.configuration import Configuration
from pideck.domain.errors import ConfigurationValidationError


def application_order(configuration: Configuration) -> tuple[str, ...]:
    """Return all configured identifiers in home-screen order, hidden ones in place.

    Visible applications follow their tile order. Hidden applications are kept
    behind the visible application that precedes them in ``applications``.
    """
    identifiers = tuple(item.identifier for item in configuration.applications)
    visible = configuration.home.visible_applications
    if not visible:
        return identifiers
    visible_set = set(visible)
    order = [identifier for identifier in visible if identifier in identifiers]
    previous: str | None = None
    for identifier in identifiers:
        if identifier in visible_set:
            previous = identifier
            continue
        index = order.index(previous) + 1 if previous is not None else 0
        while index < len(order) and order[index] not in visible_set:
            index += 1
        order.insert(index, identifier)
        previous = identifier
    return tuple(order)


def is_visible(configuration: Configuration, identifier: str) -> bool:
    """Return whether an application is shown on the home screen."""
    visible = configuration.home.visible_applications
    return not visible or identifier in visible


def set_visibility(configuration: Configuration, identifier: str, visible: bool) -> Configuration:
    """Show or hide an application, keeping at least one application visible."""
    order = application_order(configuration)
    if identifier not in order:
        raise ConfigurationValidationError(f"Unknown application: {identifier}")
    shown = {item for item in order if is_visible(configuration, item)}
    if visible:
        shown.add(identifier)
    else:
        shown.discard(identifier)
    if not shown:
        raise ConfigurationValidationError("At least one application must remain visible")
    return _apply(configuration, order, shown)


def set_application_order(configuration: Configuration, order: tuple[str, ...]) -> Configuration:
    """Apply a complete home-screen order, keeping each application's visibility."""
    current = application_order(configuration)
    if order == current:
        return configuration
    if len(order) != len(current) or set(order) != set(current):
        raise ConfigurationValidationError("Application order must list every configured application once")
    shown = {item for item in order if is_visible(configuration, item)}
    return _apply(configuration, order, shown)


def _apply(configuration: Configuration, order: tuple[str, ...] | list[str], shown: set[str]) -> Configuration:
    by_id = {item.identifier: item for item in configuration.applications}
    return replace(
        configuration,
        applications=tuple(by_id[identifier] for identifier in order),
        home=replace(
            configuration.home,
            visible_applications=tuple(identifier for identifier in order if identifier in shown),
        ),
    )
