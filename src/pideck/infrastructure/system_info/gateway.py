"""Compose the Linux readers into the SystemInfoGateway port."""

from pathlib import Path
import os
import platform
import shutil

from pideck import __version__
from pideck.domain.system_info import CpuInfo, DiskInfo, SystemInfo

from .commands import CommandRunner, run_command
from .gpu import read_gpus
from .network import read_addresses
from .procfs import (
    parse_cpuinfo, parse_meminfo, parse_os_release, parse_uptime, read_audio_cards, read_text,
)
from .sensors import read_cpu_temperature
from .video_decode import read_video_decoding


class LinuxSystemInfoGateway:
    """Collect a SystemInfo snapshot from procfs, sysfs and diagnostic tools."""

    def __init__(
        self,
        *,
        proc: Path = Path("/proc"),
        sysfs: Path = Path("/sys"),
        etc: Path = Path("/etc"),
        root: Path = Path("/"),
        run: CommandRunner = run_command,
    ) -> None:
        """Use the given filesystem roots and command runner; defaults read the live host."""
        self._proc = proc
        self._sysfs = sysfs
        self._etc = etc
        self._root = root
        self._run = run

    def inspect(self) -> SystemInfo:
        """Return a fresh snapshot; unavailable facts are None or empty."""
        model, cores = parse_cpuinfo(read_text(self._proc / "cpuinfo") or "")
        meminfo = read_text(self._proc / "meminfo")
        uptime = read_text(self._proc / "uptime")
        os_release = read_text(self._etc / "os-release")
        return SystemInfo(
            pideck_version=__version__,
            operating_system=parse_os_release(os_release) if os_release else None,
            kernel=platform.release() or None,
            uptime_seconds=parse_uptime(uptime) if uptime else None,
            cpu=CpuInfo(model, cores, os.cpu_count(), read_cpu_temperature(self._sysfs)),
            memory=parse_meminfo(meminfo) if meminfo else None,
            disk=self._disk(),
            gpus=read_gpus(self._sysfs, self._proc, self._run),
            video_decoding=read_video_decoding(self._run),
            audio_cards=read_audio_cards(self._proc, self._sysfs),
            addresses=read_addresses(self._run),
        )

    def _disk(self) -> DiskInfo | None:
        try:
            usage = shutil.disk_usage(self._root)
        except OSError:
            return None
        return DiskInfo(usage.total, usage.used)
