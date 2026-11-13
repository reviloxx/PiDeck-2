"""Tests for Switch Pro player LED hidraw reports."""

from pathlib import Path

from pideck.infrastructure.switch_pro_leds import (
    find_switch_pro_hidraw,
    set_switch_pro_player_led,
)


def test_find_switch_pro_hidraw_prefers_matching_mac(tmp_path: Path) -> None:
    """Match Bluetooth Pro Controllers by HID_ID and prefer the MAC uniq."""
    first = tmp_path / "hidraw3"
    second = tmp_path / "hidraw4"
    for entry, uniq in (
        (first, "AA:BB:CC:DD:EE:FF"),
        (second, "98:B6:E9:B2:4F:2C"),
    ):
        device = entry / "device"
        device.mkdir(parents=True)
        (device / "uevent").write_text(
            "HID_ID=0005:0000057E:00002009\n"
            f"HID_UNIQ={uniq}\n",
            encoding="utf-8",
        )
    found = find_switch_pro_hidraw(address="98:b6:e9:b2:4f:2c", sysfs_hidraw=tmp_path)
    assert found == Path("/dev/hidraw4")


def test_set_switch_pro_player_led_writes_report(tmp_path: Path) -> None:
    """Player 1 uses subcommand 0x30 with light mask 0x01."""
    node = tmp_path / "hidraw9"
    node.write_bytes(b"")
    assert set_switch_pro_player_led(node, player=1) is True
    payload = node.read_bytes()
    assert payload[:11] == bytes(
        [0x01, 0x00, 0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40, 0x30]
    )
    assert payload[11] == 0x01
