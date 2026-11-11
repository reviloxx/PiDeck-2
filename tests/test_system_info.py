"""Tests for the Linux system information readers using fake procfs/sysfs trees."""

import json
import os
from pathlib import Path

from pideck.domain.system_info import (
    AudioCardInfo, CpuInfo, GpuInfo, MemoryInfo, NetworkAddress, NetworkLink, SystemInfo,
)
from pideck.infrastructure.system_info import LinuxSystemInfoGateway
from pideck.infrastructure.system_info.commands import run_command
from pideck.infrastructure.system_info.gpu import parse_lspci_name, read_gpus
from pideck.infrastructure.system_info.network import parse_ip_addresses, parse_network_links
from pideck.infrastructure.system_info.procfs import (
    parse_asound_cards, parse_cpuinfo, parse_meminfo, parse_os_release, parse_uptime, read_audio_cards,
)
from pideck.infrastructure.system_info.sensors import read_cpu_temperature
from pideck.infrastructure.system_info.video_decode import codec_family, parse_vainfo, parse_vdpauinfo
from pideck.presentation.qt.system_info_format import system_info_sections

CPUINFO = "".join(
    f"processor\t: {thread}\nmodel name\t: Intel(R) Core(TM) i3-6100T CPU @ 3.20GHz\n"
    f"physical id\t: 0\ncore id\t\t: {thread % 2}\n\n"
    for thread in range(4)
)
PI_CPUINFO = "processor\t: 0\nBogoMIPS\t: 108.00\n\nRevision\t: d04170\nModel\t\t: Raspberry Pi 5 Model B Rev 1.0\n"
ASOUND = (
    " 0 [DX             ]: AV200 - Xonar DX\n"
    "                      Asus Virtuoso 100 at 0xe000, irq 16\n"
    " 1 [NVidia         ]: HDA-Intel - HDA NVidia\n"
    "                      HDA NVidia at 0xf7080000 irq 17\n"
)
VDPAUINFO = """display: :0   screen: 0
API version: 1
Information string: NVIDIA VDPAU Driver Shared Library  550.163.01  Tue Apr  8 19:05:49 UTC 2025

Video surface:

name   width height types
-------------------------------------------
420     16384 16384  NV12 YV12

Decoder capabilities:

name                        level macbs width height
----------------------------------------------------
MPEG1                           0 65536  4080  4080
MPEG2_MAIN                      3 65536  4080  4080
H264_HIGH                      41 65536  4096  4096
VC1_ADVANCED                    4 65536  4080  4080
HEVC_MAIN_10                  153 262144  8192  8192
VP9_PROFILE_0                  -- not supported --
AV1_MAIN                       --- not supported ---

Output surface:

name              width height nat types
----------------------------------------------------
B8G8R8A8         32768 32768    y  NV12 YV12
"""
VAINFO = """vainfo: VA-API version: 1.22 (libva 2.22.0)
vainfo: Driver version: VA-API NVDEC driver [direct backend]
vainfo: Supported profile and entrypoints
      VAProfileMPEG2Simple            : VAEntrypointVLD
      VAProfileH264Main               : VAEntrypointVLD
      VAProfileHEVCMain10             : VAEntrypointVLD
      VAProfileVP9Profile0            : VAEntrypointVLD
      VAProfileH264Main               : VAEntrypointEncSlice
      VAProfileAV1Profile0            : VAEntrypointEncSlice
"""


def test_cpuinfo_reports_model_and_physical_cores() -> None:
    """Count distinct physical cores rather than hyper-threads."""
    assert parse_cpuinfo(CPUINFO) == ("Intel(R) Core(TM) i3-6100T CPU @ 3.20GHz", 2)


def test_raspberry_pi_cpuinfo_falls_back_to_board_model() -> None:
    """ARM cpuinfo lacks a model name and core ids."""
    assert parse_cpuinfo(PI_CPUINFO) == ("Raspberry Pi 5 Model B Rev 1.0", None)


def test_meminfo_uptime_and_os_release() -> None:
    """Parse memory in bytes, uptime seconds and the distribution's pretty name."""
    memory = parse_meminfo("MemTotal:        8048576 kB\nMemFree: 1 kB\nMemAvailable:    6048576 kB\n")
    assert memory == MemoryInfo(8048576 * 1024, 6048576 * 1024)
    assert memory.used_bytes == 2000000 * 1024
    assert parse_meminfo("") is None
    assert parse_uptime("4373.06 33000.10\n") == 4373.06
    assert parse_uptime("") is None
    assert parse_os_release('NAME="Debian"\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n') == (
        "Debian GNU/Linux 13 (trixie)"
    )


def test_audio_cards_include_the_bound_kernel_driver(tmp_path: Path) -> None:
    """Report the Xonar DX with its snd_virtuoso driver."""
    assert parse_asound_cards(ASOUND) == [(0, "Xonar DX"), (1, "HDA NVidia")]
    proc, sysfs = tmp_path / "proc", tmp_path / "sys"
    (proc / "asound").mkdir(parents=True)
    (proc / "asound/cards").write_text(ASOUND)
    driver = sysfs / "bus/pci/drivers/snd_virtuoso"
    driver.mkdir(parents=True)
    device = sysfs / "class/sound/card0/device"
    device.mkdir(parents=True)
    (device / "driver").symlink_to(driver)
    assert read_audio_cards(proc, sysfs) == (
        AudioCardInfo(0, "Xonar DX", "snd_virtuoso"), AudioCardInfo(1, "HDA NVidia", None),
    )
    assert read_audio_cards(tmp_path / "missing", sysfs) == ()


def _pci_gpu(sysfs: Path, card: str, slot: str, driver: str) -> None:
    device = sysfs / "devices/pci0000:00" / slot
    device.mkdir(parents=True)
    (device / "vendor").write_text("0x10de\n")
    (device / "device").write_text("0x1d01\n")
    (sysfs / "bus/pci/drivers" / driver).mkdir(parents=True, exist_ok=True)
    (device / "driver").symlink_to(sysfs / "bus/pci/drivers" / driver)
    (sysfs / "class/drm" / card).mkdir(parents=True)
    (sysfs / "class/drm" / card / "device").symlink_to(device)


def test_gpus_are_named_by_lspci_with_nvidia_driver_version(tmp_path: Path) -> None:
    """Combine sysfs driver binding, the module version and lspci names."""
    sysfs, proc = tmp_path / "sys", tmp_path / "proc"
    _pci_gpu(sysfs, "card0", "0000:01:00.0", "nvidia")
    (sysfs / "class/drm/card0-HDMI-A-1").mkdir()
    (sysfs / "module/nvidia").mkdir(parents=True)
    (sysfs / "module/nvidia/version").write_text("550.163.01\n")
    commands: list[list[str]] = []

    def run(arguments: list[str]) -> str | None:
        commands.append(arguments)
        return '01:00.0 "VGA compatible controller" "NVIDIA Corporation" "GP108 [GeForce GT 1030]" -ra1 "ASUSTeK" "Device"\n'

    assert read_gpus(sysfs, proc, run) == (
        GpuInfo("NVIDIA Corporation GP108 [GeForce GT 1030]", "nvidia", "550.163.01"),
    )
    assert commands == [["lspci", "-mm", "-s", "0000:01:00.0"]]


def test_gpu_falls_back_to_pci_ids_and_proc_driver_version(tmp_path: Path) -> None:
    """Work without lspci and read the NVIDIA version from procfs."""
    sysfs, proc = tmp_path / "sys", tmp_path / "proc"
    _pci_gpu(sysfs, "card1", "0000:01:00.0", "nvidia")
    (proc / "driver/nvidia").mkdir(parents=True)
    (proc / "driver/nvidia/version").write_text(
        "NVRM version: NVIDIA UNIX x86_64 Kernel Module  550.163.01  Tue Apr  8 2025\n"
    )
    assert read_gpus(sysfs, proc, lambda _: None) == (GpuInfo("PCI 10de:1d01", "nvidia", "550.163.01"),)
    assert read_gpus(tmp_path / "missing", proc, lambda _: None) == ()
    assert parse_lspci_name("") is None


def test_vdpauinfo_lists_only_supported_decoders() -> None:
    """Ignore unsupported profiles and the output surface table."""
    info = parse_vdpauinfo(VDPAUINFO)
    assert info.api == "VDPAU" and info.available
    assert info.driver == "NVIDIA VDPAU Driver Shared Library 550.163.01"
    assert info.codecs == ("HEVC", "H.264", "VC-1", "MPEG-2", "MPEG-1")


def test_vainfo_lists_decode_entrypoints_only() -> None:
    """Encode-only profiles are not decoders."""
    info = parse_vainfo(VAINFO)
    assert info.driver == "VA-API NVDEC driver [direct backend]"
    assert info.codecs == ("HEVC", "H.264", "VP9", "MPEG-2")
    assert not parse_vainfo("vainfo: Driver version: x\n").available
    assert codec_family("VAProfileJPEGBaseline") == "JPEG"
    assert codec_family("DIVX5_QMOBILE") is None


def test_cpu_temperature_prefers_hwmon_then_thermal_zone(tmp_path: Path) -> None:
    """Read coretemp on x86 and cpu-thermal zones on the Pi."""
    hwmon = tmp_path / "class/hwmon/hwmon1"
    hwmon.mkdir(parents=True)
    (hwmon / "name").write_text("coretemp\n")
    (hwmon / "temp1_input").write_text("45000\n")
    assert read_cpu_temperature(tmp_path) == 45.0
    (hwmon / "name").write_text("nvme\n")
    zone = tmp_path / "class/thermal/thermal_zone0"
    zone.mkdir(parents=True)
    (zone / "type").write_text("cpu-thermal\n")
    (zone / "temp").write_text("51500\n")
    assert read_cpu_temperature(tmp_path) == 51.5
    assert read_cpu_temperature(tmp_path / "missing") is None


def test_ip_addresses_skip_loopback_and_link_local() -> None:
    """Show only addresses other devices can reach."""
    document = json.dumps([
        {"ifname": "lo", "addr_info": [{"local": "127.0.0.1", "scope": "host"}]},
        {"ifname": "enp3s0", "addr_info": [
            {"family": "inet", "local": "192.168.1.20", "scope": "global"},
            {"family": "inet6", "local": "fe80::1", "scope": "link"},
        ]},
    ])
    assert parse_ip_addresses(document) == (NetworkAddress("enp3s0", "192.168.1.20"),)
    assert parse_ip_addresses("not json") == ()


def test_network_links_include_connected_ethernet_and_wifi() -> None:
    """Only connected data devices become System information link rows."""
    text = (
        "enp3s0:ethernet:connected:Wired connection 1\n"
        "wlan0:wifi:disconnected:\n"
        "lo:loopback:unmanaged:\n"
    )
    assert parse_network_links(text) == (
        NetworkLink("enp3s0", "ethernet", "Wired connection 1"),
    )
    assert parse_network_links("bad") == ()


def test_system_info_shows_ethernet_without_addresses() -> None:
    """A cable without a published address is still shown as connected."""
    info = SystemInfo(
        "0.3.13", None, None, None, CpuInfo(None, None, None, None), None, None,
        links=(NetworkLink("enp3s0", "ethernet", "Wired connection 1"),),
    )
    network = dict(dict(system_info_sections(info, "en"))["Network"])
    assert network == {"Ethernet (enp3s0)": "Wired connection 1"}
    offline = SystemInfo("0.3.13", None, None, None, CpuInfo(None, None, None, None), None, None)
    assert dict(dict(system_info_sections(offline, "en"))["Network"]) == {"Not connected": ""}


def test_missing_diagnostic_tool_returns_none() -> None:
    """A tool that is not installed is not an error."""
    assert run_command(["pideck-definitely-missing-tool"]) is None
    assert run_command(["sh", "-c", "exit 3"]) is None
    assert run_command(["sh", "-c", "echo out; echo err >&2"]) == "out\n"


def test_gateway_degrades_gracefully_on_an_empty_host(tmp_path: Path) -> None:
    """Every fact is optional; nothing raises without procfs, sysfs or tools."""
    info = LinuxSystemInfoGateway(
        proc=tmp_path / "proc", sysfs=tmp_path / "sys", etc=tmp_path / "etc", root=tmp_path / "missing",
        run=lambda _: None,
    ).inspect()
    assert info.cpu.model is None and info.cpu.threads == os.cpu_count()
    assert info.memory is None and info.disk is None and info.uptime_seconds is None
    assert info.operating_system is None
    assert info.gpus == () and info.audio_cards == () and info.addresses == () and info.links == ()
    assert [decoder.available for decoder in info.video_decoding] == [False, False]
