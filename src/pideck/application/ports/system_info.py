"""Port for collecting read-only system information."""

from typing import Protocol

from pideck.domain.system_info import SystemInfo


class SystemInfoGateway(Protocol):
    """Collect hardware, driver and software facts from the host platform."""

    def inspect(self) -> SystemInfo:
        """Return a fresh snapshot; may block briefly and must not raise for missing data."""
        ...
