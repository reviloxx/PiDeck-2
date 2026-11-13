"""Set Nintendo Switch Pro Controller player LEDs over hidraw.

``hid-generic`` (used because ``hid-nintendo`` probe fails on CSR reconnect)
never sends the player-light subcommand, so the pad keeps its connecting
animation. One output report with subcommand ``0x30`` and mask ``0x01`` lights
player 1 and stops the animation.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# Bluetooth Nintendo Co., Ltd. Pro Controller (bus 0005, VID 057E, PID 2009).
_PRO_CONTROLLER_HID_IDS = frozenset(
    {
        "0005:0000057E:00002009",
        "0003:0000057E:00002009",  # USB wired
    }
)
# output_id=0x01, packet_num, neutral rumble (8 bytes), subcmd=0x30, lights.
_PLAYER_LED_REPORT_PREFIX = bytes(
    [0x01, 0x00, 0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40, 0x30]
)
_PLAYER_LED_MASKS = (0x01, 0x02, 0x04, 0x08)


def find_switch_pro_hidraw(*, address: str = "", sysfs_hidraw: Path | None = None) -> Path | None:
    """Return the hidraw node for a Switch Pro Controller, preferably by MAC."""
    root = sysfs_hidraw or Path("/sys/class/hidraw")
    normalized = address.strip().upper().replace("-", ":")
    fallback: Path | None = None
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        uevent = entry / "device" / "uevent"
        try:
            text = uevent.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hid_id = ""
        uniq = ""
        for line in text.splitlines():
            if line.startswith("HID_ID="):
                hid_id = line.partition("=")[2].strip().upper()
            elif line.startswith("HID_UNIQ="):
                uniq = line.partition("=")[2].strip().upper().replace("-", ":")
        if hid_id not in _PRO_CONTROLLER_HID_IDS:
            continue
        node = Path("/dev") / entry.name
        if normalized and uniq == normalized:
            return node
        if fallback is None:
            fallback = node
    return fallback


def set_switch_pro_player_led(
    hidraw_path: Path,
    *,
    player: int = 1,
) -> bool:
    """Write the player-light subcommand; return whether the write succeeded."""
    if player < 1 or player > 4:
        raise ValueError("player must be 1-4")
    report = _PLAYER_LED_REPORT_PREFIX + bytes([_PLAYER_LED_MASKS[player - 1]])
    try:
        fd = os.open(hidraw_path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as error:
        _LOGGER.debug("Unable to open Switch Pro hidraw path=%s error=%s", hidraw_path, error)
        return False
    try:
        written = os.write(fd, report)
    except OSError as error:
        _LOGGER.debug("Unable to set Switch Pro LEDs path=%s error=%s", hidraw_path, error)
        return False
    finally:
        os.close(fd)
    if written < len(report):
        _LOGGER.debug(
            "Short Switch Pro LED write path=%s written=%s expected=%s",
            hidraw_path,
            written,
            len(report),
        )
        return False
    _LOGGER.info("Switch Pro player LED set path=%s player=%s", hidraw_path, player)
    return True


def apply_switch_pro_player_led(*, address: str = "", player: int = 1) -> bool:
    """Find a Pro Controller hidraw and set its player LED."""
    path = find_switch_pro_hidraw(address=address)
    if path is None:
        _LOGGER.debug("No Switch Pro hidraw found address=%s", address or "-")
        return False
    return set_switch_pro_player_led(path, player=player)
