"""Detect installed packages from local databases without running subprocesses.

Reading dpkg's status file and Flatpak's deployment directories is fast
enough to run on the Qt thread when the settings menu opens.
"""

from collections.abc import Iterable
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import platform

from pideck.domain.catalog import InstallMethod, InstallSpec

_LOGGER = logging.getLogger(__name__)
_DEBIAN_ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def device_architecture(machine: str | None = None) -> str:
    """Return the Debian architecture name used as catalog key."""
    machine = machine or platform.machine()
    return _DEBIAN_ARCHITECTURES.get(machine.lower(), machine.lower())


def _user_flatpak_root() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(data) / "flatpak"


@dataclass(frozen=True, slots=True)
class InstalledPackages:
    """Answer install-state questions from dpkg and Flatpak on-disk state."""

    dpkg_status: Path = Path("/var/lib/dpkg/status")
    system_flatpak: Path = Path("/var/lib/flatpak")
    user_flatpak: Path | None = None

    def is_installed(self, spec: InstallSpec) -> bool:
        """Return whether all of an application's own packages are installed."""
        if spec.method is InstallMethod.FLATPAK:
            assert spec.flatpak is not None
            return self.flatpak_scope(spec.flatpak) is not None
        return self.debian_installed(spec.packages)

    def flatpak_scope(self, app_id: str) -> str | None:
        """Return ``"system"`` or ``"user"`` for an installed Flatpak, else ``None``."""
        user_root = self.user_flatpak or _user_flatpak_root()
        for scope, root in (("system", self.system_flatpak), ("user", user_root)):
            if (root / "app" / app_id / "current" / "active").exists():
                return scope
        return None

    def debian_installed(self, packages: Iterable[str]) -> bool:
        """Return whether every named Debian package is fully installed."""
        wanted = set(packages)
        return bool(wanted) and wanted <= self._installed_debian_packages(wanted)

    def _installed_debian_packages(self, wanted: set[str]) -> set[str]:
        try:
            content = self.dpkg_status.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            _LOGGER.debug("dpkg status unavailable: %s", error)
            return set()
        installed: set[str] = set()
        for stanza in content.split("\n\n"):
            name = status = None
            for line in stanza.splitlines():
                if line.startswith("Package: "):
                    name = line[9:].strip()
                elif line.startswith("Status: "):
                    status = line[8:].split()
            if name in wanted and status is not None and status[-1] == "installed":
                installed.add(name)
        return installed
