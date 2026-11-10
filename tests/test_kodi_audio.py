"""Verify Kodi HDMI audio modes for surround PCM and bitstream passthrough."""

from pathlib import Path

from pideck.application.ports.audio import HdmiAudioMode
from pideck.infrastructure.kodi_audio import configure_kodi_audio


def test_surround_mode_sets_51_pcm(tmp_path: Path) -> None:
    """AVR surround mode forces Pulse Default, Fixed 5.1, passthrough off."""
    configure_kodi_audio(home=tmp_path, hdmi_mode=HdmiAudioMode.SURROUND)
    text = (tmp_path / ".kodi/userdata/guisettings.xml").read_text(encoding="utf-8")
    assert '<setting id="audiooutput.audiodevice">PULSE:Default</setting>' in text
    assert '<setting id="audiooutput.config">1</setting>' in text
    assert '<setting id="audiooutput.channels">8</setting>' in text
    assert '<setting id="audiooutput.passthrough">false</setting>' in text


def test_bitstream_mode_sets_passthrough(tmp_path: Path) -> None:
    """ARC bitstream mode forces Fixed 2.0 channels and passthrough codecs."""
    configure_kodi_audio(home=tmp_path, hdmi_mode=HdmiAudioMode.BITSTREAM)
    text = (tmp_path / ".kodi/userdata/guisettings.xml").read_text(encoding="utf-8")
    assert '<setting id="audiooutput.config">1</setting>' in text
    assert '<setting id="audiooutput.channels">1</setting>' in text
    assert '<setting id="audiooutput.passthrough">true</setting>' in text
    assert '<setting id="audiooutput.ac3transcode">true</setting>' in text
    assert '<setting id="videoplayer.usedisplayasclock">false</setting>' in text


def test_switching_modes_rewrites_settings(tmp_path: Path) -> None:
    """Changing HDMI mode replaces the previous Kodi audio configuration."""
    configure_kodi_audio(home=tmp_path, hdmi_mode=HdmiAudioMode.BITSTREAM)
    configure_kodi_audio(home=tmp_path, hdmi_mode=HdmiAudioMode.SURROUND)
    text = (tmp_path / ".kodi/userdata/guisettings.xml").read_text(encoding="utf-8")
    assert '<setting id="audiooutput.channels">8</setting>' in text
    assert '<setting id="audiooutput.passthrough">false</setting>' in text
