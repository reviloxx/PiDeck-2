"""Tests for framework-free launcher selection and grid navigation."""

from pideck.application.launcher import LauncherController, NavigationDirection
from pideck.infrastructure.config.parser import YamlConfigurationParser
from tests.test_configuration import configuration_document


def test_empty_visibility_shows_all_configured_applications() -> None:
    """An empty visibility list means all configured applications are shown."""
    document = configuration_document()
    document["applications"].append(
        {
            "id": "kodi",
            "name": "Kodi",
            "executable": "kodi",
            "preferred_input": "cec",
        }
    )
    document["home"]["visible_applications"] = []
    configuration = YamlConfigurationParser().parse(document)

    controller = LauncherController(configuration)

    assert [app.identifier for app in controller.state.applications] == ["browser", "kodi"]


def test_grid_navigation_clamps_at_edges() -> None:
    """Arrow navigation moves by grid coordinates without wrapping unexpectedly."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two", "three", "four", "five")
    ]
    document["home"]["visible_applications"] = []
    configuration = YamlConfigurationParser().parse(document)
    controller = LauncherController(configuration)
    controller.set_columns(2)

    controller.move(NavigationDirection.RIGHT)
    controller.move(NavigationDirection.DOWN)
    controller.move(NavigationDirection.DOWN)
    controller.move(NavigationDirection.RIGHT)

    assert controller.state.focused_index == 4
    assert controller.activate().identifier == "five"


def test_vertical_navigation_preserves_column_when_target_exists() -> None:
    """Moving vertically keeps the current column when the next row has it."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two", "three", "four")
    ]
    document["home"]["visible_applications"] = []
    controller = LauncherController(YamlConfigurationParser().parse(document))
    controller.set_columns(2)

    controller.move(NavigationDirection.RIGHT)
    controller.move(NavigationDirection.DOWN)
    controller.move(NavigationDirection.UP)

    assert controller.state.focused_index == 1
