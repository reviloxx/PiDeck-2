"""Verify Kodi stays on PulseAudio Default for PULSE_SINK routing."""

from pathlib import Path

from pideck.infrastructure.kodi_audio import configure_kodi_audio


def test_creates_guisettings_with_pulse_default(tmp_path: Path) -> None:
    """First launch creates guisettings with PulseAudio Default selected."""
    configure_kodi_audio(home=tmp_path)
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    text = path.read_text(encoding="utf-8")
    assert '<settings version="2">' in text
    assert '<setting id="audiooutput.audiodevice">PULSE:Default</setting>' in text


def test_rewrites_alsa_device_to_pulse_default(tmp_path: Path) -> None:
    """A previous ALSA device selection is corrected on each launch."""
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    path.parent.mkdir(parents=True)
    path.write_text(
        '<settings version="2">\n'
        '    <setting id="locale.language">resource.language.de_de</setting>\n'
        '    <setting id="audiooutput.audiodevice" default="true">ALSA:default</setting>\n'
        "</settings>\n",
        encoding="utf-8",
    )

    configure_kodi_audio(home=tmp_path)

    text = path.read_text(encoding="utf-8")
    assert '<setting id="locale.language">resource.language.de_de</setting>' in text
    assert '<setting id="audiooutput.audiodevice">PULSE:Default</setting>' in text
    assert 'default="true"' not in text
    assert "ALSA:default" not in text


def test_leaves_pulse_default_unchanged(tmp_path: Path) -> None:
    """Already-correct settings are not rewritten."""
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    path.parent.mkdir(parents=True)
    original = (
        '<settings version="2">\n'
        '    <setting id="audiooutput.audiodevice">PULSE:Default</setting>\n'
        "</settings>\n"
    )
    path.write_text(original, encoding="utf-8")
    configure_kodi_audio(home=tmp_path)
    assert path.read_text(encoding="utf-8") == original
