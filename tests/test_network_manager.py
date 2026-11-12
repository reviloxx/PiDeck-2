"""Unit tests for the NetworkManager adapter and its secret handling."""

import subprocess
from pathlib import Path

import pytest

from pideck.application.ports.network import NetworkError, WirelessNetwork
from pideck.infrastructure.network_manager import NetworkManagerGateway, read_link_speed_mbps


def test_wired_link_reads_ethernet_and_ipv4(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Report the first connected Ethernet device, IPv4 address, and link speed."""
    speed_path = tmp_path / "enp3s0"
    speed_path.mkdir()
    (speed_path / "speed").write_text("1000\n", encoding="utf-8")
    gateway = NetworkManagerGateway(sysfs_net=tmp_path)

    def run(arguments: list[str], *, secret: str | None = None, timeout: int = 20) -> str:
        if "show" in arguments:
            return "IP4.ADDRESS[1]:192.168.1.20/24\n"
        return "enp3s0:ethernet:connected:Wired connection 1\nwlan0:wifi:disconnected:\n"

    monkeypatch.setattr(gateway, "_run", run)
    link = gateway.wired_link()
    assert link is not None
    assert link.device == "enp3s0"
    assert link.connection == "Wired connection 1"
    assert link.address == "192.168.1.20"
    assert link.speed_mbps == 1000


def test_wired_link_absent_without_ethernet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wi-Fi-only hosts do not expose a wired link."""
    gateway = NetworkManagerGateway()
    monkeypatch.setattr(
        gateway, "_run",
        lambda arguments, *, secret=None, timeout=20: "wlan0:wifi:connected:Home\n",
    )
    assert gateway.wired_link() is None


@pytest.mark.parametrize("raw", ["-1\n", "0\n", "bad\n"])
def test_read_link_speed_treats_invalid_sysfs_as_missing(tmp_path: Path, raw: str) -> None:
    """Kernel down markers and unreadable speed files yield no speed."""
    device = tmp_path / "enp3s0"
    device.mkdir()
    (device / "speed").write_text(raw, encoding="utf-8")
    assert read_link_speed_mbps("enp3s0", sysfs_net=tmp_path) is None


def test_read_link_speed_missing_device_is_none(tmp_path: Path) -> None:
    """Absent sysfs nodes do not raise."""
    assert read_link_speed_mbps("missing", sysfs_net=tmp_path) is None


@pytest.mark.parametrize(("exit_code", "connected"), [(0, True), (1, False)])
def test_waits_for_connection_in_progress(
    monkeypatch: pytest.MonkeyPatch, exit_code: int, connected: bool,
) -> None:
    """Wait briefly for an in-progress connection instead of prompting immediately."""
    gateway = NetworkManagerGateway()
    monkeypatch.setattr(gateway, "_run", lambda *args, **kwargs: "connecting")
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, exit_code, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert gateway.connected() is connected
    assert calls == [["nm-online", "--quiet", "--timeout=10"]]


def test_connection_wait_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report unexpected NetworkManager errors rather than treating them as offline."""
    gateway = NetworkManagerGateway()
    monkeypatch.setattr(gateway, "_run", lambda *args, **kwargs: "connecting")
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 2, "", ""))
    with pytest.raises(NetworkError, match="could not check"):
        gateway.connected()


def test_scan_parses_escaped_fields_and_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep colons and backslashes in names and prefer connected access points."""
    gateway = NetworkManagerGateway()
    monkeypatch.setattr(
        gateway, "_run",
        lambda *args, **kwargs: "Living\\:Room:wlan0:WPA2:55:\n"
        "Living\\:Room:wlan0:WPA2:60:*\n\nGuest:wlan0:--:90: \n\n",
    )
    networks = gateway.scan()
    assert [(item.ssid, item.signal, item.connected) for item in networks] == [
        ("Living:Room", 60, True), ("Guest", 90, False),
    ]


def test_connect_never_puts_secret_in_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send a Wi-Fi key over stdin without exposing it in argv or error output."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, "", "sensitive network error")

    monkeypatch.setattr(subprocess, "run", run)
    network = WirelessNetwork("Home", "wlan0", "WPA2", 75)
    with pytest.raises(NetworkError, match="could not complete") as error:
        NetworkManagerGateway().connect(network, "secret-key")
    assert "secret-key" not in str(error.value)
    assert "secret-key" not in calls[0][0]
    assert calls[0][1]["input"] == "\x15secret-key\n"


@pytest.mark.parametrize("security", ["WEP", "WPA2 802.1X"])
def test_unsupported_security_is_rejected(security: str) -> None:
    """Do not attempt enterprise or unsupported legacy authentication."""
    network = WirelessNetwork("Office", "wlan0", security, 75)
    with pytest.raises(NetworkError, match="not supported"):
        NetworkManagerGateway().connect(network, "secret-key")


def test_bad_scan_data_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed NetworkManager output is a failure, not an empty network list."""
    gateway = NetworkManagerGateway()
    monkeypatch.setattr(gateway, "_run", lambda *args, **kwargs: "bad:data\n")
    with pytest.raises(NetworkError, match="invalid access point"):
        gateway.scan()


def test_hex_psk_is_accepted_without_publishing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Support the full-length hexadecimal WPA key as well as passphrases."""
    gateway = NetworkManagerGateway()
    calls: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(
        gateway, "_run",
        lambda arguments, *, secret=None, timeout=20: calls.append((arguments, secret)) or "",
    )
    key = "a" * 64
    gateway.connect(WirelessNetwork("Home", "wlan0", "WPA2", 75), key)
    assert calls[0][1] == key
    assert key not in calls[0][0]
