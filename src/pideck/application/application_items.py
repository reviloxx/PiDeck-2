"""Combine configured and catalog applications into one managed list, without Qt.

Each entry is one row of Settings > Applications. The available actions are
derived here so that the rules are testable without a user interface.
"""

from dataclasses import dataclass
from enum import StrEnum

from pideck.application.app_management import CatalogItemStatus
from pideck.application.catalog import configured_application
from pideck.application.home_layout import application_order, is_visible
from pideck.domain.catalog import InstallState
from pideck.domain.configuration import ApplicationDefinition, Configuration


class ApplicationAction(StrEnum):
    """Actions offered in an application's action menu."""

    CANCEL_INSTALL = "cancel_install"
    UPDATE = "update"
    CANCEL_UPDATE = "cancel_update"
    SHOW = "show"
    HIDE = "hide"
    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    INSTALL = "install"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class ApplicationItem:
    """One supported or configured application and its home-screen state.

    ``key`` is the configured identifier when the application is on the home
    screen list, otherwise the catalog identifier.
    """

    key: str
    name: str
    application: ApplicationDefinition | None = None
    catalog: CatalogItemStatus | None = None
    visible: bool = False
    position: int | None = None
    """Index in the home-screen order, or ``None`` when not configured."""

    @property
    def catalog_id(self) -> str | None:
        """Return the catalog identifier used for install and removal."""
        return self.catalog.identifier if self.catalog is not None else None

    @property
    def configured(self) -> bool:
        """Return whether the application is part of the saved configuration."""
        return self.application is not None


def application_items(
    configuration: Configuration, statuses: tuple[CatalogItemStatus, ...],
) -> tuple[ApplicationItem, ...]:
    """Return configured applications in tile order, then the remaining catalog."""
    by_id = {item.identifier: item for item in configuration.applications}
    catalog_by_app: dict[str, CatalogItemStatus] = {}
    unconfigured: list[CatalogItemStatus] = []
    for status in statuses:
        configured = configured_application(configuration, status.entry)
        if configured is None:
            unconfigured.append(status)
        else:
            catalog_by_app.setdefault(configured.identifier, status)
    items = [
        ApplicationItem(
            identifier, by_id[identifier].name, by_id[identifier], catalog_by_app.get(identifier),
            is_visible(configuration, identifier), position,
        )
        for position, identifier in enumerate(application_order(configuration))
    ]
    items.extend(ApplicationItem(status.identifier, status.entry.name, catalog=status) for status in unconfigured)
    return tuple(items)


def available_actions(
    item: ApplicationItem, *, count: int, update_available: bool = False, updating: bool = False,
    installing: bool = False, busy: bool = False,
) -> tuple[ApplicationAction, ...]:
    """Return the actions offered for one row.

    ``count`` is the number of configured applications; ``busy`` means any
    install or removal is running, which blocks starting another one.
    """
    if installing:
        return (ApplicationAction.CANCEL_INSTALL,)
    actions: list[ApplicationAction] = []
    if updating:
        actions.append(ApplicationAction.CANCEL_UPDATE)
    elif update_available:
        actions.append(ApplicationAction.UPDATE)
    state = item.catalog.state if item.catalog is not None else None
    if item.configured:
        actions.append(ApplicationAction.HIDE if item.visible else ApplicationAction.SHOW)
        if item.position is not None and item.position > 0:
            actions.append(ApplicationAction.MOVE_UP)
        if item.position is not None and item.position < count - 1:
            actions.append(ApplicationAction.MOVE_DOWN)
    elif state is InstallState.INSTALLED:
        actions.append(ApplicationAction.SHOW)
    if not busy and state is InstallState.NOT_INSTALLED:
        actions.append(ApplicationAction.INSTALL)
    if not busy and state is InstallState.INSTALLED and item.catalog is not None and item.catalog.removable:
        actions.append(ApplicationAction.REMOVE)
    return tuple(actions)
