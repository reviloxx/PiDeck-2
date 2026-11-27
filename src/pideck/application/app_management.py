"""Install, remove and report catalog applications independently of the UI.

Package work (``install``/``uninstall``) blocks and runs on a worker thread;
configuration changes (``registered``/``unregistered``) are pure and run on the
caller's thread so the presentation layer stays the single owner of state.
"""

from dataclasses import dataclass, replace
import logging

from pideck.application.catalog import configured_application, register_application, unregister_application
from pideck.application.ports.installation import InstallProgress, InstallProgressCallback, InstallStage
from pideck.application.ports.packages import ApplicationPackageManager, PackageOperationError
from pideck.domain.catalog import CatalogEntry, InstallState
from pideck.domain.configuration import Configuration
from pideck.infrastructure.packages.app_markers import SHARED_CHROMIUM_APP_IDS, AppInstallMarkers

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CatalogItemStatus:
    """Install state and available action for one supported application."""

    entry: CatalogEntry
    state: InstallState
    removable: bool = False

    @property
    def identifier(self) -> str:
        """Return the catalog identifier."""
        return self.entry.identifier


class ApplicationManagementService:
    """Coordinate the catalog, the package manager and saved applications."""

    def __init__(
        self,
        catalog: tuple[CatalogEntry, ...],
        packages: ApplicationPackageManager,
        architecture: str,
        markers: AppInstallMarkers | None = None,
    ) -> None:
        """Bind the catalog to this device's architecture and package manager."""
        self._catalog = {entry.identifier: entry for entry in catalog}
        self._packages = packages
        self._architecture = architecture
        self._markers = markers or AppInstallMarkers()

    @property
    def catalog(self) -> tuple[CatalogEntry, ...]:
        """Return all supported applications in catalog order."""
        return tuple(self._catalog.values())

    def statuses(self, configuration: Configuration) -> tuple[CatalogItemStatus, ...]:
        """Return the install state of every user-facing catalog application."""
        from pideck.infrastructure.remote_support import SUPPORT_APP_ID

        return tuple(
            self.status(configuration, entry.identifier)
            for entry in self._catalog.values()
            if entry.identifier != SUPPORT_APP_ID
        )

    def status(self, configuration: Configuration, identifier: str) -> CatalogItemStatus:
        """Return the install state of one supported application."""
        entry = self._entry(identifier)
        spec = entry.spec_for(self._architecture)
        configured = configured_application(configuration, entry) is not None
        if spec is None:
            return CatalogItemStatus(entry, InstallState.INSTALLED if configured else InstallState.UNAVAILABLE)
        if identifier in SHARED_CHROMIUM_APP_IDS:
            return self._shared_chromium_status(entry, spec, configured)
        if self._packages.is_installed(spec):
            return CatalogItemStatus(entry, InstallState.INSTALLED, self._packages.can_remove(spec))
        if not self._packages.can_install(spec):
            return CatalogItemStatus(entry, InstallState.REQUIRES_NEWER_IMAGE)
        return CatalogItemStatus(entry, InstallState.NOT_INSTALLED)

    def needs_password(self, identifier: str) -> bool:
        """Return whether installing first needs an administrator password."""
        spec = self._entry(identifier).spec_for(self._architecture)
        return spec is not None and self._packages.needs_password(spec)

    def install(self, identifier: str, on_progress: InstallProgressCallback, password: str | None = None) -> None:
        """Install the application's packages; call from a worker thread."""
        spec = self._entry(identifier).spec_for(self._architecture)
        if spec is None:
            raise PackageOperationError("This application is not available for this device")
        _LOGGER.info("Installing application id=%s method=%s", identifier, spec.method)
        self._packages.install(identifier, spec, on_progress, password)
        if identifier in SHARED_CHROMIUM_APP_IDS:
            self._markers.mark(identifier)

    def uninstall(self, identifier: str, on_progress: InstallProgressCallback) -> None:
        """Remove the application's packages but keep user data; call from a worker thread."""
        spec = self._entry(identifier).spec_for(self._architecture)
        if spec is None:
            raise PackageOperationError("This application cannot be removed on this device")
        _LOGGER.info("Removing application id=%s method=%s", identifier, spec.method)
        if identifier in SHARED_CHROMIUM_APP_IDS:
            self._markers.unmark(identifier)
            siblings = SHARED_CHROMIUM_APP_IDS - {identifier}
            if self._markers.any_marked(siblings):
                on_progress(InstallProgress(InstallStage.FINISHING, 100))
                return
        self._packages.uninstall(identifier, spec, on_progress)

    def cancel(self) -> None:
        """Request cancellation of the running operation."""
        self._packages.cancel()

    def registered(self, configuration: Configuration, identifier: str) -> Configuration:
        """Add an installed application to the home screen unless it is already configured."""
        entry = self._entry(identifier)
        if configured_application(configuration, entry) is not None:
            return configuration
        definition = replace(entry.definition_for(self._architecture), window_mode=entry.window_mode)
        return register_application(configuration, definition)

    def unregistered(self, configuration: Configuration, identifier: str) -> Configuration:
        """Remove a removed application, including a custom entry for the same Flatpak."""
        configured = configured_application(configuration, self._entry(identifier))
        if configured is None:
            return configuration
        return unregister_application(configuration, configured.identifier)

    def _shared_chromium_status(
        self, entry: CatalogEntry, spec, configured: bool,
    ) -> CatalogItemStatus:
        """Use install markers so Chromium and YouTube TV stay independent."""
        identifier = entry.identifier
        runtime_present = self._packages.is_installed(spec)
        if configured and runtime_present and not self._markers.is_marked(identifier):
            # Existing home tiles from before markers keep working after OTA.
            self._markers.mark(identifier)
        if self._markers.is_marked(identifier) and runtime_present:
            return CatalogItemStatus(entry, InstallState.INSTALLED, self._packages.can_remove(spec))
        if not self._packages.can_install(spec):
            return CatalogItemStatus(entry, InstallState.REQUIRES_NEWER_IMAGE)
        return CatalogItemStatus(entry, InstallState.NOT_INSTALLED)

    def _entry(self, identifier: str) -> CatalogEntry:
        try:
            return self._catalog[identifier]
        except KeyError:
            raise PackageOperationError(f"Unknown application: {identifier}") from None
