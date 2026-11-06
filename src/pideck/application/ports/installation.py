"""Immutable progress model reported while a catalog application is installed or removed."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable


class InstallStage(StrEnum):
    """Coarse installation phases shown to the user."""

    PREPARING = "preparing"
    INSTALLING_FLATPAK = "installing_flatpak"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    REMOVING = "removing"
    FINISHING = "finishing"


@dataclass(frozen=True, slots=True)
class InstallProgress:
    """One installation progress snapshot.

    ``percent`` is the overall progress (0-100) or ``None`` while it is unknown.
    ``operation`` and ``operations`` describe the current package-manager step.
    """

    stage: InstallStage
    percent: int | None = None
    operation: int | None = None
    operations: int | None = None


InstallProgressCallback = Callable[[InstallProgress], None]
