"""Linux timezone control through timedatectl and the system zoneinfo database."""

from collections.abc import Callable
from pathlib import Path
import shutil
import subprocess
from zoneinfo import available_timezones

from pideck.application.ports.timezone import TimezoneError


class TimedatectlTimezoneGateway:
    """Read and change the host timezone without depending on Qt."""

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
        zones: Callable[[], set[str]] = available_timezones,
        timezone_file: Path = Path("/etc/timezone"),
    ) -> None:
        """Create a gateway; ``runner``, ``which``, and ``zones`` are injectable for tests."""
        self._runner = runner
        self._which = which
        self._zones = zones
        self._timezone_file = timezone_file

    def current_timezone(self) -> str:
        """Return the active IANA timezone, falling back to ``/etc/timezone``."""
        executable = self._which("timedatectl")
        if executable is not None:
            try:
                result = self._runner(
                    [executable, "show", "-p", "Timezone", "--value"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise TimezoneError(f"Unable to read the system timezone: {error}") from error
            if result.returncode == 0:
                timezone = result.stdout.strip()
                if timezone:
                    return timezone
        try:
            timezone = self._timezone_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise TimezoneError(f"Unable to read the system timezone: {error}") from error
        if not timezone:
            raise TimezoneError("Unable to read the system timezone")
        return timezone

    def list_timezones(self) -> tuple[str, ...]:
        """Return sorted IANA timezone identifiers from the local zoneinfo database."""
        return tuple(sorted(self._zones()))

    def set_timezone(self, timezone: str) -> None:
        """Apply ``timezone`` through timedatectl."""
        if timezone not in self._zones():
            raise TimezoneError(f"Unknown timezone: {timezone}")
        executable = self._which("timedatectl")
        if executable is None:
            raise TimezoneError("timedatectl is unavailable")
        try:
            result = self._runner(
                [executable, "set-timezone", timezone],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise TimezoneError(f"Unable to set the system timezone: {error}") from error
        if result.returncode:
            detail = result.stderr.strip() or f"timedatectl exited with status {result.returncode}"
            raise TimezoneError(f"Unable to set the system timezone: {detail}")
