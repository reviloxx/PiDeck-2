"""Read the CPU temperature from hwmon or thermal zones."""

from pathlib import Path

from .procfs import read_text

_HWMON_NAMES = ("coretemp", "k10temp", "zenpower", "cpu_thermal")
_ZONE_TYPES = ("x86_pkg_temp", "cpu-thermal", "cpu_thermal")


def _celsius(path: Path) -> float | None:
    text = read_text(path)
    try:
        return int(text.strip()) / 1000 if text else None
    except ValueError:
        return None


def _children(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir())
    except OSError:
        return []


def read_cpu_temperature(sysfs: Path) -> float | None:
    """Return the CPU package temperature in degrees Celsius if a sensor exists."""
    for hwmon in _children(sysfs / "class/hwmon"):
        if (read_text(hwmon / "name") or "").strip() in _HWMON_NAMES:
            value = _celsius(hwmon / "temp1_input")
            if value is not None:
                return value
    for zone in _children(sysfs / "class/thermal"):
        if (read_text(zone / "type") or "").strip() in _ZONE_TYPES:
            value = _celsius(zone / "temp")
            if value is not None:
                return value
    return None
