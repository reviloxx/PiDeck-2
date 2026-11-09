"""User preferences for the launcher's HDMI-CEC input."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CecSettings:
    """Describe CEC connection, direct-TV address, and switching preferences."""

    enabled: bool = True
    auto_connect: bool = True
    hdmi_port: int | None = None
    switch_input: bool = True

    def __post_init__(self) -> None:
        """Reject invalid settings rather than passing them to an adapter."""
        if any(type(value) is not bool for value in (self.enabled, self.auto_connect, self.switch_input)):
            raise ValueError("CEC enabled, auto-connect, and switch-input must be boolean")
        if self.hdmi_port is not None and (
            type(self.hdmi_port) is not int or not 1 <= self.hdmi_port <= 15
        ):
            raise ValueError("CEC HDMI port must be automatic or between 1 and 15")
