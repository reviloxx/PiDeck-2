"""Render theme-colored background styles from shared SVG templates.

Uses the Qt SVG image plugin (``QPixmap`` / ``QImageReader``) rather than
``PySide6.QtSvg``, which Debian's appliance packages do not ship.
"""

from __future__ import annotations

import logging
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap

from pideck.domain.backgrounds import BACKGROUND_STYLE_ID_SET, DEFAULT_BACKGROUND

_LOGGER = logging.getLogger(__name__)

_WALLPAPER_WIDTH = 1920
_WALLPAPER_HEIGHT = 1080


def normalize_background(style_id: str | None) -> str:
    """Return a known background style id, falling back to the default."""
    if style_id and style_id in BACKGROUND_STYLE_ID_SET:
        return style_id
    return DEFAULT_BACKGROUND


def background_template_path(style_id: str, asset_root: Path | None = None) -> Path | None:
    """Locate the SVG template for one background style."""
    style = normalize_background(style_id)
    candidates: list[Path] = []
    if asset_root is not None:
        root = asset_root.resolve()
        candidates.extend(
            (
                root / "assets" / "themes" / "backgrounds" / f"{style}.svg",
                root / "themes" / "backgrounds" / f"{style}.svg",
                (root / ".." / "assets" / "themes" / "backgrounds" / f"{style}.svg").resolve(),
            )
        )
    else:
        candidates.append(
            Path(__file__).resolve().parents[4]
            / "assets"
            / "themes"
            / "backgrounds"
            / f"{style}.svg"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def render_background_pixmap(
    style_id: str,
    colors: Mapping[str, str],
    asset_root: Path | None = None,
) -> QPixmap:
    """Render a wallpaper pixmap tinted with the active theme colors."""
    style = normalize_background(style_id)
    color_items = tuple(
        sorted(
            (key, colors[key])
            for key in ("background", "surface", "primary", "running", "focus")
            if key in colors
        )
    )
    pixmap = _cached_render(style, color_items, str(asset_root.resolve()) if asset_root else "")
    return QPixmap(pixmap)


@lru_cache(maxsize=64)
def _cached_render(style: str, color_items: tuple[tuple[str, str], ...], asset_root: str) -> QPixmap:
    """Cache rendered wallpapers by style, colors, and asset root."""
    return _render(style, dict(color_items), Path(asset_root) if asset_root else None)


def _render(style: str, colors: Mapping[str, str], asset_root: Path | None) -> QPixmap:
    """Load, tint, and rasterize one background template via the SVG image plugin."""
    path = background_template_path(style, asset_root)
    if path is None:
        _LOGGER.warning("Missing background template for style=%s", style)
        return QPixmap()
    try:
        template = path.read_text(encoding="utf-8")
    except OSError as error:
        _LOGGER.warning("Unable to read background template %s: %s", path, error)
        return QPixmap()
    replacements = {
        "__BACKGROUND__": colors.get("background", "#000000"),
        "__SURFACE__": colors.get("surface", "#111111"),
        "__PRIMARY__": colors.get("primary", "#ffffff"),
        "__RUNNING__": colors.get("running", colors.get("primary", "#ffffff")),
        "__FOCUS__": colors.get("focus", colors.get("primary", "#ffffff")),
    }
    svg_text = template
    for placeholder, value in replacements.items():
        svg_text = svg_text.replace(placeholder, value)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="pideck-bg-", suffix=".svg", delete=False,
        ) as handle:
            handle.write(svg_text.encode("utf-8"))
            temporary = Path(handle.name)
        pixmap = QPixmap(str(temporary))
        if pixmap.isNull():
            _LOGGER.warning("Unable to rasterize background SVG for style=%s", style)
            return QPixmap()
        target = QSize(_WALLPAPER_WIDTH, _WALLPAPER_HEIGHT)
        if pixmap.size() != target:
            pixmap = pixmap.scaled(
                target,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pixmap
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
