"""Configure Kodi for PulseAudio HDMI bitstream passthrough (TV ARC)."""

from __future__ import annotations

import logging
from pathlib import Path
import xml.etree.ElementTree as ET

_LOGGER = logging.getLogger(__name__)
_GUISETTINGS = Path(".kodi") / "userdata" / "guisettings.xml"
_SETTINGS = {
    "audiooutput.audiodevice": "PULSE:Default",
    # 2.0 channels — required for PulseAudio passthrough mode.
    "audiooutput.channels": "1",
    "audiooutput.passthrough": "true",
    "audiooutput.passthroughdevice": "PULSE:Default",
    "audiooutput.ac3passthrough": "true",
    "audiooutput.dtspassthrough": "true",
    "audiooutput.eac3passthrough": "true",
    "audiooutput.ac3transcode": "true",
    "audiooutput.truehdpassthrough": "false",
    "audiooutput.dtshdpassthrough": "false",
    # Sync-to-display silently disables passthrough.
    "videoplayer.usedisplayasclock": "false",
}


def configure_kodi_audio(*, home: Path | None = None) -> None:
    """Force Kodi onto Pulse Default with ARC-friendly bitstream passthrough.

    Failures are logged and never raised.
    """
    path = (home or Path.home()) / _GUISETTINGS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            text = path.read_text(encoding="utf-8")
        else:
            text = '<settings version="2">\n</settings>\n'
        updated = _apply_audio_settings(text)
        if updated == text and path.exists():
            return
        path.write_text(updated, encoding="utf-8")
    except OSError as error:
        _LOGGER.warning("Unable to configure Kodi audio at %s: %s", path, error)
        return
    except ET.ParseError as error:
        _LOGGER.warning("Unable to parse Kodi guisettings %s: %s", path, error)
        return
    _LOGGER.info("Configured Kodi HDMI ARC passthrough settings at %s", path)


def _apply_audio_settings(text: str) -> str:
    """Return guisettings XML with PiDeck Pulse passthrough values."""
    root = ET.fromstring(text)
    if root.tag != "settings":
        raise ET.ParseError(f"expected <settings> root, found <{root.tag}>")
    changed = False
    for setting_id, value in _SETTINGS.items():
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
