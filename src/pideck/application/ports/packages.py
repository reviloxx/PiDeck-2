"""Port for installing and removing catalog applications on the device."""

from typing import Protocol

from pideck.application.ports.installation import InstallProgressCallback
from pideck.domain.catalog import InstallSpec


class PackageOperationError(Exception):
    """Report a failed application installation or removal."""


class PackageOperationCancelled(PackageOperationError):
    """Report an operation stopped at the user's request."""


class ApplicationPackageManager(Protocol):
    """Install, detect and remove application packages for one device."""

    def is_installed(self, spec: InstallSpec) -> bool:
        """Return whether the application's packages are present."""
        ...

    def can_install(self, spec: InstallSpec) -> bool:
        """Return whether this image can install the application."""
        ...

    def can_remove(self, spec: InstallSpec) -> bool:
        """Return whether this image can remove the installed application."""
        ...

    def needs_password(self, spec: InstallSpec) -> bool:
        """Return whether installing requires an administrator password first."""
        ...

    def install(
        self, identifier: str, spec: InstallSpec,
        on_progress: InstallProgressCallback, password: str | None = None,
    ) -> None:
        """Install the application; blocks and is called from a worker thread."""
        ...

    def uninstall(self, identifier: str, spec: InstallSpec, on_progress: InstallProgressCallback) -> None:
        """Remove the application but keep user data; blocks in a worker thread."""
        ...

    def cancel(self) -> None:
        """Request cancellation; raises ``PermissionError`` if it cannot stop now."""
        ...
