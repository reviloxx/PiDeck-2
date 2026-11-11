"""Read-only hardware and software facts shown on the System information page.

Every field that a platform cannot provide is ``None`` or empty, so the same
model describes x86 PCs, Raspberry Pi boards and virtual machines.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CpuInfo:
    """Describe the processor model, topology and current temperature."""

    model: str | None
    cores: int | None
    threads: int | None
    temperature_celsius: float | None


@dataclass(frozen=True, slots=True)
class MemoryInfo:
    """Describe installed and currently available main memory in bytes."""

    total_bytes: int
    available_bytes: int

    @property
    def used_bytes(self) -> int:
        """Return memory that applications and the kernel currently hold."""
        return max(0, self.total_bytes - self.available_bytes)


@dataclass(frozen=True, slots=True)
class GpuInfo:
    """Describe one graphics device and the kernel driver bound to it."""

    name: str
    driver: str | None
    driver_version: str | None


@dataclass(frozen=True, slots=True)
class VideoDecodeInfo:
    """Describe one hardware video decoding API and its supported codecs."""

    api: str
    driver: str | None
    codecs: tuple[str, ...]
    available: bool


@dataclass(frozen=True, slots=True)
class AudioCardInfo:
    """Describe one ALSA sound card and its kernel driver."""

    index: int
    name: str
    driver: str | None


@dataclass(frozen=True, slots=True)
class DiskInfo:
    """Describe usage of the root filesystem in bytes."""

    total_bytes: int
    used_bytes: int


@dataclass(frozen=True, slots=True)
class NetworkAddress:
    """Describe one globally routable address assigned to an interface."""

    interface: str
    address: str


@dataclass(frozen=True, slots=True)
class NetworkLink:
    """Describe one NetworkManager ethernet or Wi-Fi device that is connected."""

    device: str
    kind: str
    connection: str | None = None


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Aggregate all facts shown on the System information page."""

    pideck_version: str
    operating_system: str | None
    kernel: str | None
    uptime_seconds: float | None
    cpu: CpuInfo
    memory: MemoryInfo | None
    disk: DiskInfo | None
    gpus: tuple[GpuInfo, ...] = ()
    video_decoding: tuple[VideoDecodeInfo, ...] = ()
    audio_cards: tuple[AudioCardInfo, ...] = ()
    addresses: tuple[NetworkAddress, ...] = ()
    links: tuple[NetworkLink, ...] = ()
