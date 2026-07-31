"""Composition root and Milestone 1 validation entry point."""

import argparse
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from pideck.application.dependencies import ApplicationDependencies
from pideck.application.session import ApplicationSessionService, LaunchStatus, SessionObserver
from pideck.application.settings import SettingsService, SettingsUpdate
from pideck.domain.configuration import ApplicationDefinition, ApplicationProfile
from pideck.domain.errors import ConfigurationError
from pideck.infrastructure.config import FileConfigurationRepository
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.logging import configure_logging
from pideck.infrastructure.process import SubprocessSupervisor
from pideck.infrastructure.theme import FileThemeRepository

if TYPE_CHECKING:
    from pideck.presentation.qt.launcher_window import LauncherWindow
    from pideck.presentation.qt.settings_window import SettingsWindow

_LOGGER = logging.getLogger(__name__)


class _QtSessionObserver:
    """Bridge worker-thread session callbacks into Qt signals."""

    def __init__(self, window: "LauncherWindow") -> None:
        """Create an observer for one launcher window."""
        self._window = window

    def started(self, application: ApplicationDefinition, profile: ApplicationProfile | None) -> None:
        """Forward a successful process start to the window."""
        self._window.notify_session_started(application, profile)

    def finished(self, application: ApplicationDefinition, return_code: int) -> None:
        """Forward process exit and return-to-launcher behavior to the window."""
        self._window.notify_session_finished(application, return_code)

    def failed(self, application: ApplicationDefinition, message: str) -> None:
        """Forward launch failure feedback to the window."""
        self._window.notify_session_failed(application, message)


def default_configuration_path() -> Path:
    """Return the platform-appropriate writable configuration path."""
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_root / "pideck" / "pideck.yaml"


def build_dependencies(
    configuration_path: Path,
    asset_root: Path | None = None,
    log_file: Path | None = None,
    log_level: str = "INFO",
) -> ApplicationDependencies:
    """Build the typed runtime graph without creating a Qt application."""
    logger = configure_logging(level=log_level, log_file=log_file)
    configuration_repository = FileConfigurationRepository(configuration_path)
    configuration = configuration_repository.load()
    theme_repository = FileThemeRepository(
        themes=configuration.themes,
        asset_root=asset_root or configuration_path.parent,
        fallback=DEFAULT_THEME,
    )
    process_supervisor = SubprocessSupervisor()
    logger.info(
        "PiDeck foundation initialized applications=%d themes=%d configuration=%s",
        len(configuration.applications),
        len(configuration.themes),
        configuration_path,
    )
    return ApplicationDependencies(
        configuration=configuration,
        configuration_repository=configuration_repository,
        theme_repository=theme_repository,
        process_supervisor=process_supervisor,
        logger=logger,
    )


def build_launcher_window(
    dependencies: ApplicationDependencies,
    asset_root: Path,
) -> "LauncherWindow":
    """Create the Qt launcher from injected configuration and theme adapters."""
    from pideck.application.launcher import LauncherController
    from pideck.presentation.qt.launcher_window import LauncherWindow

    controller = LauncherController(dependencies.configuration)
    theme = dependencies.theme_repository.get(dependencies.configuration.home.theme)
    return LauncherWindow(
        controller,
        theme,
        asset_root,
        dependencies.configuration.settings.reduced_motion,
    )


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate configuration and initialize Milestone 1 dependencies."""
    parser = argparse.ArgumentParser(description="PiDeck foundation bootstrap")
    parser.add_argument("--config", type=Path, default=default_configuration_path())
    parser.add_argument("--asset-root", type=Path, default=None)
    parser.add_argument("--log-file", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Load and validate configuration without starting the Qt launcher",
    )
    parsed_arguments = parser.parse_args(arguments)
    dependencies = build_dependencies(
        configuration_path=parsed_arguments.config,
        asset_root=parsed_arguments.asset_root,
        log_file=parsed_arguments.log_file,
        log_level=parsed_arguments.log_level,
    )
    if parsed_arguments.validate_only:
        return 0

    from PySide6.QtWidgets import QApplication

    application = QApplication(["pideck"])
    application.setApplicationName("PiDeck")
    asset_root = parsed_arguments.asset_root or parsed_arguments.config.parent
    window = build_launcher_window(dependencies, asset_root)
    current_configuration = dependencies.configuration
    settings_service = SettingsService()
    settings_window: SettingsWindow | None = None
    session_observer: SessionObserver = _QtSessionObserver(window)
    session_service = ApplicationSessionService(dependencies.process_supervisor, session_observer)

    def handle_application_request(application_definition: ApplicationDefinition) -> None:
        """Resolve profile and replacement decisions for a tile activation."""
        result = session_service.request_launch(application_definition)
        if result.status is LaunchStatus.PROFILE_SELECTION_REQUIRED:
            selected_profile = window.choose_profile(result.profiles)
            if selected_profile is None:
                return
            result = session_service.request_launch(application_definition, selected_profile)
        if result.status is LaunchStatus.CONFIRMATION_REQUIRED:
            if not window.confirm_application_replacement(result.application):
                return
            selected_profile = (
                window.choose_profile(application_definition.profiles)
                if len(application_definition.profiles) > 1
                else None
            )
            if application_definition.profiles and selected_profile is None:
                return
            result = session_service.replace_running(application_definition, selected_profile)
        if result.status is LaunchStatus.FAILED:
            dependencies.logger.error(
                "Application launch failed application=%s error=%s",
                application_definition.identifier,
                result.message,
            )
        else:
            dependencies.logger.info(
                "Application launch requested application=%s status=%s",
                application_definition.identifier,
                result.status,
            )

    def handle_shutdown_request() -> None:
        """Stop any application session before ending the Qt event loop."""
        session_service.close()
        application.quit()

    def handle_settings_changed(update: SettingsUpdate) -> None:
        """Persist each valid settings change and refresh both views."""
        nonlocal current_configuration
        if settings_window is None:
            return
        try:
            current_configuration = settings_service.save(
                dependencies.configuration_repository,
                current_configuration,
                update,
            )
            theme = dependencies.theme_repository.get(current_configuration.home.theme)
        except ConfigurationError as error:
            settings_window.show_error(str(error))
            return
        window.apply_configuration(current_configuration, theme)
        settings_window.apply_theme_definition(theme)
        dependencies.logger.info("Settings changed theme=%s", current_configuration.home.theme)

    def handle_settings_request() -> None:
        """Open the modal settings surface and suspend the launcher behind it."""
        nonlocal settings_window
        from pideck.presentation.qt.settings_window import SettingsWindow

        if settings_window is not None:
            settings_window.close()
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        settings_window = SettingsWindow(current_configuration, theme, asset_root, window)
        settings_window.settings_changed.connect(handle_settings_changed)
        settings_window.finished.connect(lambda _: window.show_launcher())
        window.hide()
        settings_window.show_fullscreen()

    window.application_requested.connect(handle_application_request)
    window.settings_requested.connect(handle_settings_request)
    window.shutdown_requested.connect(handle_shutdown_request)
    window.show_launcher()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
