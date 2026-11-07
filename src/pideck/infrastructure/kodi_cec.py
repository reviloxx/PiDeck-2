"""Keep Kodi from opening the HDMI-CEC adapter while PiDeck owns it."""

from __future__ import annotations

import logging
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

_LOGGER = logging.getLogger(__name__)
_CEC_NAME = re.compile(r"cec", re.IGNORECASE)
_DEFAULT_ADAPTER = "cec_CEC_Adapter.xml"
# Editable checkout: <repo>/src/pideck/infrastructure → <repo>/assets
# Installed release: <prefix>/python/pideck/infrastructure → <prefix>/assets
_SEED = Path(__file__).resolve().parents[3] / "assets" / "app-defaults" / "kodi" / _DEFAULT_ADAPTER


def disable_kodi_cec(*, home: Path | None = None, seed: Path | None = None) -> None:
    """Disable Kodi CEC peripherals under ``home`` so PiDeck can keep ``/dev/cec0``.

    Seeds the Linux CEC adapter settings when missing, then forces ``enabled=0``
    on every CEC peripheral file. Failures are logged and never raised.
    """
    root = (home or Path.home()) / ".kodi" / "userdata" / "peripheral_data"
    try:
        root.mkdir(parents=True, exist_ok=True)
        default = root / _DEFAULT_ADAPTER
        if not default.exists() and not default.is_symlink():
            source = seed or _SEED
            shutil.copyfile(source, default)
            _LOGGER.info("Created Kodi CEC peripheral defaults at %s", default)
        for path in sorted(root.iterdir()):
            if path.is_file() and _CEC_NAME.search(path.name) and path.suffix.lower() == ".xml":
                _set_enabled_disabled(path)
    except OSError as error:
        _LOGGER.warning("Unable to disable Kodi CEC peripherals: %s", error)


def _set_enabled_disabled(path: Path) -> None:
    """Write ``enabled=0`` into ``path``, preserving other settings when possible."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        _LOGGER.warning("Unable to read Kodi CEC peripheral %s: %s", path, error)
        return
    try:
        updated = _disable_enabled_setting(text)
    except ET.ParseError as error:
        _LOGGER.warning("Unable to parse Kodi CEC peripheral %s: %s", path, error)
        return
    if updated == text:
        return
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as error:
        _LOGGER.warning("Unable to update Kodi CEC peripheral %s: %s", path, error)
        return
    _LOGGER.info("Disabled Kodi CEC peripheral %s", path)


def _disable_enabled_setting(text: str) -> str:
    """Return XML with the CEC ``enabled`` setting forced to ``0``."""
    root = ET.fromstring(text)
    if root.tag != "settings":
        raise ET.ParseError(f"expected <settings> root, found <{root.tag}>")
    enabled = next(
        (
            child for child in root
            if child.tag == "setting" and child.attrib.get("id") == "enabled"
        ),
        None,
    )
    if enabled is None:
        ET.SubElement(root, "setting", {"id": "enabled", "value": "0"})
    elif enabled.attrib.get("value") == "0":
        return text
    else:
        enabled.set("value", "0")
    return _serialize(root)


def _serialize(root: ET.Element) -> str:
    """Serialize peripheral settings with one setting per line."""
    lines = ["<settings>"]
    for child in root:
        if child.tag != "setting":
            continue
        attributes = " ".join(f'{key}="{value}"' for key, value in child.attrib.items())
        lines.append(f"    <setting {attributes} />")
    lines.append("</settings>")
    lines.append("")
    return "\n".join(lines)
