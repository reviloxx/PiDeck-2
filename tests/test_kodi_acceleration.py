"""Verify Kodi selects VDPAU on NVIDIA and leaves other platforms alone."""

from pathlib import Path

from pideck.infrastructure.kodi_acceleration import configure_kodi_acceleration


def test_creates_guisettings_on_nvidia_when_missing(tmp_path: Path) -> None:
    """First NVIDIA launch creates guisettings with VA-API off and VDPAU on."""
    configure_kodi_acceleration(home=tmp_path, nvidia_present=lambda: True)
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    text = path.read_text(encoding="utf-8")
    assert '<settings version="2">' in text
    assert '<setting id="videoplayer.usevaapi">false</setting>' in text
    assert '<setting id="videoplayer.usevdpau">true</setting>' in text


def test_updates_existing_locale_guisettings_on_nvidia(tmp_path: Path) -> None:
    """Locale seed values are kept while acceleration keys are added."""
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    path.parent.mkdir(parents=True)
    path.write_text(
        '<settings version="2">\n'
        '    <setting id="locale.language">resource.language.de_de</setting>\n'
        "</settings>\n",
        encoding="utf-8",
    )

    configure_kodi_acceleration(home=tmp_path, nvidia_present=lambda: True)

    text = path.read_text(encoding="utf-8")
    assert '<setting id="locale.language">resource.language.de_de</setting>' in text
    assert '<setting id="videoplayer.usevaapi">false</setting>' in text
    assert '<setting id="videoplayer.usevdpau">true</setting>' in text


def test_rewrites_wrong_acceleration_values_every_launch(tmp_path: Path) -> None:
    """Wrong decoder flags are corrected on each NVIDIA launch."""
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    path.parent.mkdir(parents=True)
    path.write_text(
        '<settings version="2">\n'
        '    <setting id="videoplayer.usevaapi" default="true">true</setting>\n'
        '    <setting id="videoplayer.usevdpau">false</setting>\n'
        "</settings>\n",
        encoding="utf-8",
    )

    configure_kodi_acceleration(home=tmp_path, nvidia_present=lambda: True)

    text = path.read_text(encoding="utf-8")
    assert '<setting id="videoplayer.usevaapi">false</setting>' in text
    assert '<setting id="videoplayer.usevdpau">true</setting>' in text
    assert 'default="true"' not in text


def test_leaves_guisettings_untouched_without_nvidia(tmp_path: Path) -> None:
    """Raspberry Pi / Intel / AMD keep existing Kodi settings."""
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    path.parent.mkdir(parents=True)
    original = (
        '<settings version="2">\n'
        '    <setting id="videoplayer.usevaapi">true</setting>\n'
        '    <setting id="videoplayer.usevdpau">false</setting>\n'
        "</settings>\n"
    )
    path.write_text(original, encoding="utf-8")

    configure_kodi_acceleration(home=tmp_path, nvidia_present=lambda: False)

    assert path.read_text(encoding="utf-8") == original
    assert not (tmp_path / ".kodi/userdata/guisettings.xml.bak").exists()


def test_does_not_create_guisettings_without_nvidia(tmp_path: Path) -> None:
    """Non-NVIDIA launches never create a guisettings file."""
    configure_kodi_acceleration(home=tmp_path, nvidia_present=lambda: False)
    assert not (tmp_path / ".kodi/userdata/guisettings.xml").exists()


def test_corrupt_guisettings_does_not_raise(tmp_path: Path) -> None:
    """Corrupt guisettings are skipped without aborting the launch path."""
    path = tmp_path / ".kodi/userdata/guisettings.xml"
    path.parent.mkdir(parents=True)
    path.write_text("not xml", encoding="utf-8")
    configure_kodi_acceleration(home=tmp_path, nvidia_present=lambda: True)
    assert path.read_text(encoding="utf-8") == "not xml"
