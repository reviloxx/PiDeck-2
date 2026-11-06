"""Identify graphics devices and their kernel drivers from sysfs."""

from pathlib import Path
import re
import shlex

from pideck.domain.system_info import GpuInfo

from .commands import CommandRunner
from .procfs import driver_name, read_text

_CARD = re.compile(r"^card\d+$")


def parse_lspci_name(text: str) -> str | None:
    """Return "vendor device" from one ``lspci -mm`` line."""
    try:
        fields = shlex.split(text.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None
    if len(fields) < 4:
        return None
    return f"{fields[2]} {fields[3]}"


def driver_version(sysfs: Path, proc: Path, driver: str | None) -> str | None:
    """Return the version of an out-of-tree driver such as NVIDIA's."""
    if driver is None:
        return None
    version = read_text(sysfs / f"module/{driver}/version")
    if version is None and driver == "nvidia":
        text = read_text(proc / "driver/nvidia/version") or ""
        match = re.search(r"Kernel Module\s+(?:for \S+\s+)?([\d.]+)", text)
        version = match.group(1) if match else None
    return version.strip() if version else None


def _fallback_name(device: Path, driver: str | None) -> str:
    vendor = (read_text(device / "vendor") or "").strip()
    product = (read_text(device / "device") or "").strip()
    if vendor and product:
        return f"PCI {vendor.removeprefix('0x')}:{product.removeprefix('0x')}"
    compatible = (read_text(device / "of_node/compatible") or "").split("\0")[0]
    return compatible or driver or device.name


def read_gpus(sysfs: Path, proc: Path, run: CommandRunner) -> tuple[GpuInfo, ...]:
    """Return one entry per DRM device, named via lspci where available."""
    seen: set[Path] = set()
    gpus = []
    drm = sysfs / "class/drm"
    try:
        cards = sorted(path for path in drm.iterdir() if _CARD.match(path.name))
    except OSError:
        return ()
    for card in cards:
        try:
            device = (card / "device").resolve(strict=True)
        except OSError:
            continue
        if device in seen:
            continue
        seen.add(device)
        driver = driver_name(card / "device")
        name = None
        if (device / "vendor").is_file():
            output = run(["lspci", "-mm", "-s", device.name])
            name = parse_lspci_name(output) if output else None
        gpus.append(GpuInfo(
            name or _fallback_name(device, driver), driver, driver_version(sysfs, proc, driver),
        ))
    return tuple(gpus)
