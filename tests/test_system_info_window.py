"""Offscreen tests for the System information window and its formatting."""

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.system_info import (
    AudioCardInfo, CpuInfo, DiskInfo, GpuInfo, MemoryInfo, NetworkAddress, SystemInfo, VideoDecodeInfo,
)
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.system_info_format import format_bytes, format_uptime, system_info_sections
from pideck.presentation.qt.system_info_window import SystemInfoWindow

INFO = SystemInfo(
    pideck_version="0.3.13",
    operating_system="Debian GNU/Linux 13 (trixie)",
    kernel="6.12.48+deb13-amd64",
    uptime_seconds=93780,
    cpu=CpuInfo("Intel(R) Core(TM) i3-6100T CPU @ 3.20GHz", 2, 4, 41.0),
    memory=MemoryInfo(8 * 1024**3, 6 * 1024**3),
    disk=DiskInfo(100 * 1024**3, 25 * 1024**3),
    gpus=(GpuInfo("NVIDIA Corporation GP108 [GeForce GT 1030]", "nvidia", "550.163.01"),),
    video_decoding=(
        VideoDecodeInfo("VDPAU", "NVIDIA VDPAU Driver Shared Library 550.163.01", ("HEVC", "H.264"), True),
        VideoDecodeInfo("VA-API", None, (), False),
    ),
    audio_cards=(AudioCardInfo(0, "Xonar DX", "snd_virtuoso"),),
    addresses=(NetworkAddress("enp3s0", "192.168.1.20"),),
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide an offscreen Qt application."""
    return QApplication.instance() or QApplication([])


class Gateway:
    """In-memory gateway that can fail or block on demand."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error
        self.release = threading.Event()
        self.release.set()

    def inspect(self) -> SystemInfo:
        self.calls += 1
        self.release.wait(5)
        if self.error is not None:
            raise self.error
        return INFO


def _wait(application: QApplication, condition) -> None:
    for _ in range(200):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("System information did not load")


def test_formatting_uses_units_and_language() -> None:
    """Bytes use binary units; German uses a decimal comma."""
    assert format_bytes("en", 512) == "512 B"
    assert format_bytes("en", 8 * 1024**3) == "8.0 GiB"
    assert format_bytes("de", 1536 * 1024**2) == "1,5 GiB"
    assert format_uptime("en", 93780) == "1 d 2 h 3 min"
    assert format_uptime("en", 3700) == "1 h 1 min"


def test_sections_cover_hardware_drivers_and_software() -> None:
    """Every requested fact appears in a labeled row."""
    sections = dict(system_info_sections(INFO, "en"))
    assert set(sections) == {
        "Software", "Hardware", "Graphics", "Hardware video decoding", "Audio devices", "Network",
    }
    software = dict(sections["Software"])
    assert software["PiDeck"] == "0.3.13" and software["Uptime"] == "1 d 2 h 3 min"
    hardware = dict(sections["Hardware"])
    assert hardware["Processor"].endswith("\n2 cores, 4 threads")
    assert hardware["CPU temperature"] == "41 °C"
    assert hardware["Memory"] == "2.0 GiB of 8.0 GiB used"
    assert dict(sections["Graphics"]) == {
        "NVIDIA Corporation GP108 [GeForce GT 1030]": "nvidia driver 550.163.01",
    }
    video = dict(sections["Hardware video decoding"])
    assert video["VDPAU"].startswith("HEVC, H.264\n") and video["VA-API"] == "Not available"
    assert dict(sections["Audio devices"]) == {"Xonar DX": "snd_virtuoso driver"}
    assert dict(sections["Network"]) == {"enp3s0": "192.168.1.20"}


def test_missing_facts_are_reported_as_unavailable() -> None:
    """A platform without sensors, GPUs or network still renders every section."""
    sparse = SystemInfo("0.3.13", None, None, None, CpuInfo(None, None, None, None), None, None)
    sections = dict(system_info_sections(sparse, "de"))
    assert dict(sections["Hardware"])["Prozessor"] == "Nicht verfügbar"
    assert dict(sections["Grafik"]) == {"Keine Geräte gefunden": ""}
    assert dict(sections["Netzwerk"]) == {"Nicht verbunden": ""}


def test_window_loads_asynchronously_and_is_controller_accessible(application: QApplication) -> None:
    """Loading happens off the UI thread; the controller scrolls, refreshes and closes."""
    parent = QWidget()
    gateway = Gateway()
    gateway.release.clear()
    window = SystemInfoWindow(gateway, DEFAULT_THEME, "en", parent)
    window.resize(1280, 400)
    window.show()
    application.processEvents()
    assert window.status.text() == "Collecting system information..."
    assert not window.refresh.isEnabled()
    gateway.release.set()
    _wait(application, lambda: window.refresh.isEnabled())
    texts = {label.text() for label in window.scroll.widget().findChildren(QLabel)}
    assert {"Xonar DX", "snd_virtuoso driver", "nvidia driver 550.163.01"} <= texts
    assert window.status.isHidden()
    bar = window.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert bar.value() > 0
    QTest.keyClick(window.back, Qt.Key.Key_Up)
    assert bar.value() == 0
    window.handle_input(InputEvent(InputAction.LEFT, "gamepad"))
    assert window.focusWidget() is window.refresh
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: gateway.calls == 2 and window.refresh.isEnabled())
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert not window.isVisible()


def test_window_reports_collection_failure(application: QApplication) -> None:
    """An unexpected gateway failure is shown instead of crashing the launcher."""
    parent = QWidget()
    window = SystemInfoWindow(Gateway(RuntimeError("boom")), DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.refresh.isEnabled())
    assert window.status.text() == "System information is unavailable."
    assert window.status.property("error") is True
    window.reject()
