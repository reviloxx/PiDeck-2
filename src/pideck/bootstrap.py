"""Composition root and Milestone 1 validation entry point."""

import argparse
from dataclasses import replace
import logging
import os
from pathlib import Path
import shutil
import threading
import time
from typing import TYPE_CHECKING, Callable, Sequence

from pideck.application.dependencies import ApplicationDependencies
from pideck.application.power import shutdown
from pideck.application.session import (
    ApplicationSessionService,
    LaunchStatus,
    SessionObserver,
    SessionState,
)
from pideck.application.settings import SettingsService, SettingsUpdate
from pideck.application.app_management import ApplicationManagementService
from pideck.application.catalog import catalog_window_modes
from pideck.application.home_layout import normalize_home, set_application_order
from pideck.application.input_forwarding import InputForwardingService, should_forward_gamepad
from pideck.application.updates import ApplicationUpdateService
from pideck.application.pideck_updates import PiDeckUpdateInfo, PiDeckUpdateService, PiDeckUpdateStatus
from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.installation import InstallProgress
from pideck.application.ports.packages import PackageOperationError
from pideck.domain.catalog import CatalogEntry, InstallState
from pideck.domain.configuration import (
    ApplicationDefinition,
    ApplicationProfile,
    Configuration,
    WindowMode,
)
from pideck.domain.errors import ConfigurationError, PowerError, ProcessError
from pideck.domain.input_settings import CecSettings
from pideck.infrastructure.config import FileConfigurationRepository
from pideck.infrastructure.build_info import installed_build_date
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.catalog import load_catalog
from pideck.infrastructure.packages.installed import device_architecture
from pideck.infrastructure.packages.manager import SystemPackageManager
from pideck.infrastructure.audio_preferences import AudioPreferencesStore
from pideck.infrastructure.kodi_acceleration import configure_kodi_acceleration
from pideck.infrastructure.kodi_audio import configure_kodi_audio
from pideck.infrastructure.kodi_cec import disable_kodi_cec
from pideck.infrastructure.kodi_keymap import configure_kodi_keymap
from pideck.application.ports.audio import AudioError, HdmiAudioMode
from pideck.infrastructure.session.openbox import apply_release_openbox
from pideck.infrastructure.session.picom import apply_release_picom
from pideck.infrastructure.user_defaults import UserDefaultsInstaller
from pideck.infrastructure.config.window_modes import load_window_modes
from pideck.infrastructure.logging import configure_logging
from pideck.infrastructure.process import SubprocessSupervisor
from pideck.infrastructure.platform_updates import NativeUpdateGateway
from pideck.infrastructure.github_api_state import default_github_api_state_dir
from pideck.infrastructure.http_retry import call_with_retries
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
from pideck.infrastructure.samba_shares import SambaShareGateway
from pideck.infrastructure.udisks_storage import DrivePreferences, UDisksStorageGateway
from pideck.infrastructure.x11_display import DisplayPreferences, X11DisplayGateway
from pideck.infrastructure.x11_hotkey import X11HotkeyListener
from pideck.infrastructure.x11_key_injector import X11KeyInjector
from pideck.infrastructure.config.themes import apply_shipped_themes
from pideck.infrastructure.theme import FileThemeRepository

if TYPE_CHECKING:
    from pideck.presentation.qt.launcher_window import LauncherWindow
    from pideck.presentation.qt.settings_window import SettingsWindow
    from pideck.presentation.qt.network_window import NetworkWindow
    from pideck.presentation.qt.audio_window import AudioWindow
    from pideck.presentation.qt.display_window import DisplayWindow
    from pideck.presentation.qt.timezone_window import TimezoneWindow
    from pideck.presentation.qt.system_info_window import SystemInfoWindow
    from pideck.presentation.qt.cec_debug_window import CecDebugWindow
    from pideck.presentation.qt.bluetooth_window import BluetoothWindow
    from pideck.presentation.qt.drives_window import DrivesWindow

_LOGGER = logging.getLogger(__name__)


class _QtSessionObserver:
    """Bridge worker-thread session callbacks into Qt signals."""

    def __init__(
        self,
        window: "LauncherWindow",
        on_session_end: Callable[[], None] | None = None,
    ) -> None:
        """Create an observer for one launcher window."""
        self._window = window
        self._on_session_end = on_session_end

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
        if self._on_session_end is not None:
            self._on_session_end()
        self._window.notify_session_finished(application, return_code)

    def failed(self, application: ApplicationDefinition, message: str) -> None:
        """Forward launch failure feedback to the window."""
        if self._on_session_end is not None:
            self._on_session_end()
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


def _apply_catalog_preferred_inputs(
    configuration: Configuration, catalog: tuple[CatalogEntry, ...],
) -> Configuration:
    """Prefer the catalog's input hint icons without rewriting saved settings."""
    preferred = {
        entry.identifier: entry.application.preferred_input for entry in catalog
    }
    if not preferred:
        return configuration
    return replace(
        configuration,
        applications=tuple(
            replace(app, preferred_input=preferred[app.identifier])
            if app.identifier in preferred
            else app
            for app in configuration.applications
        ),
    )


_BROWSER_LAUNCH_IDS = frozenset({"brave", "chrome", "chromium"})
_LEGACY_VAAPI_MARKERS = ("VaapiVideoDecoder", "VaapiVideoDecodeLinuxGL")
_MODERN_VAAPI_MARKER = "AcceleratedVideoDecodeLinuxGL"
_YOUTUBE_VAAPI_ENV_MARKER = "--env=QTWEBENGINE_CHROMIUM_FLAGS="


def _arguments_text(arguments: tuple[str, ...]) -> str:
    """Join launch arguments for substring checks against feature flag blobs."""
    return "\x00".join(arguments)


def _browser_launch_needs_migration(
    saved: tuple[str, ...],
    catalog_arguments: tuple[str, ...],
) -> bool:
    """Detect missing maximize or outdated NVIDIA VA-API Chromium feature flags."""
    if "--start-maximized" in catalog_arguments and "--start-maximized" not in saved:
        return True
    saved_text = _arguments_text(saved)
    catalog_text = _arguments_text(catalog_arguments)
    if any(marker in saved_text for marker in _LEGACY_VAAPI_MARKERS):
        return True
    if _MODERN_VAAPI_MARKER in catalog_text and _MODERN_VAAPI_MARKER not in saved_text:
        return True
    return False


def _youtube_launch_needs_migration(
    saved: tuple[str, ...],
    catalog_arguments: tuple[str, ...],
) -> bool:
    """Detect missing Flatpak VA-API / QtWebEngine env overrides for Red."""
    catalog_text = _arguments_text(catalog_arguments)
    if _YOUTUBE_VAAPI_ENV_MARKER not in catalog_text:
        return False
    return _YOUTUBE_VAAPI_ENV_MARKER not in _arguments_text(saved)


def _migrate_catalog_launch_arguments(
    configuration: Configuration,
    catalog: tuple[CatalogEntry, ...],
    architecture: str,
) -> Configuration:
    """Persist catalog launch flags missing from older installs.

    OTA updates ship catalog argv (maximize, modern VA-API features, Red Flatpak
    envs) but never rewrite saved ``pideck.yaml`` arguments. Existing tiles keep
    the pre-OTA argv until this migration runs on appliance start.
    """
    by_id = {entry.identifier: entry for entry in catalog}
    updated: list[ApplicationDefinition] = []
    changed = False
    for application in configuration.applications:
        entry = by_id.get(application.identifier)
        if entry is None:
            updated.append(application)
            continue
        catalog_arguments = entry.definition_for(architecture).arguments
        needs_migration = False
        if application.identifier in _BROWSER_LAUNCH_IDS:
            needs_migration = _browser_launch_needs_migration(
                application.arguments, catalog_arguments,
            )
        elif application.identifier == "youtube":
            needs_migration = _youtube_launch_needs_migration(
                application.arguments, catalog_arguments,
            )
        if not needs_migration:
            updated.append(application)
            continue
        updated.append(replace(application, arguments=catalog_arguments))
        changed = True
    if not changed:
        return configuration
    return replace(configuration, applications=tuple(updated))


def _load_catalog(asset_root: Path, logger: logging.Logger) -> tuple[tuple[CatalogEntry, ...], str | None]:
    """Load the release catalog with icon paths resolved against the asset root."""
    try:
        catalog = load_catalog(asset_root.parent / "assets/catalog/applications.yaml")
    except ConfigurationError as error:
        logger.error("Unable to load application catalog: %s", error)
        return (), str(error)
    resolved = []
    for entry in catalog:
        icon = entry.application.icon
        if icon is not None and not icon.is_absolute():
            icon = Path(os.path.abspath(asset_root / icon))
        resolved.append(replace(entry, application=replace(entry.application, icon=icon)))
    return tuple(resolved), None


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
    resolved_asset_root = asset_root or configuration_path.parent
    catalog, catalog_error = _load_catalog(resolved_asset_root, logger)
    window_modes: dict[str, WindowMode] = catalog_window_modes(catalog)
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
    configuration = _apply_catalog_preferred_inputs(configuration, catalog)
    if image_mode and catalog:
        migrated = _migrate_catalog_launch_arguments(
            configuration, catalog, device_architecture(),
        )
        if migrated is not configuration:
            configuration_repository.save(migrated)
            logger.info("Migrated browser launch arguments from the application catalog")
            configuration = migrated
    configuration = apply_shipped_themes(configuration, resolved_asset_root, logger=logger)
    normalized = normalize_home(configuration)
    if normalized is not configuration:
        configuration_repository.save(normalized)
        configuration = normalized
    theme_repository = FileThemeRepository(
        themes=configuration.themes,
        asset_root=resolved_asset_root,
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
        catalog=catalog,
        catalog_error=catalog_error,
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

    from pideck.presentation.qt.backspace_back import BackspaceAsBack
    from pideck.presentation.qt.cursor_visibility import CursorVisibility

    application = QApplication(["pideck"])
    application.setApplicationName("PiDeck")
    # Replace image-started openbox/picom so updates pick up release session configs.
    apply_release_openbox()
    apply_release_picom()
    cursor_visibility = CursorVisibility(application)
    application.installEventFilter(cursor_visibility)
    application.installEventFilter(BackspaceAsBack(application))
    application.aboutToQuit.connect(cursor_visibility.close)
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

    class CecKeyBridge(QObject):
        """Forward CEC key observations to the Qt thread for the debug window."""

        key_received = Signal(object)

    cec_status_bridge = CecStatusBridge()
    cec_key_bridge = CecKeyBridge()
    cec_adapter = CecClientAdapter(
        settings=input_settings,
        status_changed=cec_status_bridge.status_received.emit,
        key_observed=cec_key_bridge.key_received.emit,
    )
    power = SystemPower() if parsed_arguments.power_off_on_shutdown else None
    settings_window: SettingsWindow | None = None
    catalog = dependencies.catalog
    catalog_error = dependencies.catalog_error
    catalog_by_id = {entry.identifier: entry for entry in catalog}
    package_manager = SystemPackageManager()
    application_manager = ApplicationManagementService(catalog, package_manager, device_architecture())
    from pideck.infrastructure.remote_support import (
        RemoteSupportService,
        clock_looks_wrong,
        is_enabled as remote_support_enabled,
        repair_system_clock,
    )

    remote_support = RemoteSupportService(package_manager, asset_root)
    if image_mode and clock_looks_wrong():
        dependencies.logger.warning("System clock looks wrong; requesting NTP")
        repair_system_clock()
    user_defaults = UserDefaultsInstaller()
    from pideck.presentation.qt.application_operations import ApplicationOperationsController

    application_operations = ApplicationOperationsController(application_manager)
    network_window: NetworkWindow | None = None
    audio_window: AudioWindow | None = None
    drives_window: DrivesWindow | None = None
    system_info_window: SystemInfoWindow | None = None
    cec_debug_window: CecDebugWindow | None = None
    display_window: DisplayWindow | None = None
    timezone_window: TimezoneWindow | None = None
    bluetooth_window: BluetoothWindow | None = None
    bluetooth_opening = False

    def handle_cec_status(status: str) -> None:
        if settings_window is not None:
            settings_window.set_cec_status(status)

    def handle_cec_key(observation: object) -> None:
        if cec_debug_window is not None and cec_debug_window.isVisible():
            from pideck.infrastructure.input_cec import CecKeyObservation

            if isinstance(observation, CecKeyObservation):
                cec_debug_window.append(observation)

    cec_status_bridge.status_received.connect(handle_cec_status, Qt.ConnectionType.QueuedConnection)
    cec_key_bridge.key_received.connect(handle_cec_key, Qt.ConnectionType.QueuedConnection)
    display_gateway = X11DisplayGateway(DisplayPreferences(parsed_arguments.config.parent / "display.yaml"))
    audio_gateway = PulseAudioGateway()
    audio_preferences = AudioPreferencesStore(parsed_arguments.config.parent / "audio.yaml")
    drive_preferences = DrivePreferences(parsed_arguments.config.parent / "drives.yaml")
    storage_gateway = UDisksStorageGateway(preferences=drive_preferences)
    share_gateway = SambaShareGateway(
        preferences=drive_preferences,
        list_drives=storage_gateway.list_drives,
    )

    class PiDeckUpdateBridge(QObject):
        """Forward release worker results to the Qt thread."""

        status_received = Signal(object)

    update_bridge = PiDeckUpdateBridge()
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
        call_with_retries(
            lambda: image_update_request("stage", timeout=1800, version=version),
            label=f"PiDeck update stage {version}",
        )

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

    github_api_state_dir = default_github_api_state_dir()

    def discover_pideck_release():
        return find_latest_release(github_api_state_dir)

    pideck_update_service = PiDeckUpdateService(
        release_version,
        discover_pideck_release,
        version_key,
        update_bridge.status_received.emit,
        inspect=inspect_image_update,
        prepare=prepare_image_update,
        ready=confirm_image_ready,
    )

    last_pideck_status: PiDeckUpdateStatus | None = None

    def display_pideck_update(info: PiDeckUpdateInfo) -> None:
        """Refresh About and offer an immediate reboot when an update is ready."""
        nonlocal last_pideck_status
        previous = last_pideck_status
        last_pideck_status = info.status
        if settings_window is not None:
            settings_window.set_pideck_update_info(info)
            if (
                info.status is PiDeckUpdateStatus.PENDING
                and previous is PiDeckUpdateStatus.PREPARING
                and settings_window.isVisible()
            ):
                settings_window.offer_update_restart()

    update_bridge.status_received.connect(display_pideck_update, Qt.ConnectionType.QueuedConnection)
    application.aboutToQuit.connect(pideck_update_service.close)
    release_timer = QTimer(application)
    release_timer.setInterval(current_configuration.settings.update_check_interval_hours * 60 * 60 * 1000)
    release_timer.timeout.connect(pideck_update_service.check_async)
    release_timer.start()
    from pideck.presentation.qt.session_overlay import SessionOverlay

    overlay_theme = dependencies.theme_repository.get(current_configuration.home.theme)
    overlay_language = current_configuration.settings.language
    # Recreate on each open so picom never keeps a stale redirected pixmap.
    session_overlay: SessionOverlay | None = None

    def clear_session_overlay() -> None:
        """Destroy any overlay instance so it cannot keep Qt focus after dismiss."""
        nonlocal session_overlay
        overlay = session_overlay
        session_overlay = None
        if overlay is None:
            return
        if overlay.isVisible():
            overlay.hide()
        overlay.deleteLater()
        QApplication.setActiveWindow(None)
        QApplication.processEvents()

    def hide_session_overlay() -> None:
        """Destroy the current overlay when the application session ends."""
        clear_session_overlay()
        cursor_visibility.show_cursor()

    session_observer: SessionObserver = _QtSessionObserver(window, hide_session_overlay)
    visibility_detector = X11WindowVisibilityDetector()
    session_service = ApplicationSessionService(
        dependencies.process_supervisor,
        session_observer,
        visibility_detector,
        cec_adapter,
        releases_input=lambda _app: False,
    )
    key_injector = X11KeyInjector()
    input_forwarding = InputForwardingService(key_injector)

    def session_active() -> bool:
        """True while an application session is starting or running."""
        return session_service.snapshot.state in (SessionState.STARTING, SessionState.RUNNING)

    def session_overlay_visible() -> bool:
        """True when the session overlay is currently shown."""
        return session_overlay is not None and session_overlay.isVisible()

    def pideck_dialog_visible() -> bool:
        """True when a settings or system dialog should receive remote input."""
        return (
            (bluetooth_window is not None and bluetooth_window.isVisible())
            or (display_window is not None and display_window.isVisible())
            or (timezone_window is not None and timezone_window.isVisible())
            or (audio_window is not None and audio_window.isVisible())
            or (drives_window is not None and drives_window.isVisible())
            or (system_info_window is not None and system_info_window.isVisible())
            or (cec_debug_window is not None and cec_debug_window.isVisible())
            or (network_window is not None and network_window.isVisible())
            or (settings_window is not None and settings_window.isVisible())
            or window.has_open_dialog()
        )

    def pideck_owns_focus() -> bool:
        """True when a PiDeck surface should receive input instead of the app.

        Do not use ``QApplication.activeWindow()``: a hidden overlay can remain
        the active Qt window and would then swallow every forwarded remote key.
        """
        if session_overlay_visible() or pideck_dialog_visible():
            return True
        return session_service.snapshot.state is SessionState.IDLE and window.isVisible()

    def dismiss_session_overlay() -> None:
        """Hide and destroy the overlay without stopping the application."""
        overlay = session_overlay
        if overlay is None:
            return
        overlay.dismiss()
        clear_session_overlay()

    def return_to_pideck_from_overlay() -> None:
        """Cover with the launcher first, then stop the running application.

        Hiding the overlay alone would briefly (or permanently) reveal the app
        while stop() is still terminating Flatpak/steamlink orphans.
        """
        clear_session_overlay()
        window.show_launcher()
        session_service.stop()

    def open_session_overlay() -> None:
        """Raise a fresh overlay above the running application."""
        nonlocal session_overlay
        if not session_active():
            return
        clear_session_overlay()
        session_overlay = SessionOverlay(overlay_theme, overlay_language)
        session_overlay.return_requested.connect(return_to_pideck_from_overlay)
        session_overlay.dismissed.connect(clear_session_overlay)
        session_overlay.open_overlay()

    def toggle_session_overlay() -> None:
        """Open or dismiss the overlay while an application session is active."""
        if not session_active():
            return
        if session_overlay_visible():
            dismiss_session_overlay()
        else:
            open_session_overlay()

    def forward_cec_to_application(event: InputEvent) -> bool:
        """Send CEC input to the running app; report whether it was consumed."""
        if session_overlay_visible():
            return False
        return input_forwarding.handle(
            event, session_service.snapshot, pideck_has_focus=pideck_owns_focus(),
        )

    def forward_gamepad_to_application(event: InputEvent) -> bool:
        """Send gamepad actions as keys for TV apps; leave native-pad apps alone."""
        if session_overlay_visible() or pideck_dialog_visible():
            return False
        snapshot = session_service.snapshot
        if not should_forward_gamepad(snapshot.application):
            return False
        return input_forwarding.handle(
            event, snapshot, pideck_has_focus=pideck_owns_focus(),
        )

    class GamepadBridge(QObject):
        """Marshal input reader-thread events onto the Qt event loop."""

        event_received = Signal(object)

        def handle_input(self, event: InputEvent) -> None:
            """Emit one normalized event for queued Qt delivery."""
            self.event_received.emit(event)

    gamepad_bridge = GamepadBridge()

    def handle_gamepad_event(event: InputEvent) -> None:
        """Route normalized input to the overlay, settings, app, or launcher."""
        if event.pressed:
            cursor_visibility.hide_cursor()
        if event.pressed and event.action in (InputAction.MENU, InputAction.HOME):
            if session_overlay_visible() or session_active():
                toggle_session_overlay()
                return
        if session_overlay_visible() and session_overlay is not None:
            session_overlay.handle_input(event)
            return
        if session_active() and forward_gamepad_to_application(event):
            return
        if bluetooth_window is not None and bluetooth_window.isVisible():
            bluetooth_window.handle_input(event)
        elif display_window is not None and display_window.isVisible():
            display_window.handle_input(event)
        elif timezone_window is not None and timezone_window.isVisible():
            timezone_window.handle_input(event)
        elif audio_window is not None and audio_window.isVisible():
            audio_window.handle_input(event)
        elif drives_window is not None and drives_window.isVisible():
            drives_window.handle_input(event)
        elif system_info_window is not None and system_info_window.isVisible():
            system_info_window.handle_input(event)
        elif cec_debug_window is not None and cec_debug_window.isVisible():
            cec_debug_window.handle_input(event)
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
                if event.pressed:
                    cursor_visibility.hide_cursor()
                if not forward_cec_to_application(event):
                    handle_gamepad_event(event)

        cec_bridge.event_received.connect(handle_cec_event, Qt.ConnectionType.QueuedConnection)
        cec_adapter.start(cec_bridge)

    class HotkeyBridge(QObject):
        """Marshal the global Super grab onto the Qt event loop."""

        pressed = Signal()

    hotkey_bridge = HotkeyBridge()
    hotkey_bridge.pressed.connect(toggle_session_overlay, Qt.ConnectionType.QueuedConnection)
    hotkey_listener = X11HotkeyListener(hotkey_bridge.pressed.emit)
    hotkey_listener.start()

    def handle_application_request(application_definition: ApplicationDefinition) -> None:
        """Resolve profile and replacement decisions for a tile activation."""
        entry = catalog_by_id.get(application_definition.identifier)
        if entry is not None:
            user_defaults.apply(entry)
        if application_definition.identifier == "kodi":
            disable_kodi_cec()
            configure_kodi_keymap()
            configure_kodi_acceleration()
            try:
                hdmi_mode = audio_preferences.load().hdmi_mode
            except AudioError:
                hdmi_mode = HdmiAudioMode.SURROUND
            try:
                audio_gateway.ensure_hdmi(hdmi_mode=hdmi_mode)
            except AudioError as error:
                dependencies.logger.warning("Unable to prepare HDMI audio for Kodi: %s", error)
            configure_kodi_audio(hdmi_mode=hdmi_mode)
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
        hide_session_overlay()
        session_service.close()
        update_service.close()
        gamepad_adapter.close()
        hotkey_listener.close()
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

    class RemoteSupportBridge(QObject):
        """Marshal remote-support worker results onto the Qt event loop."""

        finished = Signal(object, object)  # error message or None, status text

    remote_support_bridge = RemoteSupportBridge()
    remote_support_busy = False

    def handle_remote_support_request() -> None:
        """Toggle LAN SSH remote support via the privileged package broker."""
        nonlocal remote_support_busy
        from pideck.presentation.qt.localization import tr
        from pideck.presentation.qt.power_dialog import ConfirmationDialog
        from PySide6.QtWidgets import QDialog

        if settings_window is None or remote_support_busy:
            return
        language = current_configuration.settings.language
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        enabling = not remote_support_enabled()
        title = tr(language, "remote_support_enable" if enabling else "remote_support_disable")
        dialog = ConfirmationDialog(
            title,
            tr(language, "remote_support_description"),
            tr(language, "continue"),
            tr(language, "cancel"),
            theme,
            settings_window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        remote_support_busy = True
        settings_window.set_remote_support_status(tr(language, "remote_support_working"))

        def worker() -> None:
            from pideck.application.ports.installation import InstallProgress
            from pideck.application.ports.packages import PackageOperationError

            def on_progress(progress: InstallProgress) -> None:
                del progress

            try:
                if enabling:
                    remote_support.enable(on_progress)
                else:
                    remote_support.disable(on_progress)
                remote_support_bridge.finished.emit(None, remote_support.status_message())
            except PackageOperationError as error:
                remote_support_bridge.finished.emit(str(error), remote_support.status_message())
            except Exception as error:  # noqa: BLE001 — surface unexpected broker failures
                dependencies.logger.exception("Remote support toggle failed")
                remote_support_bridge.finished.emit(str(error), remote_support.status_message())

        threading.Thread(target=worker, name="pideck-remote-support", daemon=True).start()

    def finish_remote_support(error: object, status: object) -> None:
        """Update Settings after enable/disable completes."""
        nonlocal remote_support_busy
        remote_support_busy = False
        if settings_window is None:
            return
        settings_window.set_remote_support_status(str(status))
        if error is not None:
            settings_window.show_error(str(error))

    remote_support_bridge.finished.connect(finish_remote_support, Qt.ConnectionType.QueuedConnection)

    def handle_settings_request() -> None:
        """Open the modal settings surface and suspend the launcher behind it."""
        nonlocal settings_window
        from pideck.presentation.qt.settings_window import SettingsWindow

        if settings_window is not None:
            settings_window.close()
        sync_installed_applications()
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
            catalog_statuses=application_manager.statuses(current_configuration),
            input_settings=input_settings,
            keyboard_layout=keyboard_layout,
        )
        settings_window.set_cec_status(input_settings_error or cec_adapter.status)
        settings_window.set_remote_support_status(remote_support.status_message())

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
        settings_window.cec_debug_requested.connect(open_cec_debug_window)
        def handle_pideck_update_request() -> None:
            state = pideck_update_service.state
            if state.status is PiDeckUpdateStatus.FAILED and state.available_version is None:
                pideck_update_service.check_async()
            else:
                pideck_update_service.prepare_async()

        settings_window.pideck_update_requested.connect(handle_pideck_update_request)
        settings_window.pideck_check_requested.connect(pideck_update_service.check_async)
        settings_window.network_requested.connect(open_network_window)
        settings_window.audio_requested.connect(open_audio_window)
        settings_window.drives_requested.connect(open_drives_window)
        settings_window.display_requested.connect(open_display_window)
        settings_window.timezone_requested.connect(open_timezone_window)
        settings_window.bluetooth_requested.connect(open_bluetooth_window)
        settings_window.system_info_requested.connect(open_system_info_window)
        settings_window.remote_support_requested.connect(handle_remote_support_request)
        settings_window.power_requested.connect(handle_system_power_request)
        settings_window.settings_changed.connect(handle_settings_changed)
        settings_window.application_install_requested.connect(handle_install_request)
        settings_window.application_remove_requested.connect(handle_remove_request)
        settings_window.application_order_requested.connect(handle_order_request)
        settings_window.finished.connect(lambda _: window.show_launcher())
        settings_window.finished.connect(cancel_install_on_close)
        window.hide()
        settings_window.show_fullscreen()
        active_id = application_operations.active_id
        if active_id is not None:
            settings_window.set_application_busy(active_id, removing=application_operations.removing)
            if application_operations.last_progress is not None:
                settings_window.set_application_progress(active_id, application_operations.last_progress)
        if catalog_error is not None:
            settings_window.show_error(catalog_error)

    def cancel_install_on_close(_: int) -> None:
        """Cancel an active user-requested installation when settings closes."""
        try:
            application_operations.cancel()
        except PermissionError:
            dependencies.logger.warning("Unable to cancel privileged installation process")

    def handle_install_request(application_id: str) -> None:
        """Start or cancel a catalog install without running package commands in Qt."""
        from pideck.domain.catalog import InstallState
        from pideck.presentation.qt.localization import tr

        if settings_window is None:
            return
        if application_operations.active_id is not None:
            if application_id == application_operations.active_id:
                try:
                    application_operations.cancel()
                except PermissionError as error:
                    settings_window.show_error(tr(
                        current_configuration.settings.language, "install_cancel_deferred", message=str(error),
                    ))
            return
        status = application_manager.status(current_configuration, application_id)
        if status.state is not InstallState.NOT_INSTALLED:
            settings_window.show_error("Application is no longer available for installation")
            return
        password = None
        if application_manager.needs_password(application_id):
            from pideck.presentation.qt.settings_window import PasswordPrompt
            from PySide6.QtWidgets import QDialog

            prompt = PasswordPrompt(status.entry.name, dependencies.theme_repository.get(current_configuration.home.theme),
                                    settings_window, current_configuration.settings.language, installing=True)
            if prompt.exec() != QDialog.DialogCode.Accepted:
                return
            password = prompt.password()
            prompt.password_input.clear()
        application_operations.start_install(application_id, password)
        settings_window.set_application_busy(application_id)

    def handle_remove_request(application_id: str) -> None:
        """Remove an installed catalog application after the user confirmed it."""
        if settings_window is None or application_operations.active_id is not None:
            return
        application_operations.start_remove(application_id)
        settings_window.set_application_busy(application_id, removing=True)

    def show_install_progress(application_id: str, progress: InstallProgress) -> None:
        """Display the latest progress of the running operation."""
        if settings_window is not None:
            settings_window.set_application_progress(application_id, progress)

    def cancel_install_finished(application_id: str) -> None:
        """Restore the installable entry after a user-requested cancellation."""
        if settings_window is not None:
            settings_window.set_application_busy(None)

    def finish_application_operation(application_id: str, removing: bool, error: str | None) -> None:
        """Persist an installed or removed application and refresh launcher and settings."""
        active = settings_window is not None and settings_window.isVisible()
        if error is not None:
            if active and settings_window is not None:
                settings_window.set_application_busy(None)
                settings_window.show_error(error)
            return
        try:
            if removing:
                updated = application_manager.unregistered(current_configuration, application_id)
            else:
                updated = application_manager.registered(current_configuration, application_id)
            if updated is not current_configuration:
                dependencies.configuration_repository.save(updated)
        except ConfigurationError as save_error:
            dependencies.logger.error("Unable to save application change: %s", save_error)
            if active and settings_window is not None:
                settings_window.set_application_busy(None)
                settings_window.show_error(str(save_error))
            return
        apply_application_change(updated)
        if settings_window is not None:
            settings_window.set_application_busy(None)

    def apply_application_change(updated: Configuration) -> None:
        """Adopt a saved application change in the launcher and in open settings."""
        nonlocal current_configuration
        current_configuration = updated
        theme = dependencies.theme_repository.get(updated.home.theme)
        window.apply_configuration(updated, theme)
        if settings_window is not None:
            settings_window.set_configuration(updated, application_manager.statuses(updated))

    def save_application_layout(change: Callable[[Configuration], Configuration]) -> None:
        """Persist a home-screen order change requested in settings."""
        try:
            updated = change(current_configuration)
            if updated is current_configuration:
                return
            dependencies.configuration_repository.save(updated)
        except (ConfigurationError, PackageOperationError) as error:
            dependencies.logger.warning("Unable to change application layout: %s", error)
            if settings_window is not None:
                settings_window.show_error(str(error))
            return
        apply_application_change(updated)

    def sync_installed_applications() -> None:
        """Add every installed catalog application to the home screen."""
        from pideck.infrastructure.remote_support import SUPPORT_APP_ID

        updated = current_configuration
        for status in application_manager.statuses(updated):
            if status.identifier == SUPPORT_APP_ID:
                continue
            if status.state is InstallState.INSTALLED:
                updated = application_manager.registered(updated, status.identifier)
        updated = normalize_home(updated)
        if updated is current_configuration:
            return
        try:
            dependencies.configuration_repository.save(updated)
        except ConfigurationError as error:
            dependencies.logger.warning("Unable to sync installed applications: %s", error)
            return
        apply_application_change(updated)

    def handle_order_request(order: tuple[str, ...]) -> None:
        """Save the home-screen tile order chosen in settings."""
        save_application_layout(lambda configuration: set_application_order(configuration, tuple(order)))

    application_operations.progress.connect(show_install_progress)
    application_operations.cancelled.connect(cancel_install_finished)
    application_operations.finished.connect(finish_application_operation)

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
            audio_gateway,
            audio_preferences,
            theme,
            current_configuration.settings.language,
            parent,
        )
        audio_window.showFullScreen()

    def open_drives_window() -> None:
        """Open drive mounting backed by udisks2 on supported images."""
        nonlocal drives_window
        if drives_window is not None and drives_window.isVisible():
            return
        from pideck.presentation.qt.drives_window import DrivesWindow

        parent = settings_window if settings_window is not None and settings_window.isVisible() else window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        drives_window = DrivesWindow(
            storage_gateway, theme, current_configuration.settings.language, parent,
            shares=share_gateway,
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

    def open_cec_debug_window() -> None:
        """Open the live CEC remote key log for diagnosing unmapped buttons."""
        nonlocal cec_debug_window
        if cec_debug_window is not None and cec_debug_window.isVisible():
            return
        from pideck.presentation.qt.cec_debug_window import CecDebugWindow

        parent = settings_window if settings_window is not None and settings_window.isVisible() else window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        cec_debug_window = CecDebugWindow(theme, current_configuration.settings.language, parent)
        cec_debug_window.set_history(cec_adapter.recent_keys())
        cec_debug_window.showFullScreen()

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

    def open_timezone_window() -> None:
        """Open host timezone settings using timedatectl."""
        nonlocal timezone_window
        if timezone_window is not None and timezone_window.isVisible():
            return
        from pideck.infrastructure.timedatectl_timezone import TimedatectlTimezoneGateway
        from pideck.presentation.qt.timezone_window import TimezoneWindow

        parent = settings_window if settings_window is not None and settings_window.isVisible() else window
        theme = dependencies.theme_repository.get(current_configuration.home.theme)
        timezone_window = TimezoneWindow(
            TimedatectlTimezoneGateway(), theme, current_configuration.settings.language, parent,
        )
        timezone_window.showFullScreen()

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

    def restore_drives_and_shares() -> None:
        """Remount remembered drives, then re-apply preferred Samba exports."""
        try:
            storage_gateway.restore_saved()
        except Exception as error:
            dependencies.logger.warning("Unable to restore remembered drives: %s", error)
        try:
            share_gateway.restore_shared()
        except Exception as error:
            dependencies.logger.warning("Unable to restore shared drives: %s", error)

    if drive_preferences.path.exists() and storage_gateway.available():
        threading.Thread(
            target=restore_drives_and_shares, name="pideck-drives-restore", daemon=True,
        ).start()
    sync_installed_applications()
    window.show_launcher()
    # Openbox may have just restarted for the release rc; re-assert fullscreen.
    window.schedule_fullscreen_reassert(100)

    QTimer.singleShot(0, pideck_update_service.check_async)
    if network_gateway is not None and network_setup_enabled:
        threading.Thread(target=check_initial_network, name="pideck-network-check", daemon=True).start()
    try:
        return application.exec()
    finally:
        application.removeEventFilter(cursor_visibility)
        cursor_visibility.close()
        cleanup_services()


if __name__ == "__main__":
    raise SystemExit(main())
