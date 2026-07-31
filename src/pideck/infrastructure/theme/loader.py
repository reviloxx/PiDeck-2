"""Theme repository with asset resolution and fallback behavior."""

from dataclasses import replace
import logging
from pathlib import Path

from pideck.domain.errors import ThemeValidationError
from pideck.domain.theme import ThemeDefinition

_LOGGER = logging.getLogger(__name__)


class FileThemeRepository:
    """Resolve configured themes and validate their external assets."""

    def __init__(
        self,
        themes: tuple[ThemeDefinition, ...],
        asset_root: Path,
        fallback: ThemeDefinition,
    ) -> None:
        """Create a repository rooted at the configured asset directory."""
        self._themes = {theme.identifier: theme for theme in themes}
        self._asset_root = asset_root
        self._fallback = fallback

    def get(self, identifier: str) -> ThemeDefinition:
        """Return a resolved theme, falling back when assets are unavailable."""
        theme = self._themes.get(identifier)
        if theme is None:
            raise ThemeValidationError(f"Unknown theme: {identifier!r}")
        try:
            return self._resolve(theme)
        except ThemeValidationError:
            _LOGGER.exception("Theme assets are unavailable; using fallback theme=%s", identifier)
            return self._resolve(self._fallback)

    def _resolve(self, theme: ThemeDefinition) -> ThemeDefinition:
        """Resolve and validate all paths referenced by one theme."""
        resolved_icons = {
            name: self._existing_asset(path, f"icon {name!r}")
            for name, path in theme.icons.items()
        }
        resolved_wallpaper = (
            self._existing_asset(theme.wallpaper, "wallpaper")
            if theme.wallpaper is not None
            else None
        )
        return replace(theme, icons=resolved_icons, wallpaper=resolved_wallpaper)

    def _existing_asset(self, path: Path, asset_name: str) -> Path:
        """Resolve one relative asset path and require that it exists."""
        resolved_path = path if path.is_absolute() else self._asset_root / path
        if not resolved_path.is_file():
            raise ThemeValidationError(f"Missing {asset_name} asset: {resolved_path}")
        return resolved_path
