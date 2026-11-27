"""Extract and locate the shipped uBlock Origin Lite extension for YouTube TV."""

from __future__ import annotations

import logging
from pathlib import Path
import zipfile

_LOGGER = logging.getLogger(__name__)

_ZIP_NAME = "ublock-origin-lite.chromium.zip"
_VERSION_NAME = "ublock-origin-lite.version"
_EXTRACTED_NAME = "ublock-origin-lite"


def shipped_extension_zip(asset_root: Path | None = None) -> Path | None:
    """Return the release-shipped Chromium zip, if present."""
    for candidate in _asset_roots(asset_root):
        path = candidate / "extensions" / _ZIP_NAME
        if path.is_file():
            return path
    return None


def ensure_ublock_origin_lite(asset_root: Path | None = None, home: Path | None = None) -> Path | None:
    """Extract the shipped zip into the user config tree and return that directory.

    Returns ``None`` when the zip is missing or extraction fails; callers should
    still launch YouTube TV without the extension.
    """
    zip_path = shipped_extension_zip(asset_root)
    if zip_path is None:
        _LOGGER.warning("uBlock Origin Lite zip is not available in the release assets")
        return None
    version = _shipped_version(zip_path.parent)
    destination = (home or Path.home()) / ".config" / "pideck" / "extensions" / _EXTRACTED_NAME
    stamp = destination / ".pideck-version"
    if (destination / "manifest.json").is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == version:
        return destination
    try:
        if destination.exists():
            # Replace an older extract in place.
            for path in sorted(destination.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(destination)
        stamp.write_text(f"{version}\n", encoding="utf-8")
    except (OSError, zipfile.BadZipFile) as error:
        _LOGGER.warning("Unable to extract uBlock Origin Lite: %s", error)
        return None
    if not (destination / "manifest.json").is_file():
        _LOGGER.warning("uBlock Origin Lite extract is missing manifest.json")
        return None
    _LOGGER.info("Prepared uBlock Origin Lite at %s", destination)
    return destination


def _shipped_version(extensions_dir: Path) -> str:
    version_path = extensions_dir / _VERSION_NAME
    try:
        return version_path.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def _asset_roots(asset_root: Path | None) -> tuple[Path, ...]:
    roots: list[Path] = []
    if asset_root is not None:
        roots.append(asset_root)
        roots.append(asset_root.parent / "assets")
    roots.append(Path("/usr/lib/pideck/current/assets"))
    # Developer checkout: …/src/pideck/infrastructure → repo assets/
    roots.append(Path(__file__).resolve().parents[3] / "assets")
    return tuple(roots)
