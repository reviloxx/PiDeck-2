"""Tests for theme asset resolution and fallback."""

from pathlib import Path

from pideck.bootstrap import build_dependencies
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
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
