"""Linux systemd power control for appliance sessions."""

import subprocess

from pideck.domain.errors import PowerError


class SystemPower:
    """Request a noninteractive power-off through systemd."""

    def power_off(self) -> None:
        """Request power-off and report rejected or unavailable requests."""
        try:
            result = subprocess.run(
                ["systemctl", "poweroff", "--no-block", "--no-ask-password"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PowerError(f"Unable to request system power-off: {error}") from error
        if result.returncode:
            detail = result.stderr.strip() or f"systemctl exited with status {result.returncode}"
            raise PowerError(f"Unable to request system power-off: {detail}")
