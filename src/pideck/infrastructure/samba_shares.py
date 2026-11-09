"""Export mounted data drives over Samba through the privileged image service."""

from collections.abc import Callable
import logging
import re
import socket
import threading
from typing import Any

from pideck.application.ports.sharing import ShareError
from pideck.application.ports.storage import Drive
from pideck.infrastructure.image_update_client import ImageUpdateError, request as broker_request
from pideck.infrastructure.udisks_storage import DrivePreferences


_LOGGER = logging.getLogger(__name__)
_SHARE_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

Request = Callable[..., dict[str, Any]]


def share_name_for(drive: Drive) -> str:
    """Build a Samba-safe share name from the filesystem label or UUID."""
    label = (drive.filesystem_label or drive.label or "").strip()
    cleaned = _SHARE_UNSAFE.sub("_", label).strip("._-") if label else ""
    if cleaned:
        return cleaned[:64]
    uuid = drive.uuid or "drive"
    compact = re.sub(r"[^A-Za-z0-9]", "", uuid)
    return compact[:8] or "drive"


def smb_url(name: str, host: str | None = None) -> str:
    """Return a guest SMB URL for Linux Mint and other LAN clients."""
    return f"smb://{host or socket.gethostname()}/{name}"


class SambaShareGateway:
    """Remember share preferences and ask the image service to update Samba."""

    def __init__(
        self,
        preferences: DrivePreferences | None = None,
        request: Request = broker_request,
        list_drives: Callable[[], tuple[Drive, ...]] | None = None,
        hostname: Callable[[], str] = socket.gethostname,
    ) -> None:
        """Create a gateway; collaborators are injectable for tests."""
        self._preferences = preferences
        self._request = request
        self._list_drives = list_drives
        self._hostname = hostname
        self._supported: bool | None = None

    def available(self) -> bool:
        """Return whether the running image's service supports drive shares."""
        if not self._supported:
            try:
                self._supported = self._request("capabilities", timeout=5).get("shares") == 1
            except ImageUpdateError as error:
                _LOGGER.info("Privileged drive shares unavailable: %s", error)
                self._supported = False
        return bool(self._supported)

    def is_shared(self, uuid: str | None) -> bool:
        """Return whether the user opted to share ``uuid``."""
        if uuid is None or self._preferences is None:
            return False
        try:
            return uuid in self._preferences.load_shared()
        except Exception as error:
            _LOGGER.warning("Unable to load share preferences: %s", error)
            return False

    def share_name(self, drive: Drive) -> str:
        """Return the Samba share name used for ``drive``."""
        return share_name_for(drive)

    def share(self, drive: Drive) -> str:
        """Export a mounted drive and remember the preference."""
        uuid, path, name = self._validated(drive)
        try:
            self._request("share_enable", uuid=uuid, path=path, name=name)
        except ImageUpdateError as error:
            raise ShareError(str(error)) from error
        self._remember(uuid)
        url = smb_url(name, self._hostname())
        _LOGGER.info("Shared drive uuid=%s url=%s", uuid, url)
        return url

    def unshare(self, drive: Drive) -> None:
        """Stop exporting a drive and clear the remembered preference."""
        self.release(drive)
        if drive.uuid is not None:
            self._forget(drive.uuid)

    def release(self, drive: Drive) -> None:
        """Stop an active Samba export without clearing the preference."""
        if drive.uuid is None:
            return
        if not self.available():
            return
        try:
            self._request("share_disable", uuid=drive.uuid)
        except ImageUpdateError as error:
            raise ShareError(str(error)) from error
        _LOGGER.info("Released Samba share uuid=%s", drive.uuid)

    def restore_shared(self) -> None:
        """Re-export preferred shares for drives that are currently mounted."""
        if self._preferences is None or self._list_drives is None or not self.available():
            return
        try:
            preferred = self._preferences.load_shared()
        except Exception as error:
            _LOGGER.warning("Unable to load shared drives: %s", error)
            return
        if not preferred:
            return
        try:
            drives = self._list_drives()
        except Exception as error:
            _LOGGER.warning("Unable to list drives for share restore: %s", error)
            return
        for drive in drives:
            if drive.uuid not in preferred or not drive.mounted:
                continue
            try:
                self.share(drive)
            except ShareError as error:
                _LOGGER.warning("Unable to restore share uuid=%s error=%s", drive.uuid, error)

    def restore_shared_async(self) -> None:
        """Re-apply preferred shares off the UI thread after mounts restore."""
        if self._preferences is None or not self._preferences.path.exists():
            return

        def restore() -> None:
            try:
                self.restore_shared()
            except ShareError as error:
                _LOGGER.warning("Unable to restore shared drives: %s", error)

        threading.Thread(target=restore, name="pideck-shares-restore", daemon=True).start()

    def ensure_preferred(self, drive: Drive) -> str | None:
        """Re-export ``drive`` when the user previously opted to share it."""
        if not drive.mounted or not self.is_shared(drive.uuid):
            return None
        return self.share(drive)

    def _validated(self, drive: Drive) -> tuple[str, str, str]:
        if not drive.mounted or drive.mountpoint is None:
            raise ShareError("The drive must be mounted before it can be shared")
        if drive.uuid is None:
            raise ShareError("The drive has no filesystem UUID to share")
        if not self.available():
            raise ShareError("Network sharing requires a newer PiDeck image")
        return drive.uuid, drive.mountpoint, share_name_for(drive)

    def _remember(self, uuid: str) -> None:
        if self._preferences is None:
            return
        try:
            self._preferences.remember_shared(uuid)
        except Exception as error:
            _LOGGER.warning("Unable to remember shared drive uuid=%s error=%s", uuid, error)

    def _forget(self, uuid: str) -> None:
        if self._preferences is None:
            return
        try:
            self._preferences.forget_shared(uuid)
        except Exception as error:
            _LOGGER.warning("Unable to forget shared drive uuid=%s error=%s", uuid, error)
