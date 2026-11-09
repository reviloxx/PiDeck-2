"""Port for exporting mounted data drives on the local network.

The presentation layer only sees share preferences and SMB URLs; the platform
adapter talks to the privileged image service that writes Samba configuration.
"""

from typing import Protocol

from pideck.application.ports.storage import Drive


class ShareError(Exception):
    """Report an unavailable share service or a rejected share operation."""


class ShareGateway(Protocol):
    """Inspect and change LAN exports for mounted data drives."""

    def available(self) -> bool:
        """Return whether this image can export drives over SMB."""
        ...

    def is_shared(self, uuid: str | None) -> bool:
        """Return whether the user opted to share the filesystem ``uuid``."""
        ...

    def share_name(self, drive: Drive) -> str:
        """Return the Samba share name that would be used for ``drive``."""
        ...

    def share(self, drive: Drive) -> str:
        """Export ``drive`` and return an ``smb://`` URL for LAN clients."""
        ...

    def unshare(self, drive: Drive) -> None:
        """Stop exporting ``drive`` and clear the remembered share preference."""
        ...

    def release(self, drive: Drive) -> None:
        """Stop an active export without clearing the share preference."""
        ...

    def restore_shared(self) -> None:
        """Re-export preferred shares for drives that are currently mounted."""
        ...

    def ensure_preferred(self, drive: Drive) -> str | None:
        """Re-export ``drive`` when previously shared; return an SMB URL or None."""
        ...
