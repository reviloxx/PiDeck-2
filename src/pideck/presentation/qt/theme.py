"""Qt styling derived from immutable PiDeck theme tokens."""

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPaintEvent, QPainter, QPalette, QPolygon
from PySide6.QtWidgets import QComboBox, QGraphicsDropShadowEffect, QStyledItemDelegate, QWidget

from pideck.domain.theme import ThemeDefinition

from .spinner import BusySpinner
from .toggle_switch import ToggleSwitch
from .wallpaper import WallpaperDialog, WallpaperWidget


class SettingsComboBox(QComboBox):
    """Draw a theme-colored arrow inside the uninterrupted settings control frame."""

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the standard control followed by its dropdown indicator."""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.ButtonText))
        x = self.width() - 18
        y = self.height() // 2
        painter.drawPolygon(QPolygon([QPoint(x - 5, y - 2), QPoint(x + 5, y - 2), QPoint(x, y + 3)]))


def configure_combo_box(combo: QComboBox) -> None:
    """Use a list delegate that renders the themed popup selection and item size."""
    view = combo.view()
    view.setItemDelegate(QStyledItemDelegate(view))
    combo.setMaxVisibleItems(6)


def theme_font(theme: ThemeDefinition, pixel_size: int) -> QFont:
    """Return the theme font family at an exact pixel size."""
    font = QFont(theme.fonts.family)
    font.setPixelSize(pixel_size)
    return font


def apply_theme(widget: QWidget, theme: ThemeDefinition) -> None:
    """Apply a theme palette, font, and widget stylesheet.

    Only ``:focus`` is styled, never ``:hover``: the pointer moves focus
    instead (see ``cursor_visibility``), so exactly one control is highlighted.
    Theme font sizes are pixels. The stylesheet's root rule sets the family
    and size for every widget, so re-applying a theme after a settings change
    yields exactly the same fonts as the first application.
    """
    colors = theme.colors
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["background"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["primary"]))
    widget.setPalette(palette)
    widget.setFont(theme_font(theme, theme.fonts.body_size))
    for background in widget.findChildren(WallpaperWidget):
        background.set_wallpaper(theme.wallpaper, colors["background"])
    if isinstance(widget, WallpaperDialog):
        widget.set_wallpaper(theme.wallpaper, colors["background"])
    switch_off = _blend(colors["surface"], colors["text"], 0.22)
    for switch in widget.findChildren(ToggleSwitch):
        switch.set_colors(colors["primary"], switch_off, colors["text"], colors["text"])
    for spinner in widget.findChildren(BusySpinner):
        spinner.set_color(colors["primary"])
    widget.setStyleSheet(
        """
        QWidget {
            font-family: "%(font_family)s";
            font-size: %(body_size)spx;
        }
        QPushButton {
            outline: none;
        }
        QScrollBar:vertical {
            background: transparent;
            border: none;
            margin: 0;
            width: 10px;
        }
        QScrollBar::handle:vertical {
            background-color: %(scroll_handle)s;
            border-radius: 5px;
            min-height: 48px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            border: none;
            background: none;
            height: 0;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }
        QMainWindow, QDialog#settings_window, QDialog#password_prompt {
            background-color: %(background)s;
            color: %(text)s;
        }
        QDialog QWidget {
            font-size: %(settings_body_size)spx;
        }
        QLabel#launcher_title {
            color: %(text)s;
            font-size: %(heading_size)spx;
            font-weight: 700;
        }
        QLabel#launcher_clock {
            color: %(muted_text)s;
            font-size: %(heading_size)spx;
            font-weight: 700;
            padding-top: 4px;
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
        QPushButton#launcher_tile:focus {
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
        QLabel#tile_input, QLabel#tile_icon {
            background: transparent;
        }
        QPushButton#launcher_action, QPushButton#settings_footer, QPushButton#settings_back {
            background-color: %(surface)s;
            border: 1px solid %(muted_text)s;
            border-radius: 7px;
            color: %(text)s;
            min-height: 42px;
            min-width: 130px;
            padding: 1px 19px;
        }
        QPushButton#launcher_action:focus, QPushButton#settings_footer:focus,
        QPushButton#settings_back:focus {
            background-color: %(active_surface)s;
            border: 2px solid %(focus)s;
            padding: 0 18px;
        }
        QPushButton#launcher_action:disabled, QPushButton#settings_footer:disabled,
        QPushButton#settings_back:disabled {
            border-color: %(border)s;
            color: %(muted_text)s;
        }
        QPushButton#network_key {
            background-color: %(surface)s;
            border: 1px solid %(muted_text)s;
            border-radius: 6px;
            color: %(text)s;
            min-height: 42px;
            min-width: 42px;
            padding: 1px;
        }
        QPushButton#network_key:focus {
            background-color: %(active_surface)s;
            border: 2px solid %(focus)s;
            padding: 0;
        }
        QFrame#settings_sidebar {
            background-color: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 12px;
        }
        QLabel#settings_brand {
            color: %(text)s;
            font-size: %(settings_heading_size)spx;
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
            padding: 8px 16px;
            text-align: left;
        }
        QPushButton#settings_nav[active="true"] {
            background-color: %(content_surface)s;
            border-color: transparent;
            color: %(text)s;
        }
        QPushButton#settings_nav:focus {
            background-color: %(active_surface)s;
            border-color: %(focus)s;
            color: %(text)s;
        }
        QFrame#settings_content, QFrame#system_settings_panel {
            background-color: %(content_surface)s;
            border: 1px solid %(border)s;
            border-radius: 12px;
        }
        QDialog#session_overlay {
            background-color: %(background)s;
        }
        QFrame#session_overlay_card {
            background-color: %(overlay_card)s;
            border: 1px solid %(overlay_border)s;
            border-radius: 20px;
        }
        QScrollArea#settings_scroll, QScrollArea#settings_scroll > QWidget > QWidget {
            background: transparent;
            border: none;
        }
        QLabel#settings_kicker {
            color: %(primary)s;
            font-size: %(settings_body_size)spx;
            font-weight: 700;
        }
        QLabel#settings_heading {
            color: %(text)s;
            font-size: %(settings_heading_size)spx;
            font-weight: 700;
        }
        QFrame#settings_row {
            background-color: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 8px;
            padding: 1px;
        }
        QFrame#settings_row:focus {
            background-color: %(active_surface)s;
            border: 2px solid %(focus)s;
            padding: 0;
        }
        QLabel#settings_chevron {
            color: %(muted_text)s;
            font-size: %(settings_heading_size)spx;
        }
        QLabel#settings_label, QLabel#system_info_value {
            color: %(text)s;
            font-size: %(settings_body_size)spx;
            font-weight: 700;
        }
        QComboBox#settings_control {
            background-color: %(surface)s;
            border: 1px solid %(muted_text)s;
            border-radius: 6px;
            combobox-popup: 0;
            color: %(text)s;
            font-size: %(settings_body_size)spx;
            min-height: 42px;
            padding: 0 12px;
            padding-right: 40px;
        }
        QComboBox#settings_control:focus {
            border: 2px solid %(focus)s;
        }
        QComboBox#settings_control::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 30px;
            border: none;
            background: transparent;
        }
        QComboBox#settings_control QAbstractItemView {
            background-color: %(surface)s;
            border: 1px solid %(focus)s;
            color: %(text)s;
            font-size: %(settings_body_size)spx;
            selection-background-color: %(active_surface)s;
            selection-color: %(text)s;
        }
        QComboBox#settings_control QAbstractItemView::item {
            min-height: 42px;
            padding: 4px 12px;
        }
        QComboBox#settings_control QAbstractItemView::item:selected {
            background-color: %(active_surface)s;
            color: %(text)s;
        }
        QSlider#system_volume {
            background-color: %(content_surface)s;
            min-height: 28px;
        }
        QSlider#system_volume::groove:horizontal,
        QSlider#system_volume::add-page:horizontal {
            background-color: %(border)s;
            border-radius: 3px;
            height: 6px;
        }
        QSlider#system_volume::sub-page:horizontal {
            background-color: %(primary)s;
            border-radius: 3px;
        }
        QSlider#system_volume::handle:horizontal {
            background-color: %(primary)s;
            border: 2px solid %(focus)s;
            border-radius: 10px;
            margin: -7px 0;
            width: 20px;
        }
        QSlider#system_volume:focus {
            border: 1px solid %(focus)s;
            border-radius: 6px;
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
        QListWidget#settings_applications, QListWidget#settings_catalog {
            background-color: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 8px;
            color: %(text)s;
            font-size: %(settings_body_size)spx;
            outline: none;
            padding: 8px;
        }
        QListWidget#settings_applications::item, QListWidget#settings_catalog::item {
            border: 1px solid transparent;
            border-radius: 6px;
            padding: 12px;
        }
        QListWidget#settings_applications::item:selected,
        QListWidget#settings_applications::item:focus,
        QListWidget#settings_catalog::item:selected,
        QListWidget#settings_catalog::item:focus {
            background-color: %(active_surface)s;
            border-color: %(focus)s;
            color: %(text)s;
        }
        QListWidget#settings_applications::indicator {
            background-color: %(background)s;
            border: 2px solid %(muted_text)s;
            border-radius: 4px;
            height: 22px;
            margin-right: 12px;
            width: 22px;
        }
        QListWidget#settings_applications::indicator:checked {
            background-color: %(primary)s;
            border-color: %(primary)s;
        }
        QProgressBar#install_progress {
            background-color: %(background)s;
            border: 1px solid %(border)s;
            border-radius: 5px;
            max-height: 10px;
            min-height: 10px;
        }
        QProgressBar#install_progress::chunk {
            background-color: %(primary)s;
            border-radius: 4px;
        }
        QLabel#system_status {
            color: %(text)s;
        }
        QLabel#system_status[error="true"], QLabel#settings_error {
            color: %(error)s;
            font-weight: 700;
        }
        QLineEdit#password_input {
            background-color: %(surface)s;
            border: 1px solid %(muted_text)s;
            border-radius: 6px;
            color: %(text)s;
            min-height: 42px;
            padding: 0 12px;
        }
        QLineEdit#password_input:focus {
            border: 2px solid %(focus)s;
        }
        QTextBrowser#release_notes {
            background-color: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 8px;
            color: %(text)s;
            font-size: %(settings_body_size)spx;
            padding: 12px;
        }
        QTextBrowser#release_notes:focus {
            border: 2px solid %(focus)s;
            padding: 11px;
        }
        QFrame#update_row {
            background-color: %(surface)s;
            border: 1px solid %(border)s;
            border-radius: 8px;
            min-height: 54px;
            padding: 1px;
        }
        QFrame#update_row:focus {
            background-color: %(active_surface)s;
            border: 2px solid %(focus)s;
            padding: 0;
        }
        QFrame#update_row[sorting="true"] {
            background-color: %(active_surface)s;
            border: 2px dashed %(primary)s;
            padding: 0;
        }
        QLabel#update_status {
            color: %(muted_text)s;
            min-width: 150px;
        }
        QLabel#update_spinner {
            color: %(primary)s;
            font-weight: 700;
        }
        QPushButton#update_action {
            background-color: transparent;
            border: 1px solid %(muted_text)s;
            border-radius: 6px;
            color: %(text)s;
            min-height: 34px;
            min-width: 92px;
            padding: 1px;
        }
        QPushButton#update_action:focus,
        QFrame#update_row:focus QPushButton#update_action {
            background-color: %(active_surface)s;
            border: 2px solid %(focus)s;
            padding: 0;
        }
        QLabel#tile_name, QLabel#launcher_title, QLabel#launcher_clock, QLabel#launcher_subtitle {
            background: transparent;
        }
        """
        % {
            "background": colors["background"],
            "surface": colors["surface"],
            "content_surface": _blend(colors["background"], colors["surface"], 0.45),
            "border": _blend(colors["surface"], colors["text"], 0.16),
            "overlay_card": _rgba(colors["surface"], 0.82),
            "overlay_border": _rgba(colors["text"], 0.18),
            "text": colors["text"],
            "muted_text": colors["muted_text"],
            "focus": colors["focus"],
            "primary": colors["primary"],
            "running": colors["running"],
            "error": colors["error"],
            "font_family": theme.fonts.family.replace('"', ""),
            "scroll_handle": _blend(colors["surface"], colors["text"], 0.30),
            "heading_size": theme.fonts.heading_size,
            "body_size": theme.fonts.body_size,
            "settings_body_size": theme.fonts.settings_body_size,
            "settings_heading_size": theme.fonts.settings_heading_size,
            "focus_surface": _blend(colors["surface"], colors["focus"], 0.16),
            "primary_surface": _blend(colors["surface"], colors["primary"], 0.24),
            "running_surface": _blend(colors["surface"], colors["running"], 0.20),
            "active_surface": _blend(colors["surface"], colors["focus"], 0.18),
        }
    )


class FocusGlow(QObject):
    """Animate a theme-colored glow while the watched widget has focus.

    Launcher tiles and launcher footer actions share this behaviour so every
    focusable home-screen control signals focus the same way.
    """

    _RESTING_RADIUS = 8
    _FOCUSED_RADIUS = 26

    def __init__(self, widget: QWidget, theme: ThemeDefinition, reduced_motion: bool = False) -> None:
        """Attach a disabled drop shadow and its animation to ``widget``."""
        super().__init__(widget)
        self._effect = QGraphicsDropShadowEffect(widget)
        self._effect.setBlurRadius(self._RESTING_RADIUS)
        self._effect.setOffset(0, 0)
        self._effect.setColor(QColor(theme.colors["focus"]))
        self._effect.setEnabled(False)
        widget.setGraphicsEffect(self._effect)
        self._animation = QPropertyAnimation(self._effect, b"blurRadius", widget)
        self._animation.setDuration(
            theme.animation.reduced_motion_duration_ms if reduced_motion else theme.animation.duration_ms
        )
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._disable_when_resting)
        widget.setProperty("focus_effect", self._effect)
        widget.setProperty("focus_animation", self._animation)
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Grow the glow on focus-in and fade it on focus-out."""
        if event.type() == QEvent.Type.FocusIn:
            self._effect.setEnabled(True)
            self._animate_to(self._FOCUSED_RADIUS)
        elif event.type() == QEvent.Type.FocusOut:
            self._animate_to(self._RESTING_RADIUS)
        return False

    def _animate_to(self, radius: int) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._effect.blurRadius())
        self._animation.setEndValue(radius)
        self._animation.start()

    def _disable_when_resting(self) -> None:
        if self._animation.endValue() == self._RESTING_RADIUS:
            self._effect.setEnabled(False)


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


def _rgba(color: str, alpha: float) -> str:
    """Format a theme color as an ``rgba()`` stylesheet value."""
    value = QColor(color)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {alpha:.2f})"
