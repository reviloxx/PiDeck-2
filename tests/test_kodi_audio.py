"""Verify Kodi is configured for PulseAudio HDMI ARC passthrough."""

from pathlib import Path

from pideck.infrastructure.kodi_audio import configure_kodi_audio


def test_creates_guisettings_with_passthrough(tmp_path: Path) -> None:
    """First launch creates guisettings for stereo Pulse passthrough."""
    configure_kodi_audio(home=tmp_path)
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    text = path.read_text(encoding="utf-8")
    assert '<settings version="2">' in text
    assert '<setting id="audiooutput.audiodevice">PULSE:Default</setting>' in text
    assert '<setting id="audiooutput.channels">1</setting>' in text
    assert '<setting id="audiooutput.passthrough">true</setting>' in text
    assert '<setting id="audiooutput.passthroughdevice">PULSE:Default</setting>' in text
    assert '<setting id="audiooutput.ac3passthrough">true</setting>' in text
    assert '<setting id="audiooutput.dtspassthrough">true</setting>' in text
    assert '<setting id="audiooutput.eac3passthrough">true</setting>' in text
    assert '<setting id="audiooutput.ac3transcode">true</setting>' in text
    assert '<setting id="audiooutput.truehdpassthrough">false</setting>' in text
    assert '<setting id="audiooutput.dtshdpassthrough">false</setting>' in text
    assert '<setting id="videoplayer.usedisplayasclock">false</setting>' in text


def test_rewrites_pcm_51_settings_to_passthrough(tmp_path: Path) -> None:
    """Previous PCM 5.1 / sync-to-display choices are corrected on each launch."""
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    path.parent.mkdir(parents=True)
    path.write_text(
        '<settings version="2">\n'
        '    <setting id="locale.language">resource.language.de_de</setting>\n'
        '    <setting id="audiooutput.audiodevice" default="true">ALSA:default</setting>\n'
        '    <setting id="audiooutput.channels">8</setting>\n'
        '    <setting id="audiooutput.passthrough">false</setting>\n'
        '    <setting id="videoplayer.usedisplayasclock">true</setting>\n'
        "</settings>\n",
        encoding="utf-8",
    )

    configure_kodi_audio(home=tmp_path)

    text = path.read_text(encoding="utf-8")
    assert '<setting id="locale.language">resource.language.de_de</setting>' in text
    assert '<setting id="audiooutput.audiodevice">PULSE:Default</setting>' in text
    assert '<setting id="audiooutput.channels">1</setting>' in text
    assert '<setting id="audiooutput.passthrough">true</setting>' in text
    assert '<setting id="videoplayer.usedisplayasclock">false</setting>' in text
    assert 'default="true"' not in text
    assert "ALSA:default" not in text


def test_leaves_correct_settings_unchanged(tmp_path: Path) -> None:
    """Already-correct settings are not rewritten."""
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    path.parent.mkdir(parents=True)
    original = (
        '<settings version="2">\n'
        '    <setting id="audiooutput.audiodevice">PULSE:Default</setting>\n'
        '    <setting id="audiooutput.channels">1</setting>\n'
        '    <setting id="audiooutput.passthrough">true</setting>\n'
        '    <setting id="audiooutput.passthroughdevice">PULSE:Default</setting>\n'
        '    <setting id="audiooutput.ac3passthrough">true</setting>\n'
        '    <setting id="audiooutput.dtspassthrough">true</setting>\n'
        '    <setting id="audiooutput.eac3passthrough">true</setting>\n'
        '    <setting id="audiooutput.ac3transcode">true</setting>\n'
        '    <setting id="audiooutput.truehdpassthrough">false</setting>\n'
        '    <setting id="audiooutput.dtshdpassthrough">false</setting>\n'
        '    <setting id="videoplayer.usedisplayasclock">false</setting>\n'
        "</settings>\n"
    )
    path.write_text(original, encoding="utf-8")
    configure_kodi_audio(home=tmp_path)
    assert path.read_text(encoding="utf-8") == original
