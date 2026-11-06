"""Parse /proc, /etc/os-release and ALSA card listings."""

from pathlib import Path
import re

from pideck.domain.system_info import AudioCardInfo, MemoryInfo

_CARD = re.compile(r"^\s*(\d+)\s+\[[^\]]*\]:\s*.*?\s-\s(.+?)\s*$")


def read_text(path: Path) -> str | None:
    """Return a small text file's content, or None if it cannot be read."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse_cpuinfo(text: str) -> tuple[str | None, int | None]:
    """Return the CPU model and physical core count from /proc/cpuinfo."""
    model: str | None = None
    board: str | None = None
    cores: set[tuple[str, str]] = set()
    physical = "0"
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        if key == "model name" and model is None:
            model = " ".join(value.split())
        elif key == "Model" and board is None:
            board = value
        elif key == "physical id":
            physical = value
        elif key == "core id":
            cores.add((physical, value))
    return model or board, len(cores) or None


def parse_meminfo(text: str) -> MemoryInfo | None:
    """Return total and available memory from /proc/meminfo."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s*kB", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    if "MemTotal" not in values:
        return None
    return MemoryInfo(values["MemTotal"], values.get("MemAvailable", 0))


def parse_uptime(text: str) -> float | None:
    """Return seconds since boot from /proc/uptime."""
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def parse_os_release(text: str) -> str | None:
    """Return PRETTY_NAME from an os-release file."""
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "PRETTY_NAME":
            return value.strip().strip("\"'") or None
    return None


def parse_asound_cards(text: str) -> list[tuple[int, str]]:
    """Return (index, long name) pairs from /proc/asound/cards."""
    cards = []
    for line in text.splitlines():
        match = _CARD.match(line)
        if match:
            cards.append((int(match.group(1)), match.group(2)))
    return cards


def read_audio_cards(proc: Path, sysfs: Path) -> tuple[AudioCardInfo, ...]:
    """Return ALSA cards with the kernel driver bound to each card's device."""
    text = read_text(proc / "asound/cards")
    if text is None:
        return ()
    return tuple(
        AudioCardInfo(index, name, driver_name(sysfs / f"class/sound/card{index}/device"))
        for index, name in parse_asound_cards(text)
    )


def driver_name(device: Path) -> str | None:
    """Return the name of the kernel driver bound to a sysfs device."""
    try:
        return (device / "driver").resolve(strict=True).name
    except OSError:
        return None
