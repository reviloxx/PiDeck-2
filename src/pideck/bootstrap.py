"""Composition root and Milestone 1 validation entry point."""

import argparse
from dataclasses import replace
import logging
import os
from pathlib import Path
import shutil
import threading
import time
from typing import TYPE_CHECKING, Sequence

from pideck.application.dependencies import ApplicationDependencies
from pideck.application.power import shutdown
from pideck.application.session import (
    ApplicationSessionService,
    LaunchStatus,
    SessionObserver,
    SessionState,
)
from pideck.application.settings import SettingsService, SettingsUpdate
from pideck.application.catalog import effective_cec_mode, register_application
from pideck.application.input_forwarding import InputForwardingService
from pideck.application.updates import ApplicationUpdateService
from pideck.application.pideck_updates import PiDeckUpdateInfo, PiDeckUpdateService, PiDeckUpdateStatus
from pideck.application.ports.input import InputEvent
from pideck.application.ports.installation import InstallProgress
from pideck.domain.configuration import (
    ApplicationDefinition,
    ApplicationProfile,
    CecMode,
    Configuration,
    WindowMode,
)
from pideck.domain.errors import ConfigurationError, PowerError, ProcessError
from pideck.domain.input_settings import CecSettings
from pideck.infrastructure.config import FileConfigurationRepository
from pideck.infrastructure.build_info import installed_build_date
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.catalog import load_catalog
from pideck.infrastructure.flatpak_installer import (
    FlatpakInstaller,
    InstallationCancelled,
    InstallationError,
)
from pideck.infrastructure.config.window_modes import load_window_modes
from pideck.infrastructure.logging import configure_logging
from pideck.infrastructure.process import SubprocessSupervisor
from pideck.infrastructure.platform_updates import NativeUpdateGateway
from pideck.infrastructure.image_update_client import ImageUpdateError, request as image_update_request
from pideck.infrastructure.pideck_releases import find_latest_release, version_key
from pideck.infrastructure.input_gamepad import EvdevGamepadAdapter
from pideck.infrastructure.input_cec import CecClientAdapter
from pideck.infrastructure.input_settings import InputPreferences, InputSettingsError
from pideck.infrastructure.keyboard_layout import KeyboardPreferences, X11KeyboardLayout
from pideck.domain.keyboard_layout import KeyboardLayout
from pideck.infrastructure.network_manager import NetworkManagerGateway
from pideck.application.ports.network import NetworkError
from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.process.x11_visibility import X11WindowVisibilityDetector
from pideck.infrastructure.system_power import SystemPower
from pideck.infrastructure.system_audio import PulseAudioGateway
from pideck.infrastructure.x11_display import DisplayPreferences, X11DisplayGateway
from pideck.infrastructure.x11_key_injector import X11KeyInjector
from pideck.infrastructure.theme import FileThemeRepository

if TYPE_CHECKING:
    from pideck.presentation.qt.launcher_window import LauncherWindow
    from pideck.presentation.qt.settings_window import SettingsWindow
    from pideck.presentation.qt.network_window import NetworkWindow
    from pideck.presentation.qt.audio_window import AudioWindow
    from pideck.presentation.qt.display_window import DisplayWindow
    from pideck.presentation.qt.system_info_window import SystemInfoWindow
    from pideck.presentation.qt.bluetooth_window import BluetoothWindow
    from pideck.presentation.qt.drives_window import DrivesWindow

_LOGGER = logging.getLogger(__name__)


class _QtSessionObserver:
    """Bridge worker-thread session callbacks into Qt signals."""

    def __init__(self, window: "LauncherWindow") -> None:
        """Create an observer for one launcher window."""
        self._window = window

    def started(self, application: ApplicationDefinition, profile: ApplicationProfile | None) -> None:
        """Forward a successful process start to the window."""
        self._window.notify_session_started(application, profile)

    def visible(self, application: ApplicationDefinition) -> None:
        """Forward application-window readiness to the launcher."""
        self._window.notify_session_visible(application)

    def visibility_timeout(self, application: ApplicationDefinition) -> None:
        """Keep the launcher visible and report readiness timeout."""
        self._window.notify_visibility_timeout(application)

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

def _apply_window_modes(configuration: Configuration, window_modes: dict[str, WindowMode]) -> Configuration:
    """Apply configured window presentation without changing saved settings."""
    return replace(
        configuration,
        applications=tuple(
            replace(app, window_mode=window_modes.get(app.identifier))
            for app in configuration.applications
        ),
    )


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
    window_modes: dict[str, WindowMode] = {}
    policy_paths = []
    image_mode = os.environ.get("PIDECK_IMAGE") == "1"
    if image_mode:
        policy_paths.append(Path("/usr/share/pideck/config/window-modes.yaml"))
    policy_paths.append(configuration_path.with_name("window-modes.yaml"))
    for index, policy_path in enumerate(policy_paths):
        if not policy_path.exists() and (not image_mode or index > 0):
            continue
        try:
            window_modes.update(load_window_modes(policy_path))
        except ConfigurationError:
            logger.exception("Unable to load application window policy path=%s", policy_path)
    configuration = _apply_window_modes(configuration, window_modes)
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
        dependencies.configuration.settings.show_clock,
        dependencies.configuration.settings.language,
    )


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate configuration and initialize Milestone 1 dependencies."""
    parser = argparse.ArgumentParser(description="PiDeck foundation bootstrap")
    parser.add_argument("--config", type=Path, default=default_configuration_path())
    parser.add_argument("--asset-root", type=Path, default=None)
    parser.add_argument("--log-file", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--power-off-on-shutdown",
        action="store_true",
        help="Power off the host when the shutdown button is activated",
    )
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
    from PySide6.QtCore import QObject, QTimer, Qt, Signal

    from pideck.presentation.qt.cursor_visibility import CursorVisibility

    application = QApplication(["pideck"])
    application.setApplicationName("PiDeck")
    cursor_visibility = CursorVisibility(application)
    application.installEventFilter(cursor_visibility)
    asset_root = parsed_arguments.asset_root or parsed_arguments.config.parent
    window = build_launcher_window(dependencies, asset_root)
    current_configuration = dependencies.configuration
    settings_service = SettingsService()
    image_mode = os.environ.get("PIDECK_IMAGE") == "1"
    network_setup_enabled = os.environ.get("PIDECK_NETWORK") == "1"
    network_gateway = NetworkManagerGateway() if network_setup_enabled or shutil.which("nmcli") else None
    update_service = ApplicationUpdateService(
        NativeUpdateGateway(image_mode=image_mode), allow_password_prompt=not image_mode
    )
    keyboard_preferences = KeyboardPreferences(parsed_arguments.config.parent / "keyboard.yaml")
    keyboard_layout = keyboard_preferences.load()
    # Only the appliance session owns its X server; never remap a developer's desktop keyboard.
    keyboard_applier = X11KeyboardLayout() if image_mode else None
    if keyboard_applier is not None:
        keyboard_applier.apply(keyboard_layout)
    gamepad_adapter = EvdevGamepadAdapter()
    if keyboard_applier is not None:
        gamepad_adapter.keyboard_added = lambda: keyboard_applier.apply(keyboard_layout)
    input_preferences = InputPreferences(parsed_arguments.config.parent / "input.yaml")
    try:
        input_settings = input_preferences.load()
        input_settings_error: str | None = None
    except InputSettingsError as error:
        dependencies.logger.error("Unable to load HDMI-CEC preferences: %s", error)
        input_settings = CecSettings(enabled=False, auto_connect=False)
        input_settings_error = str(error)

    class CecStatusBridge(QObject):
        """Forward CEC worker status changes to the Qt thread."""

        status_received = Signal(str)

    cec_status_bridge = CecStatusBridge()
    cec_adapter = CecClientAdapter(
        settings=input_settings, status_changed=cec_status_bridge.status_received.emit,
    )
    power = SystemPower() if parsed_arguments.power_off_on_shutdown else None
    settings_window: SettingsWindow | None = None
    installer: FlatpakInstaller | None = None
    installing_id: str | None = None
    install_progress: InstallProgress | None = None
    try:
        catalog = load_catalog(asset_root.parent / "assets/catalog/applications.yaml")
        catalog_error: str | None = None
    except ConfigurationError as error:
        dependencies.logger.error("Unable to load application catalog: %s", error)
        catalog = ()
        catalog_error = str(error)
    network_window: NetworkWindow | None = None
    audio_window: AudioWindow | None = None
    drives_window: DrivesWindow | None = None
    system_info_window: SystemInfoWindow | None = None
    display_window: DisplayWindow | None = None
    bluetooth_window: BluetoothWindow | None = None
    bluetooth_opening = False

    def handle_cec_status(status: str) -> None:
        if settings_window is not None:
            settings_window.set_cec_status(status)

    cec_status_bridge.status_received.connect(handle_cec_status, Qt.ConnectionType.QueuedConnection)
    display_gateway = X11DisplayGateway(DisplayPreferences(parsed_arguments.config.parent / "display.yaml"))

    class PiDeckUpdateBridge(QObject):
        """Forward release worker results to the Qt thread."""

        status_received = Signal(object)

    update_bridge = PiDeckUpdateBridge()
    class InstallationBridge(QObject):
        """Forward installer progress and completion from its worker thread."""

        progress = Signal(str, object)
        cancelled = Signal(str)
        finished = Signal(str, object)

    installation_bridge = InstallationBridge()
    class PowerBridge(QObject):
        """Forward a completed host power request to the Qt thread."""

        finished = Signal(object)

    power_bridge = PowerBridge()
    power_pending = False
    release_version = os.environ.get("PIDECK_RELEASE_VERSION") if image_mode else None

    def inspect_image_update() -> dict[str, str]:
        result = image_update_request("status")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in result.items()):
            raise ImageUpdateError("Invalid image updater status")
        return result

    def prepare_image_update(version: str) -> None:
        image_update_request("stage", timeout=1800, version=version)

    def confirm_image_ready() -> None:
        if release_version is None:
            return
        for attempt in range(3):
            try:
                image_update_request("ready", timeout=5, version=release_version)
                return
            except ImageUpdateError:
                if attempt == 2:
                    raise
                time.sleep(2)

    pideck_update_service = PiDeckUpdateService(
        release_version,
        find_latest_release,
        version_key,
        update_bridge.status_received.emit,
        inspect=inspect_image_update,
        prepare=prepare_image_update,
        ready=confirm_image_ready,
    )

    def display_pideck_update(info: PiDeckUpdateInfo) -> None:
        if settings_window is not None:
            settings_window.set_pideck_update_info(info)

    update_bridge.status_received.connect(display_pideck_update, Qt.ConnectionType.QueuedConnection)
    application.aboutToQuit.connect(pideck_update_service.close)
    release_timer = QTimer(application)
    release_timer.setInterval(current_configuration.settings.update_check_interval_hours * 60 * 60 * 1000)
    release_timer.timeout.connect(pideck_update_service.check_async)
    release_timer.start()
    session_observer: SessionObserver = _QtSessionObserver(window)
    visibility_detector = X11WindowVisibilityDetector()
    session_service = ApplicationSessionService(
        dependencies.process_supervisor,
        session_observer,
        visibility_detector,
        cec_adapter,
        releases_input=lambda app: effective_cec_mode(app, catalog) is CecMode.RELEASE,
    )
    key_injector = X11KeyInjector()
    input_forwarding = InputForwardingService(
        key_injector, session_service.stop, lambda app: effective_cec_mode(app, catalog),
    )

    def forward_cec_to_application(event: InputEvent) -> bool:
        """Send CEC input to a running app that keeps PiDeck's adapter; report if consumed."""
        return input_forwarding.handle(
            event, session_service.snapshot,
            pideck_has_focus=window.isVisible() or QApplication.activeWindow() is not None,
        )

    class GamepadBridge(QObject):
        """Marshal input reader-thread events onto the Qt event loop."""

        event_received = Signal(object)

        def handle_input(self, event: InputEvent) -> None:
            """Emit one normalized event for queued Qt delivery."""
            self.event_received.emit(event)

    gamepad_bridge = GamepadBridge()

    def handle_gamepad_event(event: InputEvent) -> None:
        """Route normalized input to the active launcher/settings surface."""
        if event.pressed:
            cursor_visibility.hide_cursor()
        if bluetooth_window is not None and bluetooth_window.isVisible():
            bluetooth_window.handle_input(event)
        elif display_window is not None and display_window.isVisible():
            display_window.handle_input(event)
        elif audio_window is not None and audio_window.isVisible():
            audio_window.handle_input(event)
        elif drives_window is not None and drives_window.isVisible():
            drives_window.handle_input(event)
        elif system_info_window is not None and system_info_window.isVisible():
            system_info_window.handle_input(event)
        elif network_window is not None and network_window.isVisible():
            network_window.handle_input(event)
        elif settings_window is not None and settings_window.isVisible():
            settings_window.handle_input(event)
        elif window.has_open_dialog() or (
            session_service.snapshot.state is SessionState.IDLE and window.isVisible()
        ):
            window.handle_input(event)

    gamepad_bridge.event_received.connect(
        handle_gamepad_event,
        Qt.ConnectionType.QueuedConnection,
    )
    if "gamepad" in current_configuration.input.enabled_sources:
        gamepad_adapter.start(gamepad_bridge)
    if cec_adapter is not None:
        class CecBridge(QObject):
            """Reject CEC events queued before an application handoff."""

            event_received = Signal(object, int)

            def __init__(self, adapter: CecClientAdapter) -> None:
                super().__init__()
                self._adapter = adapter

            def handle_input(self, event: InputEvent) -> None:
                self.event_received.emit(event, self._adapter.generation)

        cec_bridge = CecBridge(cec_adapter)

        def handle_cec_event(event: InputEvent, generation: int) -> None:
            if cec_adapter is not None and cec_adapter.accepts(generation):
                if not forward_cec_to_application(event):
                    handle_gamepad_event(event)

        cec_bridge.event_received.connect(handle_cec_event, Qt.ConnectionType.QueuedConnection)
        cec_adapter.start(cec_bridge)

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

    services_closed = False

    def cleanup_services() -> None:
        """Stop active services once, before their Qt bridges are destroyed."""
        nonlocal services_closed
        if services_closed:
            return
        services_closed = True
        session_service.close()
        update_service.close()
        gamepad_adapter.close()
        key_injector.close()
        try:
            cec_adapter.close()
        except ProcessError:
            dependencies.logger.exception("Unable to stop CEC client during shutdown")

    application.aboutToQuit.connect(cleanup_services)

    def cleanup_session() -> None:
        """Stop active services before ending the Qt event loop."""
        cleanup_services()
        application.quit()

    def handle_shutdown_request() -> None:
        """Power off when configured, otherwise close the launcher."""
        try:
            shutdown(power, cleanup_session)
        except PowerError as error:
            dependencies.logger.error("System power-off failed: %s", error)
            window.show_power_error(str(error))

    def handle_system_power_request(action: str) -> None:
        """Run an explicitly confirmed host power action without blocking Qt."""
        nonlocal power_pending
        if power_pending or action not in {"restart", "shutdown"}:
            return
        power_pending = True

        def request() -> None:
            try:
                host_power = SystemPower()
                if action == "restart":
                    host_power.restart()
                else:
                    host_power.power_off()
            except PowerError as error:
                power_bridge.finished.emit(str(error))
                return
            except Exception:
                dependencies.logger.exception("Unexpected system power request failure")
                power_bridge.finished.emit("System power request failed")
                return
            power_bridge.finished.emit(None)

        threading.Thread(target=request, name="pideck-system-power", daemon=True).start()

    def finish_system_power(error: str | None) -> None:
        """Keep the session open on failure; clean up only after success."""
        nonlocal power_pending
        power_pending = False
        if error is not None:
            dependencies.logger.error("System power request failed: %s", error)
            if settings_window is not None and settings_window.isVisible():
                settings_window.show_power_error(error)
            else:
                window.show_power_error(error)
            return
        cleanup_session()

    power_bridge.finished.connect(finish_system_power, Qt.ConnectionType.QueuedConnection)

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
        settings_window.apply_language(current_configuration.settings.language)
        dependencies.logger.info("Settings changed theme=%s", current_configuration.home.theme)

    def handle_settings_request() -> None:
        """Open the modal settings surface and suspend the launcher behind it."""
        nonlocal settings_window
        from pideck.presentation.qt.settings_window import SettingsWindow

        if settings_window is not None:
            settings_window.close()
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        settings_window = SettingsWindow(
            current_configuration,
            theme,
            asset_root,
            window,
            update_service=update_service,
            pideck_update_info=pideck_update_service.state,
            network_enabled=network_gateway is not None,
            system_enabled=True,
            build_date=installed_build_date(),
            catalog=catalog,
            input_settings=input_settings,
            keyboard_layout=keyboard_layout,
        )
        settings_window.set_cec_status(input_settings_error or cec_adapter.status)

        def handle_input_settings_changed(settings: CecSettings) -> None:
            nonlocal input_settings, input_settings_error
            try:
                input_preferences.save(settings)
            except InputSettingsError as error:
                dependencies.logger.error("Unable to save input settings: %s", error)
                if settings_window is not None:
                    settings_window.set_input_settings(input_settings)
                    settings_window.show_error(str(error))
                return
            input_settings = settings
            input_settings_error = None
            cec_adapter.configure(settings)

        settings_window.input_settings_changed.connect(handle_input_settings_changed)

        def handle_keyboard_layout_changed(layout: KeyboardLayout) -> None:
            nonlocal keyboard_layout
            try:
                keyboard_preferences.save(layout)
            except OSError as error:
                dependencies.logger.error("Unable to save keyboard layout: %s", error)
                if settings_window is not None:
                    settings_window.show_error(str(error))
            keyboard_layout = layout
            if keyboard_applier is not None:
                keyboard_applier.apply(layout)

        settings_window.keyboard_layout_changed.connect(handle_keyboard_layout_changed)
        settings_window.cec_connection_requested.connect(
            lambda connect: cec_adapter.connect() if connect else cec_adapter.disconnect()
        )
        def handle_pideck_update_request() -> None:
            if pideck_update_service.state.status is PiDeckUpdateStatus.FAILED:
                pideck_update_service.check_async()
            else:
                pideck_update_service.prepare_async()

        settings_window.pideck_update_requested.connect(handle_pideck_update_request)
        settings_window.pideck_check_requested.connect(pideck_update_service.check_async)
        settings_window.network_requested.connect(open_network_window)
        settings_window.audio_requested.connect(open_audio_window)
        settings_window.drives_requested.connect(open_drives_window)
        settings_window.display_requested.connect(open_display_window)
        settings_window.bluetooth_requested.connect(open_bluetooth_window)
        settings_window.system_info_requested.connect(open_system_info_window)
        settings_window.power_requested.connect(handle_system_power_request)
        settings_window.settings_changed.connect(handle_settings_changed)
        settings_window.application_install_requested.connect(handle_install_request)
        settings_window.finished.connect(lambda _: window.show_launcher())
        settings_window.finished.connect(cancel_install_on_close)
        window.hide()
        settings_window.show_fullscreen()
        if installing_id is not None:
            settings_window.set_installing(installing_id)
            if install_progress is not None:
                settings_window.set_install_progress(installing_id, install_progress)
        if catalog_error is not None:
            settings_window.show_error(catalog_error)

    def cancel_install_on_close(_: int) -> None:
        """Cancel an active user-requested installation when settings closes."""
        if installer is not None:
            try:
                installer.cancel()
            except PermissionError:
                dependencies.logger.warning("Unable to cancel privileged installation process")

    def handle_install_request(application_id: str) -> None:
        """Start or cancel a catalog install without running package commands in Qt."""
        nonlocal installer, installing_id, install_progress
        if settings_window is None:
            return
        if installer is not None:
            if application_id == installing_id:
                try:
                    installer.cancel()
                except PermissionError as error:
                    from pideck.presentation.qt.localization import tr
                    settings_window.show_error(tr(current_configuration.settings.language, "install_cancel_deferred", message=str(error)))
            return
        definition = next((item for item in catalog if item.identifier == application_id), None)
        if definition is None or any(item.identifier == application_id for item in current_configuration.applications):
            settings_window.show_error("Application is no longer available for installation")
            return
        password = None
        if FlatpakInstaller.needs_password():
            from pideck.presentation.qt.settings_window import PasswordPrompt
            from PySide6.QtWidgets import QDialog

            prompt = PasswordPrompt(definition.name, dependencies.theme_repository.get(current_configuration.home.theme),
                                    settings_window, current_configuration.settings.language, installing=True)
            if prompt.exec() != QDialog.DialogCode.Accepted:
                return
            password = prompt.password()
            prompt.password_input.clear()
        operation = FlatpakInstaller()
        installer = operation
        installing_id = application_id
        install_progress = None
        settings_window.set_installing(application_id)

        def run_install() -> None:
            try:
                operation.install(
                    definition, password,
                    lambda progress: installation_bridge.progress.emit(application_id, progress),
                )
            except InstallationCancelled:
                installation_bridge.cancelled.emit(application_id)
                return
            except InstallationError as error:
                installation_bridge.finished.emit(application_id, str(error))
                return
            except Exception:
                dependencies.logger.exception("Unexpected application installation failure")
                installation_bridge.finished.emit(application_id, "Unexpected application installation failure")
                return
            installation_bridge.finished.emit(application_id, None)

        threading.Thread(target=run_install, name="pideck-application-install", daemon=True).start()

    def show_install_progress(application_id: str, progress: InstallProgress) -> None:
        """Remember and display the latest progress of the active installation."""
        nonlocal install_progress
        if application_id != installing_id:
            return
        install_progress = progress
        if settings_window is not None:
            settings_window.set_install_progress(application_id, progress)

    def cancel_install_finished(application_id: str) -> None:
        """Restore the installable entry after a user-requested cancellation."""
        nonlocal installer, installing_id, install_progress
        installer = None
        installing_id = None
        install_progress = None
        dependencies.logger.info("Application installation cancelled application=%s", application_id)
        if settings_window is not None:
            settings_window.set_installing(None)

    def finish_install(application_id: str, error: str | None) -> None:
        """Persist a successful installation and refresh launcher and settings."""
        nonlocal installer, installing_id, install_progress, current_configuration
        installer = None
        installing_id = None
        install_progress = None
        active = settings_window is not None and settings_window.isVisible()
        if error is not None:
            dependencies.logger.warning("Application installation failed: %s", error)
            if active and settings_window is not None:
                settings_window.set_installing(None)
                settings_window.show_error(error)
            return
        definition = next(item for item in catalog if item.identifier == application_id)
        if definition.icon is not None and not definition.icon.is_absolute():
            definition = replace(definition, icon=Path(os.path.abspath(asset_root / definition.icon)))
        try:
            updated = register_application(current_configuration, definition)
            dependencies.configuration_repository.save(updated)
        except ConfigurationError as save_error:
            dependencies.logger.error("Unable to register installed application: %s", save_error)
            if active and settings_window is not None:
                settings_window.set_installing(None)
                settings_window.show_error(str(save_error))
            return
        current_configuration = updated
        theme = dependencies.theme_repository.get(updated.home.theme)
        window.apply_configuration(updated, theme)
        if active:
            handle_settings_request()
            if settings_window is not None:
                settings_window._select_page(1)

    installation_bridge.progress.connect(show_install_progress, Qt.ConnectionType.QueuedConnection)
    installation_bridge.cancelled.connect(cancel_install_finished, Qt.ConnectionType.QueuedConnection)
    installation_bridge.finished.connect(finish_install, Qt.ConnectionType.QueuedConnection)

    def open_network_window() -> None:
        """Open wireless setup without giving presentation code system privileges."""
        nonlocal network_window
        if network_gateway is None or (network_window is not None and network_window.isVisible()):
            return
        from pideck.presentation.qt.network_window import NetworkWindow

        parent = settings_window if settings_window is not None and settings_window.isVisible() else window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        network_window = NetworkWindow(
            network_gateway, theme, current_configuration.settings.language, parent,
            keyboard_layout=keyboard_layout,
        )
        network_window.showFullScreen()

    def open_audio_window() -> None:
        """Open audio settings using an unprivileged platform adapter."""
        nonlocal audio_window
        if audio_window is not None and audio_window.isVisible():
            return
        from pideck.presentation.qt.audio_window import AudioWindow

        parent = settings_window if settings_window is not None and settings_window.isVisible() else window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        audio_window = AudioWindow(
            PulseAudioGateway(), theme, current_configuration.settings.language, parent,
        )
        audio_window.showFullScreen()

    def open_drives_window() -> None:
        """Open drive mounting backed by udisks2 on supported images."""
        nonlocal drives_window
        if drives_window is not None and drives_window.isVisible():
            return
        from pideck.infrastructure.udisks_storage import UDisksStorageGateway
        from pideck.presentation.qt.drives_window import DrivesWindow

        parent = settings_window if settings_window is not None and settings_window.isVisible() else window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        drives_window = DrivesWindow(
            UDisksStorageGateway(), theme, current_configuration.settings.language, parent,
        )
        drives_window.showFullScreen()

    def open_system_info_window() -> None:
        """Open the read-only hardware, driver and version overview."""
        nonlocal system_info_window
        if system_info_window is not None and system_info_window.isVisible():
            return
        from pideck.infrastructure.system_info import LinuxSystemInfoGateway
        from pideck.presentation.qt.system_info_window import SystemInfoWindow

        parent = settings_window if settings_window is not None and settings_window.isVisible() else window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        system_info_window = SystemInfoWindow(
            LinuxSystemInfoGateway(), theme, current_configuration.settings.language, parent,
        )
        system_info_window.showFullScreen()

    def open_display_window() -> None:
        """Open guarded display settings using the X11 platform adapter."""
        nonlocal display_window
        if display_window is not None and display_window.isVisible():
            return
        from pideck.presentation.qt.display_window import DisplayWindow

        parent = settings_window if settings_window is not None and settings_window.isVisible() else window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        display_window = DisplayWindow(
            display_gateway, theme, current_configuration.settings.language, parent,
        )
        display_window.showFullScreen()

    def open_bluetooth_window() -> None:
        """Open Bluetooth settings with an explicitly authorized pairing agent."""
        nonlocal bluetooth_window, bluetooth_opening
        if bluetooth_opening or (bluetooth_window is not None and bluetooth_window.isVisible()):
            return
        from pideck.presentation.qt.localization import tr

        from pideck.infrastructure.bluetooth_unavailable import UnavailableBluetooth
        from pideck.presentation.qt.bluetooth_window import BluetoothWindow

        bluetooth_opening = True
        try:
            from pideck.infrastructure.bluez import BluezGateway
            from pideck.infrastructure.bluez_agent import BluezAgent

            agent = BluezAgent()
            gateway = BluezGateway(agent.path)
        except (ImportError, BluetoothError) as error:
            # Open the window anyway so the failure is reported inside it, like other errors.
            dependencies.logger.warning("Bluetooth settings unavailable: %s", error)
            agent = gateway = UnavailableBluetooth(
                tr(current_configuration.settings.language, "bluetooth_service_unavailable"),
            )
        finally:
            bluetooth_opening = False
        if settings_window is None or not settings_window.isVisible():
            agent.close()
            return
        parent = settings_window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        bluetooth_window = BluetoothWindow(
            gateway, agent, theme, current_configuration.settings.language, parent,
            keyboard_layout=keyboard_layout,
        )
        bluetooth_window.showFullScreen()

    class NetworkBridge(QObject):
        """Forward initial network detection to the Qt thread."""

        status_received = Signal(bool)

    network_bridge = NetworkBridge()

    def offer_initial_network_setup(connected: bool) -> None:
        if connected or session_service.snapshot.state is not SessionState.IDLE:
            return
        marker = parsed_arguments.config.parent / "network-setup-offered"
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch(exist_ok=True)
        except OSError as error:
            dependencies.logger.warning("Could not save network setup state: %s", error)
        open_network_window()

    network_bridge.status_received.connect(offer_initial_network_setup, Qt.ConnectionType.QueuedConnection)

    def check_initial_network() -> None:
        if network_gateway is None:
            return
        marker = parsed_arguments.config.parent / "network-setup-offered"
        if marker.exists():
            return
        try:
            connected = network_gateway.connected()
        except NetworkError as error:
            dependencies.logger.warning("Unable to check network status: %s", error)
            return
        network_bridge.status_received.emit(connected)

    window.application_requested.connect(handle_application_request)
    window.settings_requested.connect(handle_settings_request)
    window.shutdown_requested.connect(handle_shutdown_request)
    display_gateway.restore_saved_async()
    window.show_launcher()

    QTimer.singleShot(0, pideck_update_service.check_async)
    if network_gateway is not None and network_setup_enabled:
        threading.Thread(target=check_initial_network, name="pideck-network-check", daemon=True).start()
    try:
        return application.exec()
    finally:
        application.removeEventFilter(cursor_visibility)
        cursor_visibility.show_cursor()
        cleanup_services()


if __name__ == "__main__":
    raise SystemExit(main())
