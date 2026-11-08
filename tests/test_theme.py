"""Tests for theme asset resolution and fallback."""

from dataclasses import replace
from pathlib import Path

import yaml

from pideck.bootstrap import build_dependencies
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.config.themes import apply_shipped_themes, merge_themes
from pideck.infrastructure.theme.loader import FileThemeRepository

from tests.test_configuration import configuration_document


def test_theme_repository_resolves_existing_assets(tmp_path: Path) -> None:
    """Relative icon and wallpaper paths resolve below the asset root."""
    icon_path = tmp_path / "icon.svg"
    wallpaper_path = tmp_path / "wallpaper.jpg"
    icon_path.write_text("icon", encoding="utf-8")
    wallpaper_path.write_text("wallpaper", encoding="utf-8")
    document = configuration_document()
    theme_data = document["themes"][0]
    theme_data["icons"] = {"browser": "icon.svg"}
    theme_data["wallpaper"] = "wallpaper.jpg"
    configuration = YamlConfigurationParser().parse(document)
    repository = FileThemeRepository(configuration.themes, tmp_path, DEFAULT_THEME)

    theme = repository.get("default")

    assert theme.icons["browser"] == icon_path
    assert theme.wallpaper == wallpaper_path


def test_theme_repository_uses_fallback_for_missing_assets(tmp_path: Path) -> None:
    """Missing custom assets do not prevent a usable theme from loading."""
    document = configuration_document()
    document["themes"][0]["icons"] = {"browser": "missing.svg"}
    configuration = YamlConfigurationParser().parse(document)
    repository = FileThemeRepository(configuration.themes, tmp_path, DEFAULT_THEME)

    theme = repository.get("default")

    assert theme == DEFAULT_THEME


def test_sample_configuration_resolves_bundled_theme_assets() -> None:
    """The checked-in theme and application assets resolve from the repository root."""
    dependencies = build_dependencies(Path("config/pideck.yaml"), log_level="WARNING")
    theme = dependencies.theme_repository.get("default")

    assert theme.wallpaper is not None
    assert theme.wallpaper.is_file()
    assert all(
        (Path("config") / application.icon).resolve().is_file()
        for application in dependencies.configuration.applications
        if application.icon is not None
    )
    dependencies.process_supervisor.close()


def test_sample_configuration_loads_all_shipped_themes() -> None:
    """Midnight plus Ember, Nordic, Aurora, and Daylight resolve with wallpapers."""
    dependencies = build_dependencies(Path("config/pideck.yaml"), log_level="WARNING")
    expected = ("default", "ember", "nordic", "aurora", "daylight")
    assert tuple(theme.identifier for theme in dependencies.configuration.themes) == expected
    for identifier in expected:
        theme = dependencies.theme_repository.get(identifier)
        assert theme.wallpaper is not None and theme.wallpaper.is_file()
        assert all(icon.is_file() for icon in theme.icons.values())
    dependencies.process_supervisor.close()


def test_shipped_themes_fill_older_user_configuration(tmp_path: Path) -> None:
    """Existing user configs that only list Midnight still get the release themes."""
    document = configuration_document()
    path = tmp_path / "pideck.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    before = YamlConfigurationParser().load(path)
    assert [theme.identifier for theme in before.themes] == ["default"]

    dependencies = build_dependencies(path, asset_root=Path("config"), log_level="WARNING")
    assert tuple(theme.identifier for theme in dependencies.configuration.themes) == (
        "default", "ember", "nordic", "aurora", "daylight",
    )
    assert dependencies.theme_repository.get("ember").name == "Ember"
    dependencies.process_supervisor.close()


def test_merge_themes_keeps_custom_user_themes() -> None:
    """Unknown user themes remain after shipped definitions replace known ids."""
    custom = replace(DEFAULT_THEME, identifier="custom", name="Custom")
    shipped = (
        replace(DEFAULT_THEME, identifier="default", name="Midnight"),
        replace(DEFAULT_THEME, identifier="ember", name="Ember"),
    )
    merged = merge_themes((DEFAULT_THEME, custom), shipped)
    assert [theme.identifier for theme in merged] == ["default", "ember", "custom"]
    assert merged[0].name == "Midnight"
    assert merged[2] is custom


def test_apply_shipped_themes_is_noop_without_manifest(tmp_path: Path) -> None:
    """Missing release themes leave the saved configuration unchanged."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    assert apply_shipped_themes(configuration, tmp_path / "config") is configuration
