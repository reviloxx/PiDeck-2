"""Tests for typed settings updates and YAML persistence."""

from pathlib import Path

from pideck.application.settings import SettingsService, SettingsUpdate
from pideck.domain.errors import ConfigurationValidationError
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.config.repository import FileConfigurationRepository
from tests.test_configuration import configuration_document


def test_settings_service_applies_and_persists_updates(tmp_path: Path) -> None:
    """Settings changes update the immutable model and survive a reload."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    repository = FileConfigurationRepository(tmp_path / "pideck.yaml")
    service = SettingsService()
    update = SettingsUpdate(
        theme="default",
        reduced_motion=True,
        visible_applications=("browser",),
    )

    updated = service.save(repository, configuration, update)
    restored = repository.load()

    assert updated.settings.reduced_motion is True
    assert updated.home.visible_applications == ("browser",)
    assert restored == updated


def test_settings_service_rejects_unknown_application() -> None:
    """Settings cannot persist an application absent from the configuration."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    service = SettingsService()

    try:
        service.apply(
            configuration,
            SettingsUpdate("default", False, ("missing",)),
        )
    except ConfigurationValidationError as error:
        assert "unknown applications" in str(error)
    else:
        raise AssertionError("Unknown application should be rejected")
