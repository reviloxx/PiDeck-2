"""Tests for typed YAML configuration loading and persistence."""

from pathlib import Path

import pytest
import yaml

from pideck.domain.errors import ConfigurationValidationError
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.config.repository import FileConfigurationRepository


def configuration_document() -> dict:
    """Return a representative valid YAML document."""
    return {
        "version": 1,
        "applications": [
            {
                "id": "browser",
                "name": "Browser",
                "executable": "chromium",
                "arguments": ["--kiosk"],
                "profiles": [
                    {
                        "id": "guest",
                        "name": "Guest",
                        "arguments": ["--incognito"],
                        "environment": {"PIDECK_PROFILE": "guest"},
                        "working_directory": None,
                    }
                ],
                "icon": None,
                "preferred_input": "mouse",
            }
        ],
        "themes": [
            {
                "id": "default",
                "name": "Midnight",
                "colors": {
                    "background": "#101214",
                    "surface": "#1b1f24",
                    "primary": "#63d6c5",
                    "text": "#f4f7f7",
                    "muted_text": "#9ca8aa",
                    "focus": "#63d6c5",
                    "running": "#f2b880",
                    "error": "#ed7777",
                },
                "fonts": {"family": "DejaVu Sans", "heading_size": 28, "body_size": 18},
                "icons": {},
                "wallpaper": None,
                "tile": {"width": 320, "height": 180, "gap": 24},
                "animation": {"duration_ms": 180, "reduced_motion_duration_ms": 0},
            }
        ],
        "home": {"visible_applications": ["browser"], "theme": "default"},
        "input": {
            "enabled_sources": ["keyboard", "mouse"],
            "bindings": {"home": "Ctrl+Alt+H"},
        },
        "settings": {"reduced_motion": False},
    }


def test_parser_builds_immutable_domain_models(tmp_path: Path) -> None:
    """A valid YAML file becomes typed application and theme models."""
    path = tmp_path / "pideck.yaml"
    path.write_text(yaml.safe_dump(configuration_document()), encoding="utf-8")

    configuration = YamlConfigurationParser().load(path)

    assert configuration.applications[0].profiles[0].environment["PIDECK_PROFILE"] == "guest"
    assert configuration.home.visible_applications == ("browser",)
    with pytest.raises(TypeError):
        configuration.input.bindings["new"] = "value"


def test_parser_rejects_unknown_keys() -> None:
    """Typos in configuration keys fail validation rather than being ignored."""
    document = configuration_document()
    document["unexpected"] = True

    with pytest.raises(ConfigurationValidationError, match="unknown keys"):
        YamlConfigurationParser().parse(document)


def test_parser_applies_defaults_to_optional_application_fields() -> None:
    """Application profiles, arguments, icons, and settings may be omitted."""
    document = configuration_document()
    document["applications"][0] = {
        "id": "browser",
        "name": "Browser",
        "executable": "chromium",
    }
    document["settings"] = {}

    configuration = YamlConfigurationParser().parse(document)

    application = configuration.applications[0]
    assert application.arguments == ()
    assert application.profiles == ()
    assert application.preferred_input.value == "keyboard"
    assert configuration.settings.reduced_motion is False


def test_repository_recovers_from_invalid_configuration(tmp_path: Path) -> None:
    """A malformed configuration returns the safe default model."""
    path = tmp_path / "pideck.yaml"
    path.write_text("version: 1\nunknown: true\n", encoding="utf-8")

    configuration = FileConfigurationRepository(path).load()

    assert configuration.applications == ()
    assert configuration.home.theme == "default"


def test_parser_rejects_invalid_theme_domain_values() -> None:
    """Invalid theme dimensions are reported as configuration errors."""
    document = configuration_document()
    document["themes"][0]["tile"]["width"] = 0

    with pytest.raises(ConfigurationValidationError, match="Tile width"):
        YamlConfigurationParser().parse(document)


def test_repository_saves_and_loads_atomically(tmp_path: Path) -> None:
    """A saved typed model can be loaded again without data loss."""
    path = tmp_path / "nested" / "pideck.yaml"
    repository = FileConfigurationRepository(path)
    source = YamlConfigurationParser().parse(configuration_document())

    repository.save(source)
    restored = repository.load()

    assert restored == source
    assert not list(path.parent.glob("*.tmp"))


def test_repository_recovers_last_known_good_backup(tmp_path: Path) -> None:
    """An invalid current file does not discard the last valid saved model."""
    path = tmp_path / "pideck.yaml"
    repository = FileConfigurationRepository(path)
    source = YamlConfigurationParser().parse(configuration_document())
    repository.save(source)
    replacement = YamlConfigurationParser().parse(
        {**configuration_document(), "home": {"visible_applications": [], "theme": "default"}}
    )
    repository.save(replacement)
    path.write_text("not: a valid configuration", encoding="utf-8")

    restored = repository.load()

    assert restored == source
