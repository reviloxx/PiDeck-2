"""Tests for typed settings updates and YAML persistence."""

from pathlib import Path

from pideck.application.settings import SettingsService, SettingsUpdate
from pideck.domain.errors import ConfigurationValidationError
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.config.repository import FileConfigurationRepository
from tests.test_configuration import configuration_document


def test_settings_service_applies_and_persists_updates(tmp_path: Path) -> None:
    """Settings changes update the immutable model and survive a reload."""
    document = configuration_document()
    document["settings"]["update_check_interval_hours"] = 12
    configuration = YamlConfigurationParser().parse(document)
    repository = FileConfigurationRepository(tmp_path / "pideck.yaml")
    service = SettingsService()
    update = SettingsUpdate(
        theme="default",
        visible_applications=("browser",),
        background="horizon",
    )

    updated = service.save(repository, configuration, update)
    restored = repository.load()

    assert updated.settings.language == "en"
    assert updated.settings.update_check_interval_hours == 12
    assert updated.home.visible_applications == ("browser",)
    assert updated.home.background == "horizon"
    assert updated.home.snowflakes == "automatic"
    assert restored == updated


def test_settings_service_persists_snowflakes_mode(tmp_path: Path) -> None:
    """Snowflakes mode is stored under home and survives a reload."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    repository = FileConfigurationRepository(tmp_path / "pideck.yaml")
    service = SettingsService()
    update = SettingsUpdate(
        theme="default",
        visible_applications=("browser",),
        snowflakes="on",
    )

    updated = service.save(repository, configuration, update)
    restored = repository.load()

    assert updated.home.snowflakes == "on"
    assert restored.home.snowflakes == "on"


def test_settings_service_rejects_unknown_application() -> None:
    """Settings cannot persist an application absent from the configuration."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    service = SettingsService()

    try:
        service.apply(
            configuration,
            SettingsUpdate("default", ("missing",)),
        )
    except ConfigurationValidationError as error:
        assert "unknown applications" in str(error)
    else:
        raise AssertionError("Unknown application should be rejected")


def test_settings_service_rejects_unknown_background() -> None:
    """Settings cannot persist a background style absent from the catalog."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    service = SettingsService()

    try:
        service.apply(
            configuration,
            SettingsUpdate("default", ("browser",), background="nebula"),
        )
    except ConfigurationValidationError as error:
        assert "unknown background" in str(error)
    else:
        raise AssertionError("Unknown background should be rejected")


def test_settings_service_rejects_unknown_snowflakes() -> None:
    """Settings cannot persist an unknown snowflakes mode."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    service = SettingsService()

    try:
        service.apply(
            configuration,
            SettingsUpdate("default", ("browser",), snowflakes="blizzard"),
        )
    except ConfigurationValidationError as error:
        assert "unknown snowflakes" in str(error)
    else:
        raise AssertionError("Unknown snowflakes mode should be rejected")
