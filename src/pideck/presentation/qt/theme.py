"""Qt styling derived from immutable PiDeck theme tokens."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from pideck.domain.theme import ThemeDefinition


def apply_theme(widget: QWidget, theme: ThemeDefinition) -> None:
    """Apply a theme palette, font, and widget stylesheet."""
    colors = theme.colors
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["background"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    widget.setPalette(palette)
    widget.setFont(QFont(theme.fonts.family, theme.fonts.body_size))
    wallpaper_style = ""
    if theme.wallpaper is not None:
        wallpaper_style = (
            "background-image: url('"
            f"{theme.wallpaper.as_posix()}"
            "'); background-position: center; background-repeat: no-repeat;"
        )
    widget.setStyleSheet(
        """
        QMainWindow, QWidget#launcher_root {
            background-color: %(background)s;
            color: %(text)s;
            %(wallpaper_style)s
        }
        QLabel#launcher_title {
            color: %(text)s;
            font-size: %(heading_size)spx;
            font-weight: 700;
        }
        QLabel#launcher_subtitle, QLabel#empty_state {
            color: %(muted_text)s;
        }
        QPushButton#launcher_tile {
            background-color: %(surface)s;
            border: 2px solid transparent;
            border-radius: 8px;
            color: %(text)s;
            padding: 16px;
            text-align: left;
        }
        QPushButton#launcher_tile:hover, QPushButton#launcher_tile:focus {
            border-color: %(focus)s;
            background-color: %(focus_surface)s;
        }
        QPushButton#launcher_tile:pressed {
            border-color: %(primary)s;
            background-color: %(primary_surface)s;
        }
        QPushButton#launcher_tile[running="true"] {
            border-color: %(running)s;
            background-color: %(running_surface)s;
        }
        QLabel#tile_name {
            color: %(text)s;
            font-size: %(body_size)spx;
            font-weight: 700;
        }
        QLabel#tile_input {
            color: %(muted_text)s;
            font-size: 12px;
        }
        QLabel#tile_icon {
            background: transparent;
        }
        QPushButton#launcher_action {
            background-color: transparent;
            border: 1px solid %(muted_text)s;
            border-radius: 6px;
            color: %(muted_text)s;
            padding: 8px 16px;
        }
        QPushButton#launcher_action:focus, QPushButton#launcher_action:hover {
            border-color: %(focus)s;
            color: %(text)s;
        }
        """
        % {
            "background": colors["background"],
            "surface": colors["surface"],
            "text": colors["text"],
            "muted_text": colors["muted_text"],
            "focus": colors["focus"],
            "primary": colors["primary"],
            "running": colors["running"],
            "heading_size": theme.fonts.heading_size,
            "body_size": theme.fonts.body_size,
            "focus_surface": _blend(colors["surface"], colors["focus"], 0.16),
            "primary_surface": _blend(colors["surface"], colors["primary"], 0.24),
            "running_surface": _blend(colors["surface"], colors["running"], 0.20),
            "wallpaper_style": wallpaper_style,
        }
    )


def configure_tile_effect(
    tile: QWidget,
    theme: ThemeDefinition,
    reduced_motion: bool = False,
) -> None:
    """Add an animated focus glow using the theme focus color."""
    effect = QGraphicsDropShadowEffect(tile)
    effect.setBlurRadius(8)
    effect.setOffset(0, 0)
    effect.setColor(QColor(theme.colors["focus"]))
    effect.setEnabled(False)
    tile.setGraphicsEffect(effect)
    tile.setProperty("focus_effect", effect)
    animation = QPropertyAnimation(effect, b"blurRadius", tile)
    duration = (
        theme.animation.reduced_motion_duration_ms
        if reduced_motion
        else theme.animation.duration_ms
    )
    animation.setDuration(duration)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    tile.setProperty("focus_animation", animation)


def icon_for_path(path: object) -> QIcon:
    """Create a Qt icon from a path-like value, returning an empty icon if absent."""
    return QIcon(str(path)) if path else QIcon()


def _blend(first: str, second: str, amount: float) -> str:
    """Blend two six-digit hex colors for derived stylesheet surfaces."""
    first_color = QColor(first)
    second_color = QColor(second)
    red = round(first_color.red() + (second_color.red() - first_color.red()) * amount)
    green = round(first_color.green() + (second_color.green() - first_color.green()) * amount)
    blue = round(first_color.blue() + (second_color.blue() - first_color.blue()) * amount)
    return QColor(red, green, blue).name()
