"""Parse NetworkManager device listings without Qt or secrets."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NmDevice:
    """One NetworkManager device row from ``nmcli device``."""

    device: str
    type: str
    state: str
    connection: str


def decode_nmcli_fields(line: str) -> list[str]:
    """Decode nmcli's backslash-escaped, colon-separated output."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current.clear()
        else:
            current.append(character)
    if escaped:
        raise ValueError("invalid escaped nmcli output")
    fields.append("".join(current))
    return fields


def parse_device_list(text: str) -> tuple[NmDevice, ...]:
    """Parse ``nmcli --terse --fields DEVICE,TYPE,STATE,CONNECTION device`` output."""
    devices: list[NmDevice] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = decode_nmcli_fields(line)
        if len(fields) != 4:
            raise ValueError("invalid nmcli device row")
        device, kind, state, connection = fields
        if not device or not kind or not state:
            raise ValueError("invalid nmcli device row")
        devices.append(NmDevice(device, kind, state, connection if connection not in {"", "--"} else ""))
    return tuple(devices)


def parse_ipv4_address(show_output: str) -> str | None:
    """Return the first IPv4 address from ``nmcli device show`` terse output."""
    for line in show_output.splitlines():
        if "IP4.ADDRESS" not in line and not line.startswith("IP4.ADDRESS"):
            # Terse ``-f IP4.ADDRESS`` may print only ``192.168.1.1/24``.
            if "/" in line and line.count(":") == 0:
                return line.split("/", 1)[0].strip() or None
            continue
        _, _, value = line.partition(":")
        address = value.split("/", 1)[0].strip()
        if address:
            return address
    return None


def connected_data_devices(devices: tuple[NmDevice, ...]) -> tuple[NmDevice, ...]:
    """Return ethernet/wifi devices that NetworkManager reports as connected."""
    return tuple(
        device for device in devices
        if device.type in {"ethernet", "wifi"} and device.state.startswith("connected")
    )
