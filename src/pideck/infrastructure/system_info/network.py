"""List globally routable interface addresses using iproute2's JSON output."""

import json
import logging

from pideck.domain.system_info import NetworkAddress

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


def read_addresses(run: CommandRunner) -> tuple[NetworkAddress, ...]:
    """Return current interface addresses, or nothing when iproute2 is unavailable."""
    output = run(["ip", "-j", "address", "show"])
    return parse_ip_addresses(output) if output else ()
