"""Tests for home-screen order helpers."""

from dataclasses import replace

import pytest

from pideck.application.home_layout import (
    application_order,
    is_visible,
    normalize_home,
    set_application_order,
)
from pideck.application.settings import SettingsService, SettingsUpdate
from pideck.domain.configuration import ApplicationDefinition
from pideck.domain.errors import ConfigurationValidationError
from pideck.infrastructure.config.parser import YamlConfigurationParser
from tests.test_configuration import configuration_document


def configuration(visible: tuple[str, ...]):
    """Build a configuration with apps a..d in that order."""
    base = YamlConfigurationParser().parse(configuration_document())
    apps = tuple(ApplicationDefinition(name, name.upper(), name) for name in "abcd")
    return replace(base, applications=apps, home=replace(base.home, visible_applications=visible))


def test_order_follows_tiles_then_remaining_applications() -> None:
    """A non-empty visibility list sets tile order; remaining apps follow."""
    assert application_order(configuration(())) == ("a", "b", "c", "d")
    assert application_order(configuration(("c", "a"))) == ("c", "a", "b", "d")
    assert application_order(configuration(("d",))) == ("d", "a", "b", "c")


def test_configured_applications_are_always_visible() -> None:
    """Every configured application is shown; unknown ids are not."""
    config = configuration(("a", "c"))
    assert all(is_visible(config, identifier) for identifier in "abcd")
    assert not is_visible(config, "x")


def test_normalize_home_reveals_every_configured_application() -> None:
    """Partial visibility lists are expanded to the show-all sentinel."""
    partial = configuration(("c", "a"))
    normalized = normalize_home(partial)
    assert [item.identifier for item in normalized.applications] == ["c", "a", "b", "d"]
    assert normalized.home.visible_applications == ()
    assert normalize_home(normalized) is normalized


def test_order_reorders_saved_applications() -> None:
    """A new order rewrites applications and clears the visibility filter."""
    moved = set_application_order(configuration(("a", "c", "d")), ("a", "d", "b", "c"))
    assert moved.home.visible_applications == ()
    assert [item.identifier for item in moved.applications] == ["a", "d", "b", "c"]
    assert application_order(moved) == ("a", "d", "b", "c")
    unchanged = configuration(())
    assert set_application_order(unchanged, ("a", "b", "c", "d")) is unchanged
    for invalid in (("a", "b", "c"), ("a", "b", "c", "c"), ("a", "b", "c", "x")):
        with pytest.raises(ConfigurationValidationError, match="every configured application"):
            set_application_order(unchanged, invalid)


def test_settings_without_applications_can_still_be_saved() -> None:
    """After removing every application, appearance changes remain possible."""
    empty = replace(configuration(()), applications=())
    updated = SettingsService().apply(empty, SettingsUpdate("default", ()))
    assert updated.home.theme == "default"


def test_application_items_merge_configuration_and_catalog() -> None:
    """Configured apps keep tile order; catalog apps join them only when not configured."""
    from pathlib import Path

    from pideck.application.app_management import CatalogItemStatus
    from pideck.application.application_items import ApplicationAction, application_items, available_actions
    from pideck.domain.catalog import InstallState
    from pideck.infrastructure.config.catalog import load_catalog

    entries = {item.identifier: item for item in load_catalog(Path("assets/catalog/applications.yaml"))}
    config = configuration(("b", "a"))
    custom_red = replace(entries["youtube"].application, identifier="a")
    config = replace(config, applications=(custom_red, *config.applications[1:]))
    statuses = (
        CatalogItemStatus(entries["youtube"], InstallState.INSTALLED, removable=True),
        CatalogItemStatus(entries["kodi"], InstallState.NOT_INSTALLED),
    )
    items = application_items(config, statuses)
    assert [item.key for item in items] == ["b", "a", "c", "d", "kodi"]
    red = items[1]
    assert red.catalog_id == "youtube" and red.visible and red.position == 1
    assert available_actions(red, count=4) == (
        ApplicationAction.MOVE, ApplicationAction.REMOVE,
    )
    assert available_actions(items[0], count=4) == (ApplicationAction.MOVE,)
    assert ApplicationAction.MOVE not in available_actions(items[0], count=1)
    assert available_actions(red, count=4, busy=True, update_available=True)[0] is ApplicationAction.UPDATE
    assert ApplicationAction.REMOVE not in available_actions(red, count=4, busy=True)
    assert available_actions(items[4], count=4) == (ApplicationAction.INSTALL,)
    assert available_actions(items[4], count=4, installing=True) == (ApplicationAction.CANCEL_INSTALL,)
    assert available_actions(items[2], count=4, updating=True)[0] is ApplicationAction.CANCEL_UPDATE
    assert items[2].visible
