"""Turn a SystemInfo snapshot into localized, display-ready sections.

Kept free of widgets so formatting is testable without a GUI.
"""

from pideck.domain.system_info import SystemInfo

from .localization import tr

Row = tuple[str, str]
Section = tuple[str, tuple[Row, ...]]

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def format_bytes(language: str, value: int) -> str:
    """Format a byte count with a binary unit and the language's decimal separator."""
    amount = float(value)
    unit = _UNITS[0]
    for unit in _UNITS:
        if amount < 1024 or unit == _UNITS[-1]:
            break
        amount /= 1024
    text = f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
    return text.replace(".", ",") if language == "de" else text


def format_uptime(language: str, seconds: float) -> str:
    """Format seconds since boot as days, hours and minutes."""
    minutes_total = int(seconds) // 60
    days, remainder = divmod(minutes_total, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    if days:
        return tr(language, "system_info_days", days=days, hours=hours, minutes=minutes)
    return tr(language, "system_info_hours", hours=hours, minutes=minutes)


def _usage(language: str, used: int, total: int) -> str:
    return tr(
        language, "system_info_usage",
        used=format_bytes(language, used), total=format_bytes(language, total),
    )


def _software(info: SystemInfo, language: str, missing: str) -> Section:
    return (tr(language, "system_info_software"), (
        (tr(language, "system_info_pideck"), info.pideck_version),
        (tr(language, "system_info_os"), info.operating_system or missing),
        (tr(language, "system_info_kernel"), info.kernel or missing),
        (tr(language, "system_info_uptime"),
         format_uptime(language, info.uptime_seconds) if info.uptime_seconds is not None else missing),
    ))


def _hardware(info: SystemInfo, language: str, missing: str) -> Section:
    cpu = info.cpu
    processor = cpu.model or missing
    if cpu.cores is not None and cpu.threads is not None:
        processor += "\n" + tr(language, "system_info_cpu_topology", cores=cpu.cores, threads=cpu.threads)
    temperature = missing
    if cpu.temperature_celsius is not None:
        temperature = f"{cpu.temperature_celsius:.0f} °C"
    memory = _usage(language, info.memory.used_bytes, info.memory.total_bytes) if info.memory else missing
    disk = _usage(language, info.disk.used_bytes, info.disk.total_bytes) if info.disk else missing
    return (tr(language, "system_info_hardware"), (
        (tr(language, "system_info_cpu"), processor),
        (tr(language, "system_info_temperature"), temperature),
        (tr(language, "system_info_memory"), memory),
        (tr(language, "system_info_disk"), disk),
    ))


def _graphics(info: SystemInfo, language: str, missing: str) -> Section:
    rows: list[Row] = []
    for gpu in info.gpus:
        if gpu.driver and gpu.driver_version:
            driver = tr(language, "system_info_driver_version", driver=gpu.driver, version=gpu.driver_version)
        elif gpu.driver:
            driver = tr(language, "system_info_driver", driver=gpu.driver)
        else:
            driver = missing
        rows.append((gpu.name, driver))
    return tr(language, "system_info_graphics"), tuple(rows) or ((tr(language, "system_info_no_devices"), ""),)


def _video(info: SystemInfo, language: str, missing: str) -> Section:
    rows: list[Row] = []
    for decoder in info.video_decoding:
        if not decoder.available:
            rows.append((decoder.api, missing))
            continue
        value = ", ".join(decoder.codecs)
        if decoder.driver:
            value += "\n" + decoder.driver
        rows.append((decoder.api, value))
    return tr(language, "system_info_video"), tuple(rows) or ((tr(language, "system_info_no_devices"), ""),)


def _audio(info: SystemInfo, language: str, missing: str) -> Section:
    rows = tuple(
        (card.name, tr(language, "system_info_driver", driver=card.driver) if card.driver else missing)
        for card in info.audio_cards
    )
    return tr(language, "system_info_audio"), rows or ((tr(language, "system_info_no_devices"), ""),)


def _network(info: SystemInfo, language: str) -> Section:
    rows = tuple((address.interface, address.address) for address in info.addresses)
    if rows:
        return tr(language, "system_info_network"), rows
    link_rows: list[Row] = []
    for link in info.links:
        if link.kind == "ethernet":
            label = tr(language, "system_info_ethernet", device=link.device)
        elif link.kind == "wifi":
            label = tr(language, "system_info_wifi", device=link.device)
        else:
            label = link.device
        detail = link.connection or tr(language, "system_info_link_connected")
        link_rows.append((label, detail))
    if link_rows:
        return tr(language, "system_info_network"), tuple(link_rows)
    return tr(language, "system_info_network"), ((tr(language, "system_info_no_addresses"), ""),)


def system_info_sections(info: SystemInfo, language: str) -> tuple[Section, ...]:
    """Return titled sections of (label, value) rows in display order."""
    missing = tr(language, "system_info_unavailable")
    return (
        _software(info, language, missing),
        _hardware(info, language, missing),
        _graphics(info, language, missing),
        _video(info, language, missing),
        _audio(info, language, missing),
        _network(info, language),
    )
