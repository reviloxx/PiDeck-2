"""List addresses and NetworkManager link state for System information."""

import json
import logging

from pideck.domain.system_info import NetworkAddress, NetworkLink
from pideck.infrastructure.nmcli_devices import connected_data_devices, parse_device_list

from .commands import CommandRunner

_LOGGER = logging.getLogger(__name__)


def parse_ip_addresses(text: str) -> tuple[NetworkAddress, ...]:
    """Return global-scope addresses from ``ip -j address show``, skipping loopback."""
    try:
        interfaces = json.loads(text)
    except json.JSONDecodeError as error:
        _LOGGER.info("Unable to parse interface addresses: %s", error)
        return ()
    addresses = []
    for interface in interfaces if isinstance(interfaces, list) else ():
        name = interface.get("ifname") if isinstance(interface, dict) else None
        if not isinstance(name, str) or name == "lo":
            continue
        for entry in interface.get("addr_info", ()):
            if isinstance(entry, dict) and entry.get("scope") == "global" and isinstance(entry.get("local"), str):
                addresses.append(NetworkAddress(name, entry["local"]))
    return tuple(addresses)


def parse_network_links(text: str) -> tuple[NetworkLink, ...]:
    """Return connected ethernet/wifi devices from nmcli device status output."""
    try:
        devices = connected_data_devices(parse_device_list(text))
    except ValueError as error:
        _LOGGER.info("Unable to parse NetworkManager devices: %s", error)
        return ()
    return tuple(
        NetworkLink(device.device, device.type, device.connection or None)
        for device in devices
    )


def read_addresses(run: CommandRunner) -> tuple[NetworkAddress, ...]:
    """Return current interface addresses, or nothing when iproute2 is unavailable."""
    output = run(["ip", "-j", "address", "show"])
    return parse_ip_addresses(output) if output else ()


def read_links(run: CommandRunner) -> tuple[NetworkLink, ...]:
    """Return connected NM data devices, or nothing when nmcli is unavailable."""
    output = run([
        "nmcli", "--terse", "--escape", "yes",
        "--fields", "DEVICE,TYPE,STATE,CONNECTION", "device",
    ])
    return parse_network_links(output) if output else ()
