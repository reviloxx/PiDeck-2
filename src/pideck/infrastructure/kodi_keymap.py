"""Install PiDeck's Kodi keymap so Back stops fullscreen playback."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

_LOGGER = logging.getLogger(__name__)
_KEYMAP_NAME = "pideck.xml"
# Editable checkout: <repo>/src/pideck/infrastructure → <repo>/assets
# Installed release: <prefix>/python/pideck/infrastructure → <prefix>/assets
_SEED = Path(__file__).resolve().parents[3] / "assets" / "app-defaults" / "kodi" / _KEYMAP_NAME


def configure_kodi_keymap(*, home: Path | None = None, seed: Path | None = None) -> None:
    """Write ``keymaps/pideck.xml`` under ``home`` so Escape stops fullscreen media.

    Overwrites PiDeck's managed keymap on every launch so updates apply. Other
    files in ``keymaps`` are left alone. Failures are logged and never raised.
    """
    source = seed or _SEED
    destination = (home or Path.home()) / ".kodi" / "userdata" / "keymaps" / _KEYMAP_NAME
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    except OSError as error:
        _LOGGER.warning("Unable to install Kodi keymap %s: %s", destination, error)
        return
    _LOGGER.info("Installed Kodi keymap at %s", destination)
