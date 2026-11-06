"""Tests for home-screen order and visibility helpers."""

from dataclasses import replace

import pytest

from pideck.application.home_layout import application_order, is_visible, move_application, set_visibility
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


def test_order_follows_tiles_and_keeps_hidden_in_place() -> None:
    """Tile order wins; hidden apps stay behind their predecessor."""
    assert application_order(configuration(())) == ("a", "b", "c", "d")
    assert application_order(configuration(("c", "a"))) == ("c", "d", "a", "b")
    assert application_order(configuration(("d",))) == ("a", "b", "c", "d")


def test_visibility_round_trip_restores_position() -> None:
    """Hiding and showing keeps the tile position and the all-visible sentinel is resolved."""
    config = configuration(())
    hidden = set_visibility(config, "b", False)
    assert hidden.home.visible_applications == ("a", "c", "d")
    assert not is_visible(hidden, "b") and is_visible(config, "b")
    shown = set_visibility(hidden, "b", True)
    assert shown.home.visible_applications == ("a", "b", "c", "d")
    with pytest.raises(ConfigurationValidationError, match="At least one"):
        set_visibility(configuration(("a",)), "a", False)
    with pytest.raises(ConfigurationValidationError, match="Unknown"):
        set_visibility(config, "x", True)


def test_move_reorders_tiles_and_saved_applications() -> None:
    """Moves change both lists consistently and stop at the ends."""
    moved = move_application(configuration(("a", "c", "d")), "d", -2)
    assert moved.home.visible_applications == ("a", "d", "c")
    assert [item.identifier for item in moved.applications] == ["a", "d", "b", "c"]
    assert application_order(moved) == ("a", "d", "b", "c")
    edge = configuration(())
    assert move_application(edge, "a", -1) is edge


def test_settings_without_applications_can_still_be_saved() -> None:
    """After removing every application, appearance changes remain possible."""
    empty = replace(configuration(()), applications=())
    updated = SettingsService().apply(empty, SettingsUpdate("default", True, ()))
    assert updated.settings.reduced_motion


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
    assert [item.key for item in items] == ["b", "c", "d", "a", "kodi"]
    red = items[3]
    assert red.catalog_id == "youtube" and red.visible and red.position == 3
    assert available_actions(red, count=4) == (
        ApplicationAction.HIDE, ApplicationAction.MOVE_UP, ApplicationAction.REMOVE,
    )
    assert available_actions(items[1], count=4)[1:] == (ApplicationAction.MOVE_UP, ApplicationAction.MOVE_DOWN)
    assert available_actions(red, count=4, busy=True, update_available=True)[0] is ApplicationAction.UPDATE
    assert ApplicationAction.REMOVE not in available_actions(red, count=4, busy=True)
    assert available_actions(items[4], count=4) == (ApplicationAction.INSTALL,)
    assert available_actions(items[4], count=4, installing=True) == (ApplicationAction.CANCEL_INSTALL,)
    assert available_actions(items[2], count=4, updating=True)[0] is ApplicationAction.CANCEL_UPDATE
    assert not items[1].visible and available_actions(items[1], count=4)[0] is ApplicationAction.SHOW
