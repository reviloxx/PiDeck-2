"""Fullscreen, controller-friendly settings surface with a category rail."""

from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QDialog,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pideck.application.settings import SettingsUpdate
from pideck.application.app_management import CatalogItemStatus
from pideck.application.pideck_updates import PiDeckUpdateInfo, PiDeckUpdateStatus
from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.installation import InstallProgress
from pideck.application.updates import ApplicationUpdateService
from pideck.domain.configuration import Configuration
from pideck.domain.input_settings import CecSettings
from pideck.domain.keyboard_layout import KeyboardLayout
from pideck.domain.theme import ThemeDefinition

from .theme import SettingsComboBox, apply_theme, configure_combo_box
from .wallpaper import WallpaperWidget
from .localization import format_timestamp, tr
from .applications_page import ApplicationsPage
from .release_notes_view import ReleaseNotesView
from .update_rows import PasswordPrompt, PiDeckUpdateRow, UpdateRow

__all__ = ["PasswordPrompt", "PiDeckUpdateRow", "SettingsWindow", "UpdateRow"]
from .power_dialog import ConfirmationDialog, PowerDialog
from .toggle_switch import ToggleSwitch

_ControlT = TypeVar("_ControlT", bound=QWidget)


class SettingsRow(QFrame):
    """Focusable settings row that exposes console-style directional input."""

    navigation_requested = Signal(object)
    activation_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Route directional keys without moving focus into child controls."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activation_requested.emit()
            event.accept()
            return
        direction_by_key = {
            Qt.Key.Key_Left: Qt.Key.Key_Left,
            Qt.Key.Key_Right: Qt.Key.Key_Right,
            Qt.Key.Key_Up: Qt.Key.Key_Up,
            Qt.Key.Key_Down: Qt.Key.Key_Down,
        }
        key = direction_by_key.get(event.key())
        if key in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            self.navigation_requested.emit(key)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Focus the complete row when it is clicked."""
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)


class ActionSettingsRow(SettingsRow):
    """Activate a complete settings row when clicked with the primary mouse button."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Focus the row and capture a primary-button press."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Activate only when the primary button is released inside the row."""
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activation_requested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SettingsWindow(QDialog):
    """Edit launcher settings in a fullscreen, controller-friendly menu."""

    settings_submitted = Signal(object)
    settings_changed = Signal(object)
    pideck_update_requested = Signal()
    pideck_check_requested = Signal()
    network_requested = Signal()
    audio_requested = Signal()
    drives_requested = Signal()
    display_requested = Signal()
    bluetooth_requested = Signal()
    system_info_requested = Signal()
    power_requested = Signal(str)
    application_install_requested = Signal(str)
    application_remove_requested = Signal(str)
    application_visibility_requested = Signal(str, bool)
    application_order_requested = Signal(tuple)
    input_settings_changed = Signal(object)
    keyboard_layout_changed = Signal(object)
    cec_connection_requested = Signal(bool)

    def __init__(
        self,
        configuration: Configuration,
        theme: ThemeDefinition,
        asset_root: Path,
        parent: QWidget | None = None,
        update_service: ApplicationUpdateService | None = None,
        pideck_update_info: PiDeckUpdateInfo | None = None,
        network_enabled: bool = False,
        system_enabled: bool = False,
        build_date: str | None = None,
        catalog_statuses: tuple[CatalogItemStatus, ...] | None = None,
        input_settings: CecSettings | None = None,
        keyboard_layout: KeyboardLayout | None = None,
    ) -> None:
        """Build the settings menu from the current validated configuration."""
        super().__init__(parent)
        self._keyboard_layout = keyboard_layout
        self._configuration = configuration
        self._catalog_statuses = catalog_statuses
        self._theme = theme
        self._language = configuration.settings.language
        self._asset_root = asset_root
        self._update_service = update_service
        self._pideck_info = pideck_update_info
        self._build_date = build_date
        self._network_enabled = network_enabled
        self._pideck_page = 2 if pideck_update_info is not None else None
        self._network_page = (
            2 + int(self._pideck_page is not None)
            if network_enabled and not system_enabled else None
        )
        self._system_page = (
            2 + int(self._pideck_page is not None) + int(self._network_page is not None) if system_enabled else None
        )
        self._input_settings = input_settings
        self._input_page = (
            2 + int(self._pideck_page is not None) + int(self._network_page is not None)
            + int(self._system_page is not None)
            if input_settings is not None else None
        )
        self._pideck_row: PiDeckUpdateRow | None = None
        self._power_dialog: ConfirmationDialog | None = None
        self._nav_buttons: list[QPushButton] = []
        self._page_controls: dict[int, list[QWidget]] = {}
        self._appearance_row_layouts: list[QBoxLayout] = []
        self._current_page = 0
        self.setWindowTitle(tr(self._language, "settings"))
        self.setObjectName("settings_window")
        self.setModal(True)
        self._build_ui()
        apply_theme(self, theme)
        self._theme_combo.currentIndexChanged.connect(self._emit_current_settings)
        self._reduced_motion.toggled.connect(self._emit_current_settings)
        self._show_clock.toggled.connect(self._emit_current_settings)
        self._language_combo.currentIndexChanged.connect(self._handle_language_changed)
        self._select_page(0, focus_first=False)
        self.apply_language(self._language)
        self._nav_buttons[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Stack appearance controls when the available content width is narrow."""
        super().resizeEvent(event)
        direction = (
            QBoxLayout.Direction.TopToBottom
            if event.size().width() < 1080
            else QBoxLayout.Direction.LeftToRight
        )
        for layout in self._appearance_row_layouts:
            layout.setDirection(direction)
        self._apps.set_compact(direction == QBoxLayout.Direction.TopToBottom)
        if self._pideck_row is not None:
            self._pideck_row.set_compact(direction == QBoxLayout.Direction.TopToBottom)

    def _build_ui(self) -> None:
        """Create the settings rail and content pages."""
        root = WallpaperWidget(self)
        root.setObjectName("settings_root")
        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(32)
        self.layout().addWidget(root)

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(32, 24, 32, 24)
        root_layout.setSpacing(24)
        sidebar = self._build_sidebar(root)
        root_layout.addWidget(sidebar)

        content = QFrame(root)
        content.setObjectName("settings_content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(12)
        self._page_title = QLabel(content)
        self._page_title.setObjectName("settings_heading")
        self._page_description = QLabel(content)
        self._page_description.setObjectName("settings_description")
        self._page_description.setWordWrap(True)
        content_layout.addWidget(self._page_title)
        content_layout.addWidget(self._page_description)

        self._pages = QStackedWidget(content)
        self._pages.setObjectName("settings_pages")
        self._pages.addWidget(self._build_appearance_page())
        self._pages.addWidget(self._build_applications_page())
        if self._pideck_page is not None:
            self._pages.addWidget(self._build_pideck_update_page())
        if self._network_page is not None:
            self._pages.addWidget(self._build_network_page())
        if self._system_page is not None:
            self._pages.addWidget(self._build_system_page())
        if self._input_page is not None:
            self._pages.addWidget(self._build_input_page())
        content_layout.addWidget(self._pages, stretch=1)

        self._error_label = QLabel("", content)
        self._error_label.setObjectName("settings_error")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        content_layout.addWidget(self._error_label)
        root_layout.addWidget(content, stretch=1)

    def _build_sidebar(self, parent: QWidget) -> QFrame:
        """Create the vertical settings category rail."""
        sidebar = QFrame(parent)
        sidebar.setObjectName("settings_sidebar")
        sidebar.setFixedWidth(270)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        brand = QLabel("PiDeck", sidebar)
        brand.setObjectName("settings_brand")
        subtitle = QLabel(tr(self._language, "launcher_settings"), sidebar)
        subtitle.setObjectName("settings_sidebar_hint")
        subtitle.setWordWrap(True)
        layout.addWidget(brand)
        layout.addWidget(subtitle)
        layout.addSpacing(12)
        self._add_nav_button(layout, tr(self._language, "appearance"), 0)
        self._add_nav_button(layout, tr(self._language, "applications"), 1)
        if self._pideck_page is not None:
            self._add_nav_button(layout, tr(self._language, "pideck_updates"), self._pideck_page)
        if self._network_page is not None:
            self._add_nav_button(layout, tr(self._language, "network"), self._network_page)
        if self._system_page is not None:
            self._add_nav_button(layout, tr(self._language, "system"), self._system_page)
        if self._input_page is not None:
            self._add_nav_button(layout, tr(self._language, "input"), self._input_page)
        layout.addStretch()
        self._back_button = QPushButton(tr(self._language, "back"), sidebar)
        self._back_button.setObjectName("settings_back")
        self._back_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._back_button.clicked.connect(self.reject)
        self._back_button.installEventFilter(self)
        layout.addWidget(self._back_button)
        self._sidebar_subtitle = subtitle
        return sidebar

    def _add_nav_button(self, layout: QVBoxLayout, label: str, index: int) -> None:
        """Add one focusable category button to the rail."""
        button = QPushButton(label, self)
        button.setObjectName("settings_nav")
        button.setProperty("active", False)
        button.setProperty("nav_index", index)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.clicked.connect(lambda: self._select_page(index))
        button.installEventFilter(self)
        self._nav_buttons.append(button)
        layout.addWidget(button)

    def _scroll_page(self) -> tuple[QScrollArea, QWidget]:
        """Create a themed page that can reveal focused rows on small screens."""
        scroll = QScrollArea(self)
        scroll.setObjectName("settings_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        page = QWidget(scroll)
        scroll.setWidget(page)
        return scroll, page

    @staticmethod
    def _page_layout(page: QWidget) -> QVBoxLayout:
        """Use identical insets on every page; the right inset leaves room for the scrollbar."""
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 14, 0)
        layout.setSpacing(12)
        return layout

    def _build_appearance_page(self) -> QWidget:
        """Create theme and motion controls."""
        self._appearance_scroll, page = self._scroll_page()
        layout = self._page_layout(page)
        theme_row, self._theme_combo = self._control_row(
            page,
            "Theme",
            "Choose the visual style used throughout PiDeck.",
            SettingsComboBox(page),
        )
        self._theme_combo.setObjectName("settings_control")
        configure_combo_box(self._theme_combo)
        self._theme_row = theme_row
        self._theme_labels = theme_row.findChildren(QLabel)
        for theme in self._configuration.themes:
            self._theme_combo.addItem(theme.name, theme.identifier)
        current_index = self._theme_combo.findData(self._configuration.home.theme)
        if current_index >= 0:
            self._theme_combo.setCurrentIndex(current_index)
        motion_row, self._reduced_motion = self._control_row(
            page,
            "Reduced motion",
            "Use shorter focus animations for a calmer interface.",
            ToggleSwitch(page),
        )
        self._reduced_motion.setObjectName("settings_control")
        self._motion_row = motion_row
        self._motion_labels = motion_row.findChildren(QLabel)
        self._reduced_motion.setChecked(self._configuration.settings.reduced_motion)
        clock_row, self._show_clock = self._control_row(
            page,
            "Clock",
            "Show the current date and time on the home screen.",
            ToggleSwitch(page),
        )
        self._show_clock.setObjectName("settings_control")
        self._clock_row = clock_row
        self._clock_labels = clock_row.findChildren(QLabel)
        self._show_clock.setChecked(self._configuration.settings.show_clock)
        language_row, self._language_combo = self._control_row(
            page,
            "Language",
            "Choose the language used by PiDeck.",
            SettingsComboBox(page),
        )
        self._language_combo.setObjectName("settings_control")
        configure_combo_box(self._language_combo)
        self._language_row = language_row
        self._language_labels = language_row.findChildren(QLabel)
        self._language_combo.addItem("English", "en")
        self._language_combo.addItem("German", "de")
        language_index = self._language_combo.findData(self._language)
        if language_index >= 0:
            self._language_combo.setCurrentIndex(language_index)
        layout.addWidget(theme_row)
        layout.addWidget(motion_row)
        layout.addWidget(clock_row)
        layout.addWidget(language_row)
        layout.addStretch()
        self._page_controls[0] = [theme_row, motion_row, clock_row, language_row]
        return self._appearance_scroll

    def _build_applications_page(self) -> QWidget:
        """Create the single page that manages every application."""
        page = ApplicationsPage(
            self._configuration, self._catalog_statuses or (), self._theme, self._language,
            self._update_service, self,
        )
        page.install_requested.connect(self.application_install_requested.emit)
        page.remove_requested.connect(self.application_remove_requested.emit)
        page.visibility_requested.connect(self.application_visibility_requested.emit)
        page.order_requested.connect(self.application_order_requested.emit)
        page.navigation_requested.connect(self._handle_update_navigation)
        page.rows_changed.connect(self._refresh_application_controls)
        for row in page.rows():
            row.installEventFilter(self)
        self._apps = page
        self._page_controls[1] = list(page.rows())
        return page

    def _refresh_application_controls(self) -> None:
        """Track rebuilt application rows for keyboard and controller navigation."""
        if not hasattr(self, "_apps"):
            return
        self._page_controls[1] = list(self._apps.rows())
        for row in self._page_controls[1]:
            row.installEventFilter(self)

    def _build_pideck_update_page(self) -> QWidget:
        """Create the PiDeck release page independently of application updates."""
        assert self._pideck_info is not None
        assert self._pideck_page is not None
        self._pideck_scroll, page = self._scroll_page()
        layout = self._page_layout(page)
        self._pideck_row = PiDeckUpdateRow(self._pideck_info, page)
        self._pideck_row.installEventFilter(self)
        self._pideck_row.activation_requested.connect(self._activate_pideck_update)
        self._pideck_row.navigation_requested.connect(self._handle_update_navigation)
        layout.addWidget(self._pideck_row)
        self._build_date_label = QLabel(page)
        self._build_date_label.setObjectName("settings_hint")
        layout.addWidget(self._build_date_label)
        self._release_notes_title = QLabel(page)
        self._release_notes_title.setObjectName("settings_label")
        layout.addWidget(self._release_notes_title)
        self._release_notes = ReleaseNotesView(page)
        self._release_notes.installEventFilter(self)
        self._release_notes.navigation_requested.connect(self._handle_update_navigation)
        layout.addWidget(self._release_notes, stretch=1)
        self._pideck_check_button = QPushButton(page)
        self._pideck_check_button.setObjectName("settings_footer")
        self._pideck_check_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pideck_check_button.installEventFilter(self)
        self._pideck_check_button.clicked.connect(self._request_pideck_check)
        layout.addWidget(self._pideck_check_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self._page_controls[self._pideck_page] = [
            self._pideck_row, self._release_notes, self._pideck_check_button,
        ]
        self._set_pideck_check_enabled(self._pideck_info)
        self._show_release_notes(self._pideck_info)
        return self._pideck_scroll

    def _build_network_page(self) -> QWidget:
        """Create the entry point to appliance wireless setup."""
        page = QWidget(self)
        layout = self._page_layout(page)
        row = self._action_row(
            page, tr(self._language, "wifi_open"), tr(self._language, "network_description"),
        )
        self._network_row = row
        self._network_labels = row.findChildren(QLabel)
        layout.addWidget(row)
        layout.addStretch()
        assert self._network_page is not None
        self._page_controls[self._network_page] = [row]
        return page

    def _build_system_page(self) -> QWidget:
        """Create entry points for host system settings."""
        assert self._system_page is not None
        self._system_scroll, page = self._scroll_page()
        layout = self._page_layout(page)
        display_row = self._action_row(
            page, tr(self._language, "display"), tr(self._language, "display_description"),
        )
        self._display_row = display_row
        self._display_labels = display_row.findChildren(QLabel)
        layout.addWidget(display_row)
        row = self._action_row(
            page, tr(self._language, "audio"), tr(self._language, "audio_description"),
        )
        self._audio_row = row
        self._audio_labels = row.findChildren(QLabel)
        layout.addWidget(row)
        bluetooth_row = self._action_row(
            page, tr(self._language, "bluetooth"), tr(self._language, "bluetooth_description"),
        )
        self._bluetooth_row = bluetooth_row
        self._bluetooth_labels = bluetooth_row.findChildren(QLabel)
        layout.addWidget(bluetooth_row)
        drives_row = self._action_row(
            page, tr(self._language, "drives"), tr(self._language, "drives_description"),
        )
        self._drives_row = drives_row
        self._drives_labels = drives_row.findChildren(QLabel)
        layout.addWidget(drives_row)
        system_rows = [display_row, row, bluetooth_row, drives_row]
        if self._network_enabled:
            network_row = self._action_row(
                page, tr(self._language, "network"), tr(self._language, "network_description"),
            )
            self._network_row = network_row
            self._network_labels = network_row.findChildren(QLabel)
            layout.addWidget(network_row)
            system_rows.append(network_row)
        info_row = self._action_row(
            page, tr(self._language, "system_info"), tr(self._language, "system_info_description"),
        )
        self._system_info_row = info_row
        self._system_info_labels = info_row.findChildren(QLabel)
        layout.addWidget(info_row)
        system_rows.append(info_row)
        self._power_rows: dict[str, QFrame] = {}
        self._power_labels: dict[str, list[QLabel]] = {}
        for action in ("restart", "shutdown"):
            power_row = self._action_row(
                page, tr(self._language, action),
                tr(self._language, f"power_{action}_description"),
            )
            self._power_rows[action] = power_row
            self._power_labels[action] = power_row.findChildren(QLabel)
            layout.addWidget(power_row)
        layout.addStretch()
        self._page_controls[self._system_page] = [*system_rows, *self._power_rows.values()]
        return self._system_scroll

    def _build_input_page(self) -> QWidget:
        """Create immediately applied HDMI-CEC controls."""
        assert self._input_settings is not None
        assert self._input_page is not None
        self._input_scroll, page = self._scroll_page()
        layout = self._page_layout(page)
        self._input_rows: dict[str, SettingsRow] = {}
        self._input_labels: dict[str, list[QLabel]] = {}
        self._input_enabled = ToggleSwitch(page)
        self._input_auto = ToggleSwitch(page)
        self._input_switch = ToggleSwitch(page)
        self._input_port = SettingsComboBox(page)
        configure_combo_box(self._input_port)
        self._input_port.addItem("", None)
        for port in range(1, 16):
            self._input_port.addItem(f"HDMI {port}", port)
        self._input_enabled.setChecked(self._input_settings.enabled)
        self._input_auto.setChecked(self._input_settings.auto_connect)
        self._input_switch.setChecked(self._input_settings.switch_input)
        self._input_port.setCurrentIndex(self._input_port.findData(self._input_settings.hdmi_port))
        self._keyboard_combo: SettingsComboBox | None = None
        if self._keyboard_layout is not None:
            self._keyboard_combo = SettingsComboBox(page)
            configure_combo_box(self._keyboard_combo)
            for layout_option in KeyboardLayout:
                self._keyboard_combo.addItem(
                    tr(self._language, f"keyboard_layout_{layout_option.value}"), layout_option.value,
                )
            self._keyboard_combo.setCurrentIndex(self._keyboard_combo.findData(self._keyboard_layout.value))
            self._keyboard_combo.setObjectName("settings_control")
            row, _ = self._control_row(
                page, tr(self._language, "keyboard_layout"),
                tr(self._language, "keyboard_layout_hint"), self._keyboard_combo,
            )
            self._input_rows["keyboard_layout"] = row
            self._input_labels["keyboard_layout"] = row.findChildren(QLabel)
            layout.addWidget(row)
            self._keyboard_combo.currentIndexChanged.connect(self._emit_keyboard_layout)
        for key, control in (
            ("cec_enabled", self._input_enabled), ("cec_auto", self._input_auto),
            ("cec_port", self._input_port), ("cec_switch", self._input_switch),
        ):
            control.setObjectName("settings_control")
            row, _ = self._control_row(page, tr(self._language, key), tr(self._language, f"{key}_hint"), control)
            self._input_rows[key] = row
            self._input_labels[key] = row.findChildren(QLabel)
            layout.addWidget(row)
        for key in ("cec_connect", "cec_disconnect"):
            row = self._action_row(page, tr(self._language, key), tr(self._language, f"{key}_hint"))
            self._input_rows[key] = row
            self._input_labels[key] = row.findChildren(QLabel)
            layout.addWidget(row)
        self._input_status = QLabel(page)
        self._input_status.setObjectName("settings_hint")
        self._input_status.setWordWrap(True)
        layout.addWidget(self._input_status)
        layout.addStretch()
        self._page_controls[self._input_page] = list(self._input_rows.values())
        self._input_enabled.toggled.connect(self._emit_input_settings)
        self._input_auto.toggled.connect(self._emit_input_settings)
        self._input_switch.toggled.connect(self._emit_input_settings)
        self._input_port.currentIndexChanged.connect(self._emit_input_settings)
        return self._input_scroll

    def _emit_input_settings(self, *_: object) -> None:
        """Emit the current CEC preferences when a control changes."""
        self.input_settings_changed.emit(CecSettings(
            enabled=self._input_enabled.isChecked(),
            auto_connect=self._input_auto.isChecked(),
            hdmi_port=self._input_port.currentData(),
            switch_input=self._input_switch.isChecked(),
        ))

    def _emit_keyboard_layout(self, *_: object) -> None:
        """Emit the selected keyboard layout when the dropdown changes."""
        if self._keyboard_combo is None:
            return
        try:
            layout = KeyboardLayout(self._keyboard_combo.currentData())
        except ValueError:
            return
        self._keyboard_layout = layout
        self.keyboard_layout_changed.emit(layout)

    def set_input_settings(self, settings: CecSettings) -> None:
        """Restore controls after a rejected or externally applied change."""
        controls = (self._input_enabled, self._input_auto, self._input_switch, self._input_port)
        blockers = [QSignalBlocker(control) for control in controls]
        self._input_enabled.setChecked(settings.enabled)
        self._input_auto.setChecked(settings.auto_connect)
        self._input_switch.setChecked(settings.switch_input)
        self._input_port.setCurrentIndex(self._input_port.findData(settings.hdmi_port))
        del blockers

    def set_cec_status(self, status: str) -> None:
        """Show the current connection state or an actionable error."""
        self._cec_status = status
        keys = {
            "Connected": "cec_status_connected",
            "Connecting": "cec_status_connecting",
            "Disconnected": "cec_status_disconnected",
            "Enable HDMI-CEC before connecting": "cec_status_enable_first",
        }
        self._input_status.setText(tr(self._language, keys[status]) if status in keys else status)

    def _confirm_power(self, action: str) -> None:
        """Ask for explicit confirmation before emitting a host power request."""
        if self._power_dialog is not None:
            return
        dialog = PowerDialog(action, self._theme, self._language, self)
        self._power_dialog = dialog

        def finish(result: int) -> None:
            self._power_dialog = None
            if result == QDialog.DialogCode.Accepted:
                self.power_requested.emit(action)
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.open()

    def _control_row(
        self,
        parent: QWidget,
        title: str,
        description: str,
        control: _ControlT,
        *,
        action: bool = False,
    ) -> tuple[SettingsRow, _ControlT]:
        """Create a consistent labelled settings row."""
        row = ActionSettingsRow(parent) if action else SettingsRow(parent)
        row.setObjectName("settings_row")
        row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row.installEventFilter(self)
        row.navigation_requested.connect(self._handle_row_navigation)
        row.activation_requested.connect(
            lambda settings_row=row: self._handle_row_activation(settings_row)
        )
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, row)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(20)
        text = QVBoxLayout()
        title_label = QLabel(title, row)
        title_label.setObjectName("settings_label")
        description_label = QLabel(description, row)
        description_label.setObjectName("settings_hint")
        description_label.setWordWrap(True)
        if action:
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setAccessibleName(title)
            row.setAccessibleDescription(description)
            for label in (title_label, description_label, control):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text.addWidget(title_label)
        text.addWidget(description_label)
        layout.addLayout(text, stretch=1)
        if not action:
            control.setMinimumWidth(220)
            self._appearance_row_layouts.append(layout)
        control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(control)
        return row, control

    def _action_row(self, parent: QWidget, title: str, description: str) -> SettingsRow:
        """Create a single clickable row with a themed navigation indicator."""
        indicator = QLabel("›", parent)
        indicator.setObjectName("settings_chevron")
        indicator.setFixedWidth(32)
        indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row, _ = self._control_row(parent, title, description, indicator, action=True)
        return row

    def _select_page(self, index: int, focus_first: bool = True) -> None:
        """Select a category and update active styling and content."""
        if index != self._current_page:
            self._clear_error()
        self._current_page = index
        self._pages.setCurrentIndex(index)
        page_titles = {
            0: tr(self._language, "appearance"),
            1: tr(self._language, "applications"),
        }
        page_descriptions = {
            0: tr(self._language, "appearance_description"),
            1: tr(self._language, "applications_description"),
        }
        if self._pideck_page is not None:
            page_titles[self._pideck_page] = tr(self._language, "pideck_updates")
            page_descriptions[self._pideck_page] = tr(self._language, "pideck_updates_description")
        if self._network_page is not None:
            page_titles[self._network_page] = tr(self._language, "network")
            page_descriptions[self._network_page] = tr(self._language, "network_description")
        if self._system_page is not None:
            page_titles[self._system_page] = tr(self._language, "system")
            page_descriptions[self._system_page] = tr(self._language, "system_description")
        if self._input_page is not None:
            page_titles[self._input_page] = tr(self._language, "input")
            page_descriptions[self._input_page] = tr(self._language, "input_description")
        self._page_title.setText(page_titles[index])
        self._page_description.setText(
            page_descriptions[index]
        )
        self._set_active_nav(index)
        if focus_first:
            self._focus_first_page_control()

    def _set_active_nav(self, index: int | None) -> None:
        """Ensure exactly one category, or none, carries the active highlight."""
        for button_index, button in enumerate(self._nav_buttons):
            button.setProperty("active", index is not None and button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def _focus_nav(self, index: int) -> None:
        """Focus and visually select one category in the rail."""
        self._select_page(index, focus_first=False)
        self._nav_buttons[index].setFocus(Qt.FocusReason.OtherFocusReason)

    def _focus_first_page_control(self) -> None:
        """Focus the first usable control on the selected page."""
        controls = self._page_controls.get(self._current_page, [])
        if controls:
            controls[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def _focus_selected_nav(self) -> None:
        """Return focus to the active category button."""
        self._focus_nav(self._current_page)

    def _handle_row_navigation(self, direction: object) -> None:
        """Move between whole settings rows and the category rail."""
        rows = self._page_controls.get(self._current_page, [])
        focused_row = self.focusWidget()
        if focused_row not in rows:
            return
        row_index = rows.index(focused_row)
        if direction == Qt.Key.Key_Up:
            if row_index > 0:
                rows[row_index - 1].setFocus(Qt.FocusReason.OtherFocusReason)
        elif direction == Qt.Key.Key_Down:
            if row_index < len(rows) - 1:
                rows[row_index + 1].setFocus(Qt.FocusReason.OtherFocusReason)
        elif direction == Qt.Key.Key_Left:
            self._focus_selected_nav()

    def handle_input(self, event: InputEvent) -> None:
        """Handle normalized gamepad actions using the current focus target."""
        if not event.pressed:
            return
        modal = QApplication.activeModalWidget()
        if isinstance(modal, PasswordPrompt) and modal.parent() is self:
            keys = {
                InputAction.LEFT: Qt.Key.Key_Left, InputAction.RIGHT: Qt.Key.Key_Right,
                InputAction.UP: Qt.Key.Key_Up, InputAction.DOWN: Qt.Key.Key_Down,
                InputAction.ACTIVATE: Qt.Key.Key_Return, InputAction.BACK: Qt.Key.Key_Escape,
            }
            key = keys.get(event.action)
            if key is not None:
                focus = modal.focusWidget() or modal
                QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
            return
        if self._power_dialog is not None:
            self._power_dialog.handle_input(event)
            return
        if self._apps.dialog is not None:
            self._apps.dialog.handle_input(event)
            return
        if self._apps.is_sorting:
            sorting_keys = {
                InputAction.UP: Qt.Key.Key_Up, InputAction.DOWN: Qt.Key.Key_Down,
                InputAction.ACTIVATE: Qt.Key.Key_Return, InputAction.BACK: Qt.Key.Key_Escape,
            }
            sorting_key = sorting_keys.get(event.action)
            if sorting_key is not None:
                self._apps.handle_sorting_key(sorting_key)
            return
        dropdown = self._open_dropdown()
        if dropdown is not None:
            keys = {
                InputAction.UP: Qt.Key.Key_Up,
                InputAction.DOWN: Qt.Key.Key_Down,
                InputAction.ACTIVATE: Qt.Key.Key_Return,
                InputAction.BACK: Qt.Key.Key_Escape,
            }
            key = keys.get(event.action)
            if key is not None:
                QApplication.sendEvent(
                    dropdown.view(), QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
                )
            return
        current = self.focusWidget()
        qt_keys = {
            InputAction.LEFT: Qt.Key.Key_Left,
            InputAction.RIGHT: Qt.Key.Key_Right,
            InputAction.UP: Qt.Key.Key_Up,
            InputAction.DOWN: Qt.Key.Key_Down,
        }
        if event.action in qt_keys:
            key = qt_keys[event.action]
            if current in self._nav_buttons:
                nav_index = self._nav_buttons.index(current)
                if key == Qt.Key.Key_Left:
                    return
                if key == Qt.Key.Key_Down:
                    if nav_index == len(self._nav_buttons) - 1:
                        self._set_active_nav(None)
                        self._back_button.setFocus(Qt.FocusReason.OtherFocusReason)
                    else:
                        self._focus_nav(nav_index + 1)
                elif key == Qt.Key.Key_Up:
                    self._focus_nav(max(nav_index - 1, 0))
                elif key == Qt.Key.Key_Right:
                    self._select_page(nav_index)
                    self._focus_first_page_control()
                return
            if current is self._back_button:
                if key == Qt.Key.Key_Up:
                    self._focus_nav(len(self._nav_buttons) - 1)
                return
            if (
                self._pideck_page is not None and current is self._release_notes
                and self._release_notes.scroll(key)
            ):
                return
            if current in self._page_controls.get(self._current_page, []):
                if self._current_page == 1 or self._current_page == self._pideck_page:
                    self._handle_update_navigation(key)
                else:
                    self._handle_row_navigation(key)
                return
        if event.action is InputAction.ACTIVATE:
            if current in self._nav_buttons:
                self._select_page(self._nav_buttons.index(current))
                self._focus_first_page_control()
            elif current is self._back_button:
                self.reject()
            elif current in self._page_controls.get(0, []):
                self._handle_row_activation(current)
            elif current is self._pideck_row and self._pideck_row is not None:
                self._activate_pideck_update()
            elif self._pideck_page is not None and current is self._pideck_check_button:
                self._request_pideck_check()
            elif current in self._page_controls.get(1, []):
                self._apps.activate(current.application_id)
            elif self._network_enabled and current is self._network_row:
                self.network_requested.emit()
            elif self._system_page is not None and isinstance(current, SettingsRow) and current in self._page_controls[self._system_page]:
                self._handle_row_activation(current)
            elif self._input_page is not None and isinstance(current, SettingsRow) and current in self._page_controls[self._input_page]:
                self._handle_row_activation(current)
        elif event.action is InputAction.BACK:
            self.reject()

    def _open_dropdown(self) -> QComboBox | None:
        """Return the currently open Appearance combo box, if any."""
        combos = [self._theme_combo, self._language_combo]
        if self._input_page is not None:
            combos.append(self._input_port)
            if self._keyboard_combo is not None:
                combos.append(self._keyboard_combo)
        for combo in combos:
            if combo.view().isVisible():
                return combo
        return None

    def _handle_row_activation(self, row: SettingsRow) -> None:
        """Adjust a row or open its selector with Enter/Space."""
        if row is self._theme_row:
            self._theme_combo.showPopup()
        elif row is self._motion_row:
            self._reduced_motion.toggle()
        elif row is self._clock_row:
            self._show_clock.toggle()
        elif row is self._language_row:
            self._language_combo.showPopup()
        elif self._input_page is not None and row in self._input_rows.values():
            if row is self._input_rows["cec_enabled"]:
                self._input_enabled.toggle()
            elif row is self._input_rows["cec_auto"]:
                self._input_auto.toggle()
            elif row is self._input_rows["cec_switch"]:
                self._input_switch.toggle()
            elif row is self._input_rows["cec_port"]:
                self._input_port.showPopup()
            elif row is self._input_rows.get("keyboard_layout") and self._keyboard_combo is not None:
                self._keyboard_combo.showPopup()
            elif row is self._input_rows["cec_connect"]:
                self.cec_connection_requested.emit(True)
            elif row is self._input_rows["cec_disconnect"]:
                self.cec_connection_requested.emit(False)
        elif self._network_enabled and row is self._network_row:
            self.network_requested.emit()
        elif self._system_page is not None and row is self._audio_row:
            self.audio_requested.emit()
        elif self._system_page is not None and row is self._display_row:
            self.display_requested.emit()
        elif self._system_page is not None and row is self._bluetooth_row:
            self.bluetooth_requested.emit()
        elif self._system_page is not None and row is self._drives_row:
            self.drives_requested.emit()
        elif self._system_page is not None and row is self._system_info_row:
            self.system_info_requested.emit()
        elif self._system_page is not None:
            for action, power_row in self._power_rows.items():
                if row is power_row:
                    self._confirm_power(action)
                    break

    def _handle_language_changed(self, index: int) -> None:
        """Apply the selected language and emit the persisted settings update."""
        language = self._language_combo.itemData(index)
        if language in {"en", "de"}:
            self._language = language
            self.apply_language(language)
            self._emit_current_settings()

    def apply_language(self, language: str) -> None:
        """Translate all visible SettingsWindow strings."""
        self._language = language if language in {"en", "de"} else "en"
        self.setWindowTitle(tr(self._language, "settings"))
        self._nav_buttons[0].setText(tr(self._language, "appearance"))
        self._nav_buttons[1].setText(tr(self._language, "applications"))
        if self._pideck_page is not None:
            self._nav_buttons[self._pideck_page].setText(tr(self._language, "pideck_updates"))
        if self._network_page is not None:
            self._nav_buttons[self._network_page].setText(tr(self._language, "network"))
        if self._input_page is not None:
            self._nav_buttons[self._input_page].setText(tr(self._language, "input"))
            self._input_port.setItemText(0, tr(self._language, "cec_auto_port"))
            if self._keyboard_combo is not None:
                for index in range(self._keyboard_combo.count()):
                    option = self._keyboard_combo.itemData(index)
                    self._keyboard_combo.setItemText(index, tr(self._language, f"keyboard_layout_{option}"))
            for control in (self._input_enabled, self._input_auto, self._input_switch):
                control.set_state_texts(tr(self._language, "on"), tr(self._language, "off"))
            for key, labels in self._input_labels.items():
                labels[0].setText(tr(self._language, key))
                labels[1].setText(tr(self._language, f"{key}_hint"))
            if hasattr(self, "_cec_status"):
                self.set_cec_status(self._cec_status)
        if self._network_enabled:
            self._network_labels[0].setText(tr(self._language, "network" if self._system_page is not None else "wifi_open"))
            self._network_labels[1].setText(tr(self._language, "network_description"))
            self._network_row.setAccessibleName(self._network_labels[0].text())
            self._network_row.setAccessibleDescription(self._network_labels[1].text())
        if self._system_page is not None:
            self._nav_buttons[self._system_page].setText(tr(self._language, "system"))
            self._display_labels[0].setText(tr(self._language, "display"))
            self._display_labels[1].setText(tr(self._language, "display_description"))
            self._audio_labels[0].setText(tr(self._language, "audio"))
            self._audio_labels[1].setText(tr(self._language, "audio_description"))
            self._bluetooth_labels[0].setText(tr(self._language, "bluetooth"))
            self._bluetooth_labels[1].setText(tr(self._language, "bluetooth_description"))
            self._drives_labels[0].setText(tr(self._language, "drives"))
            self._drives_labels[1].setText(tr(self._language, "drives_description"))
            self._system_info_labels[0].setText(tr(self._language, "system_info"))
            self._system_info_labels[1].setText(tr(self._language, "system_info_description"))
            for row, labels in (
                (self._display_row, self._display_labels),
                (self._audio_row, self._audio_labels),
                (self._bluetooth_row, self._bluetooth_labels),
                (self._drives_row, self._drives_labels),
                (self._system_info_row, self._system_info_labels),
            ):
                row.setAccessibleName(labels[0].text())
                row.setAccessibleDescription(labels[1].text())
            for action in ("restart", "shutdown"):
                self._power_labels[action][0].setText(tr(self._language, action))
                self._power_labels[action][1].setText(tr(self._language, f"power_{action}_description"))
                self._power_rows[action].setAccessibleName(self._power_labels[action][0].text())
                self._power_rows[action].setAccessibleDescription(self._power_labels[action][1].text())
        self._back_button.setText(tr(self._language, "back"))
        for switch in (self._reduced_motion, self._show_clock):
            switch.set_state_texts(tr(self._language, "on"), tr(self._language, "off"))
        self._sidebar_subtitle.setText(tr(self._language, "launcher_settings"))
        setting_rows = (
            (self._theme_labels, "theme", "theme_hint"),
            (self._motion_labels, "reduced_motion", "reduced_motion_hint"),
            (self._clock_labels, "clock", "clock_hint"),
            (self._language_labels, "language", "language_hint"),
        )
        for labels, title_key, hint_key in setting_rows:
            labels[0].setText(tr(self._language, title_key))
            labels[1].setText(tr(self._language, hint_key))
        self._select_page(self._current_page, focus_first=False)
        self._apps.set_language(self._language)
        self._language_combo.setItemText(0, tr(self._language, "english"))
        self._language_combo.setItemText(1, tr(self._language, "german"))
        if self._pideck_row is not None:
            self._pideck_row.set_language(self._language)
            self._pideck_check_button.setText(tr(self._language, "check_for_updates"))
            if self._pideck_info is not None:
                self._show_release_notes(self._pideck_info)
            self._build_date_label.setText(tr(
                self._language, "build_date",
                date=format_timestamp(self._language, self._build_date) if self._build_date
                else tr(self._language, "unknown"),
            ))

    def set_pideck_update_info(self, info: PiDeckUpdateInfo) -> None:
        """Display the latest application-independent PiDeck update state."""
        self._pideck_info = info
        if self._pideck_row is not None:
            self._pideck_row.set_pideck_info(info)
            self._set_pideck_check_enabled(info)
            self._show_release_notes(info)

    def _show_release_notes(self, info: PiDeckUpdateInfo) -> None:
        """Show what's new in the available PiDeck release, if notes were published."""
        if self._release_notes.hasFocus() and not info.release_notes and self._pideck_row is not None:
            self._pideck_row.setFocus(Qt.FocusReason.OtherFocusReason)
        self._release_notes.set_notes(info.release_notes)
        self._release_notes_title.setVisible(bool(info.release_notes))
        self._release_notes_title.setText(tr(
            self._language, "release_notes_title", version=info.available_version or "",
        ))

    def _set_pideck_check_enabled(self, info: PiDeckUpdateInfo) -> None:
        """Enable manual checks only when a release can safely be rechecked."""
        enabled = info.status in (
            PiDeckUpdateStatus.UP_TO_DATE,
            PiDeckUpdateStatus.AVAILABLE,
            PiDeckUpdateStatus.FAILED,
        )
        if not enabled and self._pideck_check_button.hasFocus() and self._pideck_row is not None:
            self._pideck_row.setFocus(Qt.FocusReason.OtherFocusReason)
        self._pideck_check_button.setEnabled(enabled)

    def _request_pideck_check(self) -> None:
        """Request a fresh release check without starting an update."""
        if self._pideck_check_button.isEnabled():
            self.pideck_check_requested.emit()

    def show_power_error(self, message: str) -> None:
        """Display a failed host power request in the active settings view."""
        self.show_error(tr(self._language, "power_action_failed", message=message))

    def offer_update_restart(self) -> None:
        """Ask whether to reboot now that a PiDeck update is ready to apply."""
        version = ""
        if self._pideck_info is not None:
            version = self._pideck_info.available_version or ""
        dialog = ConfirmationDialog(
            tr(self._language, "update_restart_title"),
            tr(self._language, "update_restart_message", version=version),
            tr(self._language, "restart"),
            tr(self._language, "later"),
            self._theme,
            self,
        )
        self._power_dialog = dialog
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.power_requested.emit("restart")
        finally:
            self._power_dialog = None
            dialog.deleteLater()

    def _activate_pideck_update(self) -> None:
        """Request a PiDeck update, retry a failure, or reboot when pending."""
        if self._pideck_info is None:
            return
        if self._pideck_info.status in (
            PiDeckUpdateStatus.AVAILABLE,
            PiDeckUpdateStatus.FAILED,
        ):
            self.pideck_update_requested.emit()
        elif self._pideck_info.status is PiDeckUpdateStatus.PENDING:
            self.offer_update_restart()

    def _handle_update_navigation(self, key: int) -> None:
        """Move through update rows; Left returns to the active settings category."""
        rows = [
            row for row in self._page_controls.get(self._current_page, [])
            if row.isEnabled() and not row.isHidden()
        ]
        current = self.focusWidget()
        if current not in rows:
            return
        index = rows.index(current)
        if key == Qt.Key.Key_Up:
            if index > 0:
                rows[index - 1].setFocus(Qt.FocusReason.OtherFocusReason)
        elif key == Qt.Key.Key_Down and index < len(rows) - 1:
            rows[index + 1].setFocus(Qt.FocusReason.OtherFocusReason)
        elif key == Qt.Key.Key_Left:
            self._focus_selected_nav()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """Implement deterministic D-pad navigation across the settings surface."""
        if (
            event.type() == QEvent.Type.FocusIn
            and self._system_page is not None
            and isinstance(watched, QWidget)
            and watched in self._page_controls.get(self._system_page, [])
        ):
            self._system_scroll.ensureWidgetVisible(watched)
        if (
            event.type() == QEvent.Type.FocusIn
            and self._input_page is not None
            and isinstance(watched, QWidget)
            and watched in self._page_controls.get(self._input_page, [])
        ):
            self._input_scroll.ensureWidgetVisible(watched)
        if (
            event.type() == QEvent.Type.FocusIn
            and isinstance(watched, QWidget)
            and watched in self._page_controls.get(0, [])
        ):
            self._appearance_scroll.ensureWidgetVisible(watched)
        if (
            event.type() == QEvent.Type.FocusIn
            and self._pideck_page is not None
            and isinstance(watched, QWidget)
            and watched in self._page_controls.get(self._pideck_page, [])
        ):
            self._pideck_scroll.ensureWidgetVisible(watched)
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        key_event = event
        if not isinstance(key_event, QKeyEvent):
            return super().eventFilter(watched, event)
        key = key_event.key()
        apps = getattr(self, "_apps", None)
        if apps is not None and apps.is_sorting and watched in self._page_controls.get(1, []):
            return apps.handle_sorting_key(key)
        if key == Qt.Key.Key_Escape:
            self.reject()
            return True
        if self._pideck_page is not None and watched is self._pideck_check_button:
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Down):
                self._handle_update_navigation(key)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._request_pideck_check()
                return True
        if watched in self._nav_buttons:
            nav_index = self._nav_buttons.index(watched)
            if key == Qt.Key.Key_Left:
                return True
            if key == Qt.Key.Key_Down:
                if nav_index == len(self._nav_buttons) - 1:
                    self._set_active_nav(None)
                    self._back_button.setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
                self._focus_nav(min(nav_index + 1, len(self._nav_buttons) - 1))
                return True
            if key == Qt.Key.Key_Up:
                self._focus_nav(max(nav_index - 1, 0))
                return True
            if key == Qt.Key.Key_Right:
                self._select_page(nav_index)
                self._focus_first_page_control()
                return True
        if watched is self._back_button:
            self._set_active_nav(None)
            if key == Qt.Key.Key_Up:
                self._focus_nav(len(self._nav_buttons) - 1)
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
                # Qt would otherwise move focus to an unstyled widget outside the D-pad navigation.
                return True
            return super().eventFilter(watched, event)
        controls = self._page_controls.get(self._current_page, [])
        if watched in controls:
            return super().eventFilter(watched, event)
        return super().eventFilter(watched, event)

    def _submit(self) -> None:
        """Emit the current settings for compatibility with direct callers."""
        update = self._build_update()
        if update is not None:
            self.settings_submitted.emit(update)

    def _emit_current_settings(self, *_: object) -> None:
        """Emit a valid settings update immediately after a control changes."""
        update = self._build_update()
        if update is not None:
            self.settings_changed.emit(update)

    def _build_update(self) -> SettingsUpdate | None:
        """Build the current typed update or show an inline validation error."""
        selected_applications = self._configuration.home.visible_applications or tuple(
            application.identifier for application in self._configuration.applications
        )
        self._clear_error()
        return SettingsUpdate(
            theme=self._theme_combo.currentData(),
            reduced_motion=self._reduced_motion.isChecked(),
            visible_applications=selected_applications,
            show_clock=self._show_clock.isChecked(),
            language=self._language,
        )

    def select_applications_page(self) -> None:
        """Show the Applications page and focus its first row."""
        self._select_page(1)

    def set_configuration(
        self, configuration: Configuration, statuses: tuple[CatalogItemStatus, ...] | None = None,
    ) -> None:
        """Refresh application rows after applications or their order changed."""
        self._configuration = configuration
        if statuses is not None:
            self._catalog_statuses = statuses
        self._apps.set_model(configuration, self._catalog_statuses or ())

    def set_catalog_statuses(self, statuses: tuple[CatalogItemStatus, ...]) -> None:
        """Refresh the install state of every supported application."""
        self.set_configuration(self._configuration, statuses)

    def set_application_busy(self, application_id: str | None, *, removing: bool = False) -> None:
        """Show or clear progress for an application being installed or removed."""
        self._apps.set_busy(application_id, removing=removing)

    def set_application_progress(self, application_id: str, progress: InstallProgress) -> None:
        """Render the latest progress of the running install or removal."""
        self._apps.set_progress(application_id, progress)

    @property
    def applications_page(self) -> ApplicationsPage:
        """Return the page that manages applications."""
        return self._apps

    def apply_theme_definition(self, theme: ThemeDefinition) -> None:
        """Apply a persisted theme change while the settings view remains open."""
        self._theme = theme
        apply_theme(self, theme)

    def show_fullscreen(self) -> None:
        """Open the settings surface fullscreen and focus its category rail."""
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._nav_buttons[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def show_error(self, message: str) -> None:
        """Display a recoverable error inside the settings content area."""
        self._error_label.setText(message)
        self._error_label.setVisible(bool(message))

    def _clear_error(self) -> None:
        """Remove a previously shown error and its reserved space."""
        self._error_label.clear()
        self._error_label.hide()
