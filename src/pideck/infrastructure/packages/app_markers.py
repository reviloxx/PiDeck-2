"""Per-catalog-app install markers so shared packages do not alias install state.

Chromium and YouTube TV both need the Chromium apt packages. Markers under
``~/.config/pideck/installed/`` record which catalog apps the user installed,
so installing one does not make the other appear installed.
"""

from __future__ import annotations

import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# Apps that share a Debian package set and therefore need distinct markers.
SHARED_CHROMIUM_APP_IDS = frozenset({"chromium", "youtube-tv"})


class AppInstallMarkers:
    """Create, query and clear install markers for catalog application ids."""

    def __init__(self, root: Path | None = None) -> None:
        """Store markers under ``root`` (default: ``~/.config/pideck/installed``)."""
        self._root = root or Path.home() / ".config" / "pideck" / "installed"

    def is_marked(self, identifier: str) -> bool:
        """Return whether ``identifier`` has an install marker."""
        return self._path(identifier).is_file()

    def mark(self, identifier: str) -> None:
        """Record that ``identifier`` was installed."""
        path = self._path(identifier)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{identifier}\n", encoding="utf-8")
        except OSError as error:
            _LOGGER.warning("Unable to write install marker for %s: %s", identifier, error)

    def unmark(self, identifier: str) -> None:
        """Clear the install marker for ``identifier``."""
        path = self._path(identifier)
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            _LOGGER.warning("Unable to remove install marker for %s: %s", identifier, error)

    def any_marked(self, identifiers: frozenset[str]) -> bool:
        """Return whether any of ``identifiers`` still has a marker."""
        return any(self.is_marked(identifier) for identifier in identifiers)

    def _path(self, identifier: str) -> Path:
        return self._root / identifier
