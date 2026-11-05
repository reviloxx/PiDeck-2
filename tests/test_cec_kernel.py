"""Tests for safe Linux-kernel HDMI-CEC address preparation."""

import struct
from pathlib import Path

import pytest

from pideck.domain.input_settings import CecSettings
from pideck.infrastructure import cec_kernel
from pideck.infrastructure.cec_kernel import CecKernelError, prepare_kernel_cec


def hdmi_edid(address: int) -> bytes:
    """Build a minimal EDID with one valid HDMI vendor block."""
    data = bytearray(256)
    data[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    data[126] = 1
    data[127] = -sum(data[:127]) % 256
    data[128] = 2
    data[130] = 10
    data[132:138] = b"\x65\x03\x0c\x00" + address.to_bytes(2, "big")
    data[255] = -sum(data[128:255]) % 256
    return bytes(data)


def test_edid_address_detection_requires_one_connected_address(tmp_path: Path) -> None:
    """Automatic detection uses the connected TV's physical EDID address."""
    connector = tmp_path / "card0-HDMI-A-1"
    connector.mkdir()
    (connector / "status").write_text("connected\n", encoding="ascii")
    (connector / "edid").write_bytes(hdmi_edid(0x2000))
    assert cec_kernel._detect_physical_address(tmp_path) == 0x2000
    second = tmp_path / "card0-HDMI-A-2"
    second.mkdir()
    (second / "status").write_text("connected\n", encoding="ascii")
    (second / "edid").write_bytes(hdmi_edid(0x1000))
    with pytest.raises(CecKernelError, match="select the TV HDMI input"):
        cec_kernel._detect_physical_address(tmp_path)


def test_corrupt_edid_is_not_used() -> None:
    """A bad extension checksum cannot silently select an HDMI input."""
    data = bytearray(hdmi_edid(0x2000))
    data[140] += 1
    assert cec_kernel._edid_physical_address(bytes(data)) is None


@pytest.mark.parametrize(
    ("driver", "caps", "current", "port", "expected", "error"),
    [
        ("pulse8-cec", 15, 0x1000, 2, 0x2000, None),
        ("vc4_hdmi", 14, 0x2000, 2, None, None),
        ("vc4_hdmi", 14, 0x1000, 2, None, "cannot set"),
        ("i915", 15, 0x1000, 2, None, "not a Pulse-Eight"),
        ("pulse8-cec", 1, 0x1000, 2, None, "lacks capabilities"),
    ],
)
def test_address_is_configured_before_client_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, driver: str, caps: int, current: int,
    port: int, expected: int | None, error: str | None,
) -> None:
    """Do not use the wrong adapter or ignore an unsupported requested port."""
    actions: list[int] = []
    address = current
    monkeypatch.setattr(cec_kernel.os, "open", lambda *args: 123)
    monkeypatch.setattr(cec_kernel.os, "close", lambda fd: None)
    persistent = tmp_path / "persistent_config"
    persistent.write_text("0\n", encoding="ascii")
    monkeypatch.setattr(cec_kernel, "_PULSE8_PERSISTENT", persistent)

    def ioctl(fd: int, operation: int, data: bytearray | bytes, *args: object) -> int:
        nonlocal address
        actions.append(operation)
        if operation == cec_kernel._CEC_ADAP_G_CAPS:
            data[:] = struct.pack("=32s32sIII", driver.encode(), b"test", 1, caps, 0)
        elif operation == cec_kernel._CEC_ADAP_G_PHYS_ADDR:
            data[:] = struct.pack("=H", address)
        elif operation == cec_kernel._CEC_ADAP_S_PHYS_ADDR:
            address = struct.unpack("=H", data)[0]
        return 0

    monkeypatch.setattr(cec_kernel.fcntl, "ioctl", ioctl)
    if error is not None:
        with pytest.raises(CecKernelError, match=error):
            prepare_kernel_cec(CecSettings(hdmi_port=port))
    else:
        assert prepare_kernel_cec(CecSettings(hdmi_port=port)) == "Linux"
    assert (cec_kernel._CEC_ADAP_S_PHYS_ADDR in actions) is (expected is not None)
    if expected is not None:
        assert address == expected


def test_missing_device_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing system integration is surfaced rather than falling back to USB."""
    def missing(*args: object) -> int:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(cec_kernel.os, "open", missing)
    with pytest.raises(CecKernelError, match="Cannot open"):
        prepare_kernel_cec(CecSettings())


def test_persistent_pulse8_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Do not change a Pulse-Eight adapter configured to write EEPROM."""
    persistent = tmp_path / "persistent_config"
    persistent.write_text("1\n", encoding="ascii")
    monkeypatch.setattr(cec_kernel, "_PULSE8_PERSISTENT", persistent)
    monkeypatch.setattr(cec_kernel.os, "open", lambda *args: 123)
    monkeypatch.setattr(cec_kernel.os, "close", lambda fd: None)
    operations: list[int] = []

    def ioctl(fd: int, operation: int, data: bytearray, *args: object) -> int:
        operations.append(operation)
        data[:] = struct.pack("=32s32sIII", b"pulse8-cec", b"test", 1, 15, 0)
        return 0

    monkeypatch.setattr(cec_kernel.fcntl, "ioctl", ioctl)
    with pytest.raises(CecKernelError, match="persistent_config"):
        prepare_kernel_cec(CecSettings(hdmi_port=2))
    assert operations == [cec_kernel._CEC_ADAP_G_CAPS]
