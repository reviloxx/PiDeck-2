"""Settings editing use case and immutable configuration updates."""

from dataclasses import dataclass, replace

from pideck.application.ports.configuration import ConfigurationRepository
from pideck.domain.backgrounds import BACKGROUND_STYLE_ID_SET, DEFAULT_BACKGROUND
from pideck.domain.configuration import Configuration
from pideck.domain.errors import ConfigurationValidationError
from pideck.domain.snowflakes import DEFAULT_SNOWFLAKES, SNOWFLAKES_MODE_SET


@dataclass(frozen=True, slots=True)
class SettingsUpdate:
    """Represent editable launcher settings from the settings presentation."""

    theme: str
    visible_applications: tuple[str, ...]
    language: str = "en"
    background: str = DEFAULT_BACKGROUND
    snowflakes: str = DEFAULT_SNOWFLAKES

    def __post_init__(self) -> None:
        """Normalize the selected application identifiers."""
        object.__setattr__(self, "visible_applications", tuple(self.visible_applications))


class SettingsService:
    """Validate, apply, and persist settings without depending on Qt."""

    def apply(self, configuration: Configuration, update: SettingsUpdate) -> Configuration:
        """Return a validated configuration containing the requested settings."""
        application_ids = {application.identifier for application in configuration.applications}
        unknown_applications = set(update.visible_applications).difference(application_ids)
        if unknown_applications:
            unknown = ", ".join(sorted(unknown_applications))
            raise ConfigurationValidationError(
                f"Settings reference unknown applications: {unknown}"
            )
        if not update.theme.strip():
            raise ConfigurationValidationError("Settings theme must not be empty")
        theme_ids = {theme.identifier for theme in configuration.themes}
        if update.theme not in theme_ids:
            raise ConfigurationValidationError(f"Settings reference unknown theme: {update.theme!r}")
        if not update.background.strip():
            raise ConfigurationValidationError("Settings background must not be empty")
        if update.background not in BACKGROUND_STYLE_ID_SET:
            raise ConfigurationValidationError(
                f"Settings reference unknown background: {update.background!r}"
            )
        if not update.snowflakes.strip():
            raise ConfigurationValidationError("Settings snowflakes mode must not be empty")
        if update.snowflakes not in SNOWFLAKES_MODE_SET:
            raise ConfigurationValidationError(
                f"Settings reference unknown snowflakes mode: {update.snowflakes!r}"
            )
        return replace(
            configuration,
            home=replace(
                configuration.home,
                visible_applications=update.visible_applications,
                theme=update.theme,
                background=update.background,
                snowflakes=update.snowflakes,
            ),
            settings=replace(
                configuration.settings,
                language=update.language,
            ),
        )

    def save(
        self,
        repository: ConfigurationRepository,
        configuration: Configuration,
        update: SettingsUpdate,
    ) -> Configuration:
        """Apply settings and atomically persist the resulting configuration."""
        updated_configuration = self.apply(configuration, update)
        repository.save(updated_configuration)
        return updated_configuration
