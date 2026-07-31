"""Immutable theme models used by the launcher presentation."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .errors import ThemeValidationError


@dataclass(frozen=True, slots=True)
class FontSpec:
    """Describe the font family and the launcher text sizes."""

    family: str
    heading_size: int
    body_size: int

    def __post_init__(self) -> None:
        """Validate font values."""
        if not self.family.strip():
            raise ThemeValidationError("Font family must not be empty")
        if self.heading_size <= 0 or self.body_size <= 0:
            raise ThemeValidationError("Font sizes must be positive")


@dataclass(frozen=True, slots=True)
class TileSpec:
    """Describe the preferred dimensions and spacing of launcher tiles."""

    width: int
    height: int
    gap: int

    def __post_init__(self) -> None:
        """Validate tile dimensions."""
        if min(self.width, self.height) <= 0:
            raise ThemeValidationError("Tile width and height must be positive")
        if self.gap < 0:
            raise ThemeValidationError("Tile gap must not be negative")


@dataclass(frozen=True, slots=True)
class AnimationSpec:
    """Describe theme animation timings in milliseconds."""

    duration_ms: int
    reduced_motion_duration_ms: int

    def __post_init__(self) -> None:
        """Validate animation timings."""
        if min(self.duration_ms, self.reduced_motion_duration_ms) < 0:
            raise ThemeValidationError("Animation durations must not be negative")


@dataclass(frozen=True, slots=True)
class ThemeDefinition:
    """Define all visual tokens and assets required by the launcher."""

    identifier: str
    name: str
    colors: Mapping[str, str]
    fonts: FontSpec
    icons: Mapping[str, Path]
    wallpaper: Path | None
    tile: TileSpec
    animation: AnimationSpec

    def __post_init__(self) -> None:
        """Validate required theme identity and immutable mappings."""
        if not self.identifier.strip():
            raise ThemeValidationError("Theme identifier must not be empty")
        if not self.name.strip():
            raise ThemeValidationError("Theme name must not be empty")
        required_colors = {
            "background",
            "surface",
            "primary",
            "text",
            "muted_text",
            "focus",
            "running",
            "error",
        }
        missing_colors = required_colors.difference(self.colors)
        if missing_colors:
            missing = ", ".join(sorted(missing_colors))
            raise ThemeValidationError(f"Theme is missing colors: {missing}")
        object.__setattr__(self, "colors", MappingProxyType(dict(self.colors)))
        object.__setattr__(self, "icons", MappingProxyType(dict(self.icons)))
