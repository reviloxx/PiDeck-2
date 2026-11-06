"""Immutable models for the release-shipped catalog of installable applications.

Catalog-only data (install methods, first-run defaults, window policy) never
enters ``pideck.yaml``: only the resulting :class:`ApplicationDefinition` is
saved, which keeps user settings readable by older releases.
"""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

from .configuration import ApplicationDefinition, WindowMode
from .errors import ConfigurationValidationError


class InstallMethod(StrEnum):
    """Package source used to install an application on one architecture."""

    APT = "apt"
    FLATPAK = "flatpak"
    DEB = "deb"


class InstallState(StrEnum):
    """Install state of a catalog application on the running device."""

    INSTALLED = "installed"
    NOT_INSTALLED = "not_installed"
    UNAVAILABLE = "unavailable"
    """The application has no package for this device's architecture."""
    REQUIRES_NEWER_IMAGE = "requires_newer_image"
    """Installing or removing needs a system service this image does not provide."""


@dataclass(frozen=True, slots=True)
class InstallSpec:
    """Describe how one application is installed on one architecture.

    ``packages`` are installed and removed with the application, while
    ``dependencies`` are installed alongside but never removed because other
    software may rely on them.
    """

    method: InstallMethod
    packages: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    flatpak: str | None = None
    url: str | None = None
    sha256: str | None = None
    executable: str | None = None
    arguments: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        """Validate method-specific required fields."""
        object.__setattr__(self, "packages", tuple(self.packages))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if self.arguments is not None:
            object.__setattr__(self, "arguments", tuple(self.arguments))
        if self.method is InstallMethod.FLATPAK:
            if not self.flatpak or self.packages or self.dependencies or self.url:
                raise ConfigurationValidationError("Flatpak installs require only a Flatpak application ID")
        elif self.method is InstallMethod.APT:
            if not self.packages or self.flatpak or self.url:
                raise ConfigurationValidationError("apt installs require at least one package")
        elif len(self.packages) != 1 or not self.url or not self.sha256 or self.flatpak:
            raise ConfigurationValidationError("deb installs require one package name, a URL and a SHA-256")


@dataclass(frozen=True, slots=True)
class UserDefault:
    """A first-run file copied into the user's home before an application starts."""

    source: Path
    target: PurePosixPath

    def __post_init__(self) -> None:
        """Reject targets that could escape the user's home directory."""
        if self.target.is_absolute() or ".." in self.target.parts or not self.target.parts:
            raise ConfigurationValidationError(f"Invalid user default target: {self.target}")


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One supported application with per-architecture install instructions."""

    application: ApplicationDefinition
    install: Mapping[str, InstallSpec] = field(default_factory=dict)
    user_defaults: tuple[UserDefault, ...] = ()
    window_mode: WindowMode | None = None

    def __post_init__(self) -> None:
        """Freeze collection fields."""
        object.__setattr__(self, "install", MappingProxyType(dict(self.install)))
        object.__setattr__(self, "user_defaults", tuple(self.user_defaults))

    @property
    def identifier(self) -> str:
        """Return the application identifier shared with ``pideck.yaml``."""
        return self.application.identifier

    @property
    def name(self) -> str:
        """Return the display name."""
        return self.application.name

    def spec_for(self, architecture: str) -> InstallSpec | None:
        """Return the install instructions for a Debian architecture, if any."""
        return self.install.get(architecture)

    def definition_for(self, architecture: str) -> ApplicationDefinition:
        """Return the launch definition, including architecture-specific overrides."""
        spec = self.spec_for(architecture)
        if spec is None:
            return self.application
        definition = self.application
        if spec.executable is not None:
            definition = replace(definition, executable=spec.executable)
        if spec.arguments is not None:
            definition = replace(definition, arguments=spec.arguments)
        return definition
