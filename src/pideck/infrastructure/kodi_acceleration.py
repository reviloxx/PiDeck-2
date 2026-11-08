"""Select VDPAU for Kodi on NVIDIA so browser VA-API env does not force software decode."""

from __future__ import annotations

import logging
from pathlib import Path
from collections.abc import Callable
import xml.etree.ElementTree as ET

_LOGGER = logging.getLogger(__name__)
_NVIDIA_VERSION = Path("/proc/driver/nvidia/version")
_GUISETTINGS = Path(".kodi") / "userdata" / "guisettings.xml"
_SETTINGS = (
    ("videoplayer.usevaapi", "false"),
    ("videoplayer.usevdpau", "true"),
)


def configure_kodi_acceleration(
    *,
    home: Path | None = None,
    nvidia_present: Callable[[], bool] | None = None,
) -> None:
    """Force VDPAU and disable VA-API in Kodi guisettings when NVIDIA is loaded.

    The session exports ``LIBVA_DRIVER_NAME=nvidia`` for Firefox and Chromium.
    Kodi inherits that and prefers VA-API, which is the wrong decode path on
    this image. Raspberry Pi, Intel and AMD are left untouched. Failures are
    logged and never raised.
    """
    if not (nvidia_present or _nvidia_driver_loaded)():
        return
    path = (home or Path.home()) / _GUISETTINGS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            text = path.read_text(encoding="utf-8")
        else:
            text = '<settings version="2">\n</settings>\n'
        updated = _apply_acceleration_settings(text)
        if updated == text and path.exists():
            return
        path.write_text(updated, encoding="utf-8")
    except OSError as error:
        _LOGGER.warning("Unable to configure Kodi acceleration at %s: %s", path, error)
        return
    except ET.ParseError as error:
        _LOGGER.warning("Unable to parse Kodi guisettings %s: %s", path, error)
        return
    _LOGGER.info("Configured Kodi NVIDIA acceleration at %s", path)


def _nvidia_driver_loaded() -> bool:
    """Return whether the proprietary NVIDIA kernel driver is present."""
    return _NVIDIA_VERSION.is_file()


def _apply_acceleration_settings(text: str) -> str:
    """Return guisettings XML with VA-API off and VDPAU on."""
    root = ET.fromstring(text)
    if root.tag != "settings":
        raise ET.ParseError(f"expected <settings> root, found <{root.tag}>")
    changed = False
    for setting_id, value in _SETTINGS:
        setting = next(
            (
                child
                for child in root
                if child.tag == "setting" and child.attrib.get("id") == setting_id
            ),
            None,
        )
        if setting is None:
            setting = ET.SubElement(root, "setting", {"id": setting_id})
            setting.text = value
            changed = True
        elif (setting.text or "").strip() != value:
            setting.text = value
            setting.attrib.pop("default", None)
            changed = True
    if not changed:
        return text
    return _serialize(root)


def _serialize(root: ET.Element) -> str:
    """Serialize guisettings with one setting per line and text content values."""
    version = root.attrib.get("version")
    opening = f'<settings version="{version}">' if version is not None else "<settings>"
    lines = [opening]
    for child in root:
        if child.tag != "setting":
            continue
        setting_id = child.attrib.get("id")
        if setting_id is None:
            continue
        attributes = [f'id="{setting_id}"']
        for key, value in child.attrib.items():
            if key == "id":
                continue
            attributes.append(f'{key}="{value}"')
        body = child.text if child.text is not None else ""
        lines.append(f'    <setting {" ".join(attributes)}>{body}</setting>')
    lines.append("</settings>")
    lines.append("")
    return "\n".join(lines)
