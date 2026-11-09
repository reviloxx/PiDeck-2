"""Derive the actions offered for one drive row, without Qt."""

from enum import StrEnum

from pideck.application.ports.storage import Drive


class DriveAction(StrEnum):
    """Actions offered in a drive's options menu."""

    MOUNT = "mount"
    UNMOUNT = "unmount"
    SHARE = "share"
    STOP_SHARING = "stop_sharing"


def available_actions(
    drive: Drive,
    *,
    busy: bool = False,
    share_available: bool = False,
    shared: bool = False,
) -> tuple[DriveAction, ...]:
    """Return the actions offered for one drive.

    Share actions require a mounted drive with a filesystem UUID and a
    privilege broker that supports Samba exports. ``shared`` is the user's
    remembered share preference for that UUID.
    """
    if busy:
        return ()
    actions: list[DriveAction] = []
    if drive.mounted:
        actions.append(DriveAction.UNMOUNT)
        if share_available and drive.uuid is not None:
            actions.append(DriveAction.STOP_SHARING if shared else DriveAction.SHARE)
    else:
        actions.append(DriveAction.MOUNT)
    return tuple(actions)
