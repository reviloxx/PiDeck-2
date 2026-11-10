"""Configure Kodi audio for HDMI surround PCM or bitstream passthrough."""

from __future__ import annotations

import logging
from pathlib import Path
import xml.etree.ElementTree as ET

from pideck.application.ports.audio import AppPcmLayout, HdmiAudioMode

_LOGGER = logging.getLogger(__name__)
_GUISETTINGS = Path(".kodi") / "userdata" / "guisettings.xml"

_SURROUND_SETTINGS = {
    "audiooutput.audiodevice": "PULSE:Default",
    # Kodi Omega option value for 5.1 (1=2.0 … 8=5.1).
    "audiooutput.channels": "8",
    "audiooutput.passthrough": "false",
    "audiooutput.ac3passthrough": "false",
    "audiooutput.dtspassthrough": "false",
    "audiooutput.eac3passthrough": "false",
    "audiooutput.ac3transcode": "false",
    "audiooutput.truehdpassthrough": "false",
    "audiooutput.dtshdpassthrough": "false",
}

_PCM_21_SETTINGS = {
    "audiooutput.audiodevice": "PULSE:Default",
    # Kodi Omega option value for 2.1 (1=2.0, 2=2.1 … 8=5.1).
    "audiooutput.channels": "2",
    "audiooutput.passthrough": "false",
    "audiooutput.ac3passthrough": "false",
    "audiooutput.dtspassthrough": "false",
    "audiooutput.eac3passthrough": "false",
    "audiooutput.ac3transcode": "false",
    "audiooutput.truehdpassthrough": "false",
    "audiooutput.dtshdpassthrough": "false",
}

_BITSTREAM_SETTINGS = {
    "audiooutput.audiodevice": "PULSE:Default",
    "audiooutput.channels": "1",
    "audiooutput.passthrough": "true",
    "audiooutput.passthroughdevice": "PULSE:Default",
    "audiooutput.ac3passthrough": "true",
    "audiooutput.dtspassthrough": "true",
    "audiooutput.eac3passthrough": "true",
    "audiooutput.ac3transcode": "true",
    "audiooutput.truehdpassthrough": "false",
    "audiooutput.dtshdpassthrough": "false",
    "videoplayer.usedisplayasclock": "false",
}


def configure_kodi_audio(
    *,
    home: Path | None = None,
    hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND,
    pcm_layout: AppPcmLayout | None = None,
) -> None:
    """Apply Kodi HDMI settings for PCM layout, surround, or ARC bitstream.

    Failures are logged and never raised.
    """
    if pcm_layout is AppPcmLayout.PCM_21:
        settings = _PCM_21_SETTINGS
        label = pcm_layout.value
    elif pcm_layout is AppPcmLayout.PCM_51:
        settings = _SURROUND_SETTINGS
        label = pcm_layout.value
    elif hdmi_mode is HdmiAudioMode.BITSTREAM:
        settings = _BITSTREAM_SETTINGS
        label = hdmi_mode.value
    else:
        settings = _SURROUND_SETTINGS
        label = hdmi_mode.value
    path = (home or Path.home()) / _GUISETTINGS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            text = path.read_text(encoding="utf-8")
        else:
            text = '<settings version="2">\n</settings>\n'
        updated = _apply_settings(text, settings)
        if updated == text and path.exists():
            return
        path.write_text(updated, encoding="utf-8")
    except OSError as error:
        _LOGGER.warning("Unable to configure Kodi audio at %s: %s", path, error)
        return
    except ET.ParseError as error:
        _LOGGER.warning("Unable to parse Kodi guisettings %s: %s", path, error)
        return
    _LOGGER.info("Configured Kodi HDMI %s settings at %s", label, path)


def _apply_settings(text: str, settings: dict[str, str]) -> str:
    """Return guisettings XML with the given setting values applied."""
    root = ET.fromstring(text)
    if root.tag != "settings":
        raise ET.ParseError(f"expected <settings> root, found <{root.tag}>")
    changed = False
    for setting_id, value in settings.items():
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
