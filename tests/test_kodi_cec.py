"""Verify Kodi CEC peripherals are disabled while PiDeck owns the adapter."""

from pathlib import Path

from pideck.infrastructure.kodi_cec import disable_kodi_cec

SEED = Path(__file__).resolve().parents[1] / "assets/app-defaults/kodi/cec_CEC_Adapter.xml"


def test_seeds_linux_cec_adapter_when_missing(tmp_path: Path) -> None:
    """First launch creates the Linux CEC peripheral with enabled=0."""
    disable_kodi_cec(home=tmp_path, seed=SEED)
    path = tmp_path / ".kodi/userdata/peripheral_data/cec_CEC_Adapter.xml"
    assert path.is_file()
    assert 'id="enabled" value="0"' in path.read_text(encoding="utf-8")


def test_disables_existing_cec_peripherals(tmp_path: Path) -> None:
    """Enabled CEC adapters are turned off; unrelated peripherals stay untouched."""
    root = tmp_path / ".kodi/userdata/peripheral_data"
    root.mkdir(parents=True)
    cec = root / "cec_CEC_Adapter.xml"
    usb = root / "usb_1234_CEC_Adapter.xml"
    other = root / "joystick.xml"
    cec.write_text(
        '<settings>\n'
        '    <setting id="device_name" value="Kodi" />\n'
        '    <setting id="enabled" value="1" />\n'
        '</settings>\n',
        encoding="utf-8",
    )
    usb.write_text('<settings>\n    <setting id="activate_source" value="1" />\n</settings>\n', encoding="utf-8")
    other.write_text('<settings>\n    <setting id="enabled" value="1" />\n</settings>\n', encoding="utf-8")

    disable_kodi_cec(home=tmp_path, seed=SEED)

    assert 'id="enabled" value="0"' in cec.read_text(encoding="utf-8")
    assert 'id="device_name" value="Kodi"' in cec.read_text(encoding="utf-8")
    assert 'id="enabled" value="0"' in usb.read_text(encoding="utf-8")
    assert 'id="activate_source" value="1"' in usb.read_text(encoding="utf-8")
    assert other.read_text(encoding="utf-8") == (
        '<settings>\n    <setting id="enabled" value="1" />\n</settings>\n'
    )


def test_leaves_already_disabled_peripherals_unchanged(tmp_path: Path) -> None:
    """A peripheral that is already off is not rewritten."""
    root = tmp_path / ".kodi/userdata/peripheral_data"
    root.mkdir(parents=True)
    path = root / "cec_CEC_Adapter.xml"
    original = (
        '<settings>\n'
        '    <setting id="enabled" value="0" />\n'
        '    <setting id="device_name" value="LivingRoom" />\n'
        '</settings>\n'
    )
    path.write_text(original, encoding="utf-8")
    disable_kodi_cec(home=tmp_path, seed=SEED)
    assert path.read_text(encoding="utf-8") == original


def test_bad_xml_does_not_raise(tmp_path: Path) -> None:
    """Corrupt peripheral files are skipped without aborting the launch path."""
    root = tmp_path / ".kodi/userdata/peripheral_data"
    root.mkdir(parents=True)
    (root / "cec_broken.xml").write_text("not xml", encoding="utf-8")
    disable_kodi_cec(home=tmp_path, seed=SEED)
    assert (root / "cec_CEC_Adapter.xml").is_file()
