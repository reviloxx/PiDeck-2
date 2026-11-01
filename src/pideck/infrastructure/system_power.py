"""Linux systemd power control for appliance sessions."""

import subprocess

from pideck.domain.errors import PowerError


class SystemPower:
    """Request noninteractive power operations through systemd."""

    def power_off(self) -> None:
        """Request power-off and report rejected or unavailable requests."""
        self._request("poweroff", "power-off")

    def restart(self) -> None:
        """Request a system restart and report rejected or unavailable requests."""
        self._request("reboot", "restart")

    def _request(self, action: str, description: str) -> None:
        try:
            result = subprocess.run(
                ["systemctl", action, "--no-block", "--no-ask-password"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PowerError(f"Unable to request system {description}: {error}") from error
        if result.returncode:
            detail = result.stderr.strip() or f"systemctl exited with status {result.returncode}"
            raise PowerError(f"Unable to request system {description}: {detail}")
