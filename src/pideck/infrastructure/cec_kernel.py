"""Prepare libCEC's Linux-kernel adapter without persisting USB settings."""

import fcntl
import os
from pathlib import Path
import struct

from pideck.domain.input_settings import CecSettings


_CEC_ADAP_G_CAPS = 0xC04C6100
_CEC_ADAP_G_PHYS_ADDR = 0x80026101
_CEC_ADAP_S_PHYS_ADDR = 0x40026102
_CEC_CAP_PHYS_ADDR = 1
_CEC_REQUIRED_CAPABILITIES = 2 | 4 | 8
_CEC_LINUX_DEVICE = Path("/dev/cec0")
_DRM = Path("/sys/class/drm")
_PULSE8_PERSISTENT = Path("/sys/module/pulse8_cec/parameters/persistent_config")


class CecKernelError(Exception):
    """Report an unavailable or incorrectly configured kernel CEC adapter."""


def _edid_physical_address(edid: bytes) -> int | None:
    """Extract the HDMI vendor block's physical address from an EDID."""
    if (
        len(edid) < 128
        or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00"
        or sum(edid[:128]) % 256
    ):
        return None
    for index in range(1, min(edid[126] + 1, len(edid) // 128)):
        block = edid[index * 128:(index + 1) * 128]
        if block[0] != 2 or sum(block) % 256:
            continue
        end = block[2] or 127
        offset = 4
        while offset < min(end, 127):
            length = block[offset] & 0x1F
            tag = block[offset] >> 5
            offset += 1
            if offset + length > min(end, 127):
                break
            payload = block[offset:offset + length]
            if tag == 3 and length >= 5 and payload[:3] == b"\x03\x0c\x00":
                address = int.from_bytes(payload[3:5], "big")
                if address not in (0, 0xFFFF):
                    return address
            offset += length
    return None


def _detect_physical_address(drm: Path = _DRM) -> int:
    """Read an unambiguous HDMI physical address from connected DRM outputs."""
    addresses: set[int] = set()
    try:
        for connector in drm.iterdir():
            status = connector / "status"
            if not status.exists() or status.read_text(encoding="ascii").strip() != "connected":
                continue
            edid = connector / "edid"
            if edid.exists():
                address = _edid_physical_address(edid.read_bytes())
                if address is not None:
                    addresses.add(address)
    except (OSError, UnicodeError) as error:
        raise CecKernelError(f"Unable to read display EDID: {error}") from error
    if len(addresses) != 1:
        raise CecKernelError("Unable to detect one HDMI address; select the TV HDMI input manually")
    return addresses.pop()


def prepare_kernel_cec(settings: CecSettings) -> str:
    """Set a safe address on libCEC's kernel device before opening the client."""
    try:
        descriptor = os.open(_CEC_LINUX_DEVICE, os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as error:
        raise CecKernelError(f"Cannot open {_CEC_LINUX_DEVICE}: {error}") from error
    try:
        caps = bytearray(76)
        fcntl.ioctl(descriptor, _CEC_ADAP_G_CAPS, caps, True)
        driver, _, _, capabilities, _ = struct.unpack("=32s32sIII", caps)
        driver_name = driver.split(b"\0", 1)[0].decode("ascii", errors="replace")
        if not (driver_name.startswith("pulse8") or driver_name.startswith("vc4")):
            raise CecKernelError(
                f"{_CEC_LINUX_DEVICE} is not a Pulse-Eight or Raspberry Pi adapter ({driver_name})"
            )
        if capabilities & _CEC_REQUIRED_CAPABILITIES != _CEC_REQUIRED_CAPABILITIES:
            raise CecKernelError("The kernel CEC adapter lacks capabilities required by libCEC")
        if driver_name.startswith("pulse8"):
            try:
                persistent = _PULSE8_PERSISTENT.read_text(encoding="ascii").strip()
            except (OSError, UnicodeError) as error:
                raise CecKernelError(f"Unable to check Pulse-Eight persistence: {error}") from error
            if persistent != "0":
                raise CecKernelError("Disable the pulse8_cec persistent_config option before connecting")
        address = bytearray(2)
        fcntl.ioctl(descriptor, _CEC_ADAP_G_PHYS_ADDR, address, True)
        current = struct.unpack("=H", address)[0]
        requested = settings.hdmi_port << 12 if settings.hdmi_port is not None else None
        if capabilities & _CEC_CAP_PHYS_ADDR:
            requested = requested if requested is not None else _detect_physical_address()
            fcntl.ioctl(descriptor, _CEC_ADAP_S_PHYS_ADDR, struct.pack("=H", requested))
            fcntl.ioctl(descriptor, _CEC_ADAP_G_PHYS_ADDR, address, True)
            current = struct.unpack("=H", address)[0]
        if current == 0xFFFF or current == 0:
            raise CecKernelError("The CEC adapter has no valid HDMI physical address")
        if requested is not None and current != requested:
            raise CecKernelError(
                f"The adapter cannot set the requested HDMI address (current: {current:04x})"
            )
        return "Linux"
    except OSError as error:
        raise CecKernelError(f"Unable to configure the kernel CEC adapter: {error}") from error
    finally:
        os.close(descriptor)
