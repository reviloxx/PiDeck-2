"""Register and unregister catalog applications without replacing other user settings."""

from collections.abc import Iterable
from dataclasses import replace
import logging
from pathlib import Path
import shlex

from pideck.domain.catalog import CatalogEntry
from pideck.domain.configuration import ApplicationDefinition, CecMode, Configuration, WindowMode
from pideck.domain.errors import ConfigurationValidationError

_LOGGER = logging.getLogger(__name__)


def configured_application(
    configuration: Configuration, entry: CatalogEntry,
) -> ApplicationDefinition | None:
    """Return the user's application that represents a catalog entry, if any.

    Besides the identifier, a matching Flatpak target counts, so custom
    entries for the same Flatpak are recognized as installed.
    """
    for item in configuration.applications:
        if item.identifier == entry.identifier:
            return item
    targets = {
        target for target in (
            _flatpak_target(entry.application),
            *(spec.flatpak for spec in entry.install.values()),
        ) if target is not None
    }
    return next((item for item in configuration.applications if _flatpak_target(item) in targets), None)


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


def effective_cec_mode(
    application: ApplicationDefinition, catalog: Iterable[CatalogEntry],
) -> CecMode:
    """Resolve CEC handling, inheriting it from a matching catalog entry when unset.

    Applications installed before the catalog declared a mode keep their saved
    definition with ``cec_mode`` null. Matching by identifier or Flatpak target
    lets them benefit from catalog improvements without rewriting the user's
    configuration.
    """
    if application.cec_mode is not None:
        return application.cec_mode
    for entry in catalog:
        if entry.identifier == application.identifier and entry.application.cec_mode is not None:
            return entry.application.cec_mode
    target = _flatpak_target(application)
    if target is not None:
        for entry in catalog:
            if entry.application.cec_mode is not None and _flatpak_target(entry.application) == target:
                return entry.application.cec_mode
    return CecMode.RELEASE


def catalog_window_modes(catalog: Iterable[CatalogEntry]) -> dict[str, WindowMode]:
    """Return default window modes declared by the catalog, keyed by identifier."""
    return {entry.identifier: entry.window_mode for entry in catalog if entry.window_mode is not None}


def register_application(configuration: Configuration, application: ApplicationDefinition) -> Configuration:
    """Add an installed application and make it visible without changing other choices.

    The catalog's CEC mode is not copied: it is resolved at runtime through
    ``effective_cec_mode`` so the saved settings stay readable by older releases.
    """
    if any(item.identifier == application.identifier for item in configuration.applications):
        raise ConfigurationValidationError(f"Application ID already exists: {application.identifier}")
    application = replace(application, cec_mode=None)
    visible = configuration.home.visible_applications
    if visible:
        visible = (*visible, application.identifier)
    return replace(
        configuration,
        applications=(*configuration.applications, application),
        home=replace(configuration.home, visible_applications=visible),
    )


def unregister_application(configuration: Configuration, identifier: str) -> Configuration:
    """Remove an uninstalled application and its home-screen entry.

    An empty visibility list means "show all", so removing the last visible
    application leaves the remaining ones visible rather than hiding them all.
    """
    applications = tuple(item for item in configuration.applications if item.identifier != identifier)
    if len(applications) == len(configuration.applications):
        return configuration
    visible = tuple(item for item in configuration.home.visible_applications if item != identifier)
    return replace(
        configuration,
        applications=applications,
        home=replace(configuration.home, visible_applications=visible),
    )
