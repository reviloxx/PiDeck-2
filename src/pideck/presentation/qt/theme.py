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
        QMainWindow, QDialog#settings_window, QWidget#launcher_root, QWidget#settings_root {
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
        QLabel#tile_spinner {
            color: %(primary)s;
            font-size: %(body_size)spx;
            font-weight: 700;
            min-width: 20px;
        }
        QPushButton#launcher_action, QPushButton#settings_footer, QPushButton#settings_back {
            background-color: %(surface)s;
            border: 1px solid %(muted_text)s;
            border-radius: 7px;
            color: %(text)s;
            min-height: 42px;
            min-width: 130px;
            padding: 0 18px;
        }
        QPushButton#launcher_action:focus, QPushButton#launcher_action:hover,
        QPushButton#settings_footer:focus, QPushButton#settings_footer:hover,
        QPushButton#settings_back:focus, QPushButton#settings_back:hover {
            background-color: %(active_surface)s;
            border: 2px solid %(focus)s;
        }
        QFrame#settings_sidebar {
            background-color: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 12px;
        }
        QLabel#settings_brand {
            color: %(text)s;
            font-size: %(heading_size)spx;
            font-weight: 700;
        }
        QLabel#settings_sidebar_hint, QLabel#settings_description, QLabel#settings_hint {
            color: %(muted_text)s;
        }
        QPushButton#settings_nav {
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 7px;
            color: %(muted_text)s;
            padding: 14px 16px;
            text-align: left;
        }
        QPushButton#settings_nav:hover, QPushButton#settings_nav:focus,
        QPushButton#settings_nav[active="true"] {
            background-color: %(active_surface)s;
            border-color: %(focus)s;
            color: %(text)s;
        }
        QFrame#settings_content {
            background-color: %(content_surface)s;
            border: 1px solid %(border)s;
            border-radius: 12px;
        }
        QLabel#settings_kicker {
            color: %(primary)s;
            font-size: 12px;
            font-weight: 700;
        }
        QLabel#settings_heading {
            color: %(text)s;
            font-size: %(heading_size)spx;
            font-weight: 700;
        }
        QFrame#settings_row {
            background-color: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 8px;
        }
        QFrame#settings_row:focus {
            background-color: %(active_surface)s;
            border: 2px solid %(focus)s;
        }
        QLabel#settings_label {
            color: %(text)s;
            font-size: %(body_size)spx;
            font-weight: 700;
        }
        QComboBox#settings_control {
            background-color: %(surface)s;
            border: 1px solid %(muted_text)s;
            border-radius: 6px;
            color: %(text)s;
            min-height: 42px;
            padding: 0 12px;
        }
        QComboBox#settings_control:focus {
            border: 2px solid %(focus)s;
        }
        QComboBox#settings_control QAbstractItemView {
            background-color: %(surface)s;
            border: 1px solid %(focus)s;
            color: %(text)s;
            selection-background-color: %(active_surface)s;
            selection-color: %(text)s;
        }
        QCheckBox#settings_control {
            color: %(text)s;
            spacing: 10px;
        }
        QCheckBox#settings_control::indicator {
            background-color: %(background)s;
            border: 2px solid %(muted_text)s;
            border-radius: 4px;
            height: 24px;
            width: 24px;
        }
        QCheckBox#settings_control::indicator:checked {
            background-color: %(primary)s;
            border-color: %(primary)s;
        }
        QListWidget#settings_applications {
            background-color: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 8px;
            color: %(text)s;
            font-size: %(body_size)spx;
            outline: none;
            padding: 8px;
        }
        QListWidget#settings_applications::item {
            border: 1px solid transparent;
            border-radius: 6px;
            padding: 12px;
        }
        QListWidget#settings_applications::item:selected,
        QListWidget#settings_applications::item:focus {
            background-color: %(active_surface)s;
            border-color: %(focus)s;
        }
        QLabel#settings_error {
            color: %(error)s;
            font-weight: 700;
        }
        """
        % {
            "background": colors["background"],
            "surface": colors["surface"],
            "content_surface": _blend(colors["background"], colors["surface"], 0.45),
            "border": _blend(colors["surface"], colors["text"], 0.16),
            "text": colors["text"],
            "muted_text": colors["muted_text"],
            "focus": colors["focus"],
            "primary": colors["primary"],
            "running": colors["running"],
            "error": colors["error"],
            "heading_size": theme.fonts.heading_size,
            "body_size": theme.fonts.body_size,
            "focus_surface": _blend(colors["surface"], colors["focus"], 0.16),
            "primary_surface": _blend(colors["surface"], colors["primary"], 0.24),
            "running_surface": _blend(colors["surface"], colors["running"], 0.20),
            "active_surface": _blend(colors["surface"], colors["focus"], 0.18),
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
