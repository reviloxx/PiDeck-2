"""Copy catalog first-run files into the user's home before an application starts.

Files are only created when absent, so the application's own settings, and
every choice the user made in it, always win over PiDeck's defaults.
"""

import logging
from pathlib import Path
import shutil

from pideck.domain.catalog import CatalogEntry

_LOGGER = logging.getLogger(__name__)


class UserDefaultsInstaller:
    """Seed missing per-application configuration files in one home directory."""

    def __init__(self, home: Path | None = None) -> None:
        """Use the current user's home unless another directory is injected."""
        self._home = home or Path.home()

    def apply(self, entry: CatalogEntry) -> None:
        """Create each missing default file; failures are logged and never block a launch."""
        for default in entry.user_defaults:
            target = self._home.joinpath(*default.target.parts)
            if target.exists() or target.is_symlink():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(default.source, target)
            except OSError as error:
                _LOGGER.warning("Unable to create default %s for %s: %s", target, entry.identifier, error)
                continue
            _LOGGER.info("Created first-run default %s for %s", target, entry.identifier)
