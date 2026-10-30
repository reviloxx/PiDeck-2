"""Network operations used by appliance setup without depending on Qt."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WirelessNetwork:
    """Represent one visible wireless network on a particular device."""

    ssid: str
    device: str
    security: str
    signal: int
    connected: bool = False

    @property
    def requires_key(self) -> bool:
        """Return whether this network requires credentials."""
        return bool(self.security and self.security != "--")

    @property
    def supported(self) -> bool:
        """Identify open and personal WPA networks supported by setup."""
        if not self.requires_key:
            return True
        upper = self.security.upper()
        return "WPA" in upper and "802.1X" not in upper


class NetworkError(Exception):
    """Report a network operation that could not be completed."""


class NetworkGateway(Protocol):
    """Provide the network actions needed by the settings presentation."""

    def connected(self) -> bool:
        """Return whether NetworkManager has an active network connection."""
        ...

    def scan(self) -> tuple[WirelessNetwork, ...]:
        """Find visible wireless networks."""
        ...

    def connect(self, network: WirelessNetwork, key: str | None) -> None:
        """Connect to a selected network, optionally using its secret key."""
        ...
