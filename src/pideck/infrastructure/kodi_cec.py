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
# Nonexistent path so libCEC cannot open /dev/cec0 even if enabled flips back on.
_DISABLED_PORT = "/dev/pideck-cec-disabled"
_FORCED_SETTINGS = (
    ("enabled", "0"),
    ("activate_source", "0"),
    ("port", _DISABLED_PORT),
)
# Editable checkout: <repo>/src/pideck/infrastructure → <repo>/assets
# Installed release: <prefix>/python/pideck/infrastructure → <prefix>/assets
_SEED = Path(__file__).resolve().parents[3] / "assets" / "app-defaults" / "kodi" / _DEFAULT_ADAPTER


def disable_kodi_cec(*, home: Path | None = None, seed: Path | None = None) -> None:
    """Disable Kodi CEC peripherals under ``home`` so PiDeck can keep ``/dev/cec0``.

    Seeds the Linux CEC adapter settings when missing, then forces CEC off and a
    dead ``port`` on every CEC peripheral file. Failures are logged and never raised.
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
                _disable_cec_peripheral(path)
    except OSError as error:
        _LOGGER.warning("Unable to disable Kodi CEC peripherals: %s", error)


def _disable_cec_peripheral(path: Path) -> None:
    """Force CEC-off settings into ``path``, preserving other settings when possible."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        _LOGGER.warning("Unable to read Kodi CEC peripheral %s: %s", path, error)
        return
    try:
        updated = _apply_disabled_settings(text)
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


def _apply_disabled_settings(text: str) -> str:
    """Return peripheral XML with CEC forced off and a nonexistent port."""
    root = ET.fromstring(text)
    if root.tag != "settings":
        raise ET.ParseError(f"expected <settings> root, found <{root.tag}>")
    changed = False
    for setting_id, value in _FORCED_SETTINGS:
        if _set_peripheral_value(root, setting_id, value):
            changed = True
    if not changed:
        return text
    return _serialize(root)


def _set_peripheral_value(root: ET.Element, setting_id: str, value: str) -> bool:
    """Set one peripheral setting using attribute or text-content form. Return if changed."""
    setting = next(
        (
            child
            for child in root
            if child.tag == "setting" and child.attrib.get("id") == setting_id
        ),
        None,
    )
    if setting is None:
        ET.SubElement(root, "setting", {"id": setting_id, "value": value})
        return True
    if "value" in setting.attrib:
        if setting.attrib.get("value") == value and (setting.text is None or not setting.text.strip()):
            return False
        setting.set("value", value)
        setting.text = None
        return True
    if (setting.text or "").strip() == value:
        return False
    setting.text = value
    return True


def _serialize(root: ET.Element) -> str:
    """Serialize peripheral settings with one setting per line."""
    lines = ["<settings>"]
    for child in root:
        if child.tag != "setting":
            continue
        setting_id = child.attrib.get("id")
        if setting_id is None:
            continue
        if "value" in child.attrib:
            attributes = " ".join(f'{key}="{value}"' for key, value in child.attrib.items())
            lines.append(f"    <setting {attributes} />")
            continue
        attributes = [f'id="{setting_id}"']
        for key, item in child.attrib.items():
            if key == "id":
                continue
            attributes.append(f'{key}="{item}"')
        body = child.text if child.text is not None else ""
        lines.append(f'    <setting {" ".join(attributes)}>{body}</setting>')
    lines.append("</settings>")
    lines.append("")
    return "\n".join(lines)
