"""Route catalog installs to the image's root service or the user's Flatpak.

New images install apt, ``.deb`` and system Flatpak packages through the
restricted update service. Older images lack that service; there only user
Flatpaks can be installed, and system packages report that a newer image is
needed. This keeps every application release usable on every image.
"""

from collections.abc import Callable
import threading

from pideck.application.ports.installation import InstallProgressCallback
from pideck.application.ports.packages import PackageOperationError
from pideck.domain.catalog import InstallMethod, InstallSpec
from pideck.infrastructure.flatpak_installer import FlatpakInstaller
from pideck.infrastructure.packages.installed import InstalledPackages
from pideck.infrastructure.packages.privileged import PrivilegedApplications


class SystemPackageManager:
    """Implement ``ApplicationPackageManager`` for PiDeck images and desktops."""

    def __init__(
        self,
        broker: PrivilegedApplications | None = None,
        installed: InstalledPackages | None = None,
        user_flatpak: Callable[[], FlatpakInstaller] = FlatpakInstaller,
    ) -> None:
        """Inject the service client, state reader and user installer factory."""
        self._broker = broker or PrivilegedApplications()
        self._installed = installed or InstalledPackages()
        self._user_flatpak = user_flatpak
        self._lock = threading.Lock()
        self._active: FlatpakInstaller | PrivilegedApplications | None = None

    def is_installed(self, spec: InstallSpec) -> bool:
        """Return whether the application's own packages are present."""
        return self._installed.is_installed(spec)

    def can_install(self, spec: InstallSpec) -> bool:
        """Return whether this image can install the application."""
        return spec.method is InstallMethod.FLATPAK or self._broker.supported()

    def can_remove(self, spec: InstallSpec) -> bool:
        """Return whether this image can remove the installed application."""
        if spec.method is InstallMethod.FLATPAK and self._flatpak_scope(spec) == "user":
            return True
        return self._broker.supported()

    def needs_password(self, spec: InstallSpec) -> bool:
        """Only user Flatpak installs on images without Flatpak ask for a password."""
        return (
            spec.method is InstallMethod.FLATPAK
            and not self._broker.supported()
            and FlatpakInstaller.needs_password()
        )

    def install(
        self, identifier: str, spec: InstallSpec,
        on_progress: InstallProgressCallback, password: str | None = None,
    ) -> None:
        """Install through the root service when available, else as a user Flatpak."""
        if self._broker.supported():
            self._start(self._broker)
            try:
                self._broker.install(identifier, on_progress)
            finally:
                self._finish()
            return
        if spec.method is not InstallMethod.FLATPAK or spec.flatpak is None:
            raise PackageOperationError("Installing this application requires a newer PiDeck image")
        installer = self._user_flatpak()
        self._start(installer)
        try:
            installer.install_ref(spec.flatpak, password, on_progress)
        finally:
            self._finish()

    def uninstall(self, identifier: str, spec: InstallSpec, on_progress: InstallProgressCallback) -> None:
        """Remove user Flatpaks directly and everything else through the root service."""
        if spec.method is InstallMethod.FLATPAK and spec.flatpak and self._flatpak_scope(spec) == "user":
            installer = self._user_flatpak()
            self._start(installer)
            try:
                installer.uninstall(spec.flatpak, on_progress)
            finally:
                self._finish()
            return
        if not self._broker.supported():
            raise PackageOperationError("Removing this application requires a newer PiDeck image")
        self._start(self._broker)
        try:
            self._broker.remove(identifier, on_progress)
        finally:
            self._finish()

    def cancel(self) -> None:
        """Forward cancellation to the running operation, if any."""
        with self._lock:
            active = self._active
        if active is not None:
            active.cancel()

    def _flatpak_scope(self, spec: InstallSpec) -> str | None:
        return self._installed.flatpak_scope(spec.flatpak) if spec.flatpak else None

    def _start(self, operation: FlatpakInstaller | PrivilegedApplications) -> None:
        with self._lock:
            if self._active is not None:
                raise PackageOperationError("Another application operation is already running")
            self._active = operation

    def _finish(self) -> None:
        with self._lock:
            self._active = None
