"""Export mounted data drives over Samba through the privileged image service."""

from collections.abc import Callable
import logging
from pathlib import Path
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
_DATA_MOUNT_ROOTS = ("/media/", "/mnt/", "/run/media/")

Request = Callable[..., dict[str, Any]]


def export_path_for(mountpoint: str) -> str:
    """Resolve ``mountpoint`` to a real data-drive directory for Samba ``path``."""
    try:
        resolved = Path(mountpoint).resolve(strict=True)
    except OSError as error:
        raise ShareError(f"The mount path is missing: {error}") from error
    if not resolved.is_dir():
        raise ShareError("The mount path is not a directory")
    text = str(resolved)
    if text != "/mnt" and not any(text.startswith(root) for root in _DATA_MOUNT_ROOTS):
        raise ShareError("The mount path must be under a data mount location")
    return text


def share_name_for(drive: Drive) -> str:
    """Build a Samba-safe share name from the UI label or UUID."""
    label = (drive.label or drive.filesystem_label or "").strip()
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
        uuid, _path, name = self._validated(drive)
        self._remember(uuid)
        try:
            self._republish_preferred()
        except ImageUpdateError as error:
            raise ShareError(str(error)) from error
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
            self._republish_preferred()
        except (ShareError, ImageUpdateError) as error:
            _LOGGER.warning("Unable to restore shared drives: %s", error)

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

    def _republish_preferred(self) -> None:
        """Disable preferred shares then re-enable mounted ones with current names.

        Remount order can reshuffle Volume1–N labels while shares.json still holds
        the previous mapping. Freeing every preferred UUID first avoids
        ``Share name is already in use`` when re-applying the current set.
        """
        if self._preferences is None or self._list_drives is None:
            raise ShareError("Drive share restore is not configured")
        if not self.available():
            raise ShareError("Network sharing requires a newer PiDeck image")
        try:
            preferred = self._preferences.load_shared()
            drives = self._list_drives()
        except Exception as error:
            raise ShareError(str(error)) from error
        for uuid in sorted(preferred):
            try:
                self._request("share_disable", uuid=uuid)
            except ImageUpdateError as error:
                _LOGGER.info("Share disable during republish uuid=%s error=%s", uuid, error)
        mounted = [
            drive for drive in drives
            if drive.uuid in preferred and drive.mounted and drive.mountpoint is not None
        ]
        for drive in sorted(mounted, key=lambda item: item.uuid or ""):
            uuid, path, name = self._validated(drive)
            self._request("share_enable", uuid=uuid, path=path, name=name)

    def _validated(self, drive: Drive) -> tuple[str, str, str]:
        if not drive.mounted or drive.mountpoint is None:
            raise ShareError("The drive must be mounted before it can be shared")
        if drive.uuid is None:
            raise ShareError("The drive has no filesystem UUID to share")
        if not self.available():
            raise ShareError("Network sharing requires a newer PiDeck image")
        # Samba rejects share roots that are by-uuid symlinks (BAD_NETWORK_NAME).
        return drive.uuid, export_path_for(drive.mountpoint), share_name_for(drive)

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
