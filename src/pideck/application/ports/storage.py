"""Port for listing, mounting and unmounting data drives.

The presentation layer only sees ``Drive`` snapshots and the ``StorageGateway``
protocol; the platform adapter decides how block devices are discovered and
which privileged service performs the mount.
"""

from dataclasses import dataclass
from typing import Protocol


class StorageError(Exception):
    """Report an unavailable storage service or a rejected drive operation."""


@dataclass(frozen=True, slots=True)
class Drive:
    """Describe one mountable filesystem that is not part of the system disk."""

    device: str
    label: str
    filesystem: str
    size: int
    mountpoint: str | None
    removable: bool
    parent: str | None = None
    uuid: str | None = None

    @property
    def mounted(self) -> bool:
        """Return whether the filesystem is currently mounted."""
        return self.mountpoint is not None


class StorageGateway(Protocol):
    """Inspect and change drive mounts through an injected platform adapter."""

    def available(self) -> bool:
        """Return whether this system can mount drives for the current user."""
        ...

    def list_drives(self) -> tuple[Drive, ...]:
        """Return mountable data drives, excluding the system disk."""
        ...

    def mount(self, drive: Drive) -> str:
        """Mount ``drive`` and return its mount point."""
        ...

    def unmount(self, drive: Drive) -> None:
        """Unmount ``drive`` and power off its disk when it is removable."""
        ...
