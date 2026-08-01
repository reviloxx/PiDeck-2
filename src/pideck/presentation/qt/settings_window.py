"""Console-style settings surface for Milestone 5."""

from pathlib import Path

from PySide6.QtCore import QEvent, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pideck.application.settings import SettingsUpdate
from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.updates import ApplicationUpdateService
from pideck.application.ports.updates import UpdateInfo, UpdateStatus
from pideck.domain.configuration import Configuration
from pideck.domain.theme import ThemeDefinition

from .theme import apply_theme, icon_for_path
from .localization import tr


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

    def mousePressEvent(self, event: object) -> None:
        """Focus the complete row when it is clicked."""
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)


class UpdateRow(QFrame):
    """Focusable update entry with status, action, and loading spinner."""

    activation_requested = Signal()
    navigation_requested = Signal(object)

    def __init__(
        self,
        application_id: str,
        application_name: str,
        parent: QWidget | None = None,
    ) -> None:
        """Create one update entry for a configured application."""
        super().__init__(parent)
        self.application_id = application_id
        self.application_name = application_name
        self.setObjectName("update_row")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        name = QLabel(application_name, self)
        name.setObjectName("settings_label")
        self.status_label = QLabel("Checking...", self)
        self.status_label.setObjectName("update_status")
        self.spinner = QLabel("", self)
        self.spinner.setObjectName("update_spinner")
        self.spinner.setFixedWidth(24)
        self.action = QPushButton("", self)
        self.action.setObjectName("update_action")
        self.action.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.action.clicked.connect(self.activation_requested)
        layout.addWidget(name, stretch=1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.spinner)
        layout.addWidget(self.action)
        self._spinner_timer = QTimer(self)
        self._spinner_timer.timeout.connect(self._advance_spinner)
        self._spinner_frames = ("|", "/", "-", "\\")
        self._spinner_index = 0
        self.info = UpdateInfo(application_id, application_name, UpdateStatus.CHECKING)
        self.last_available_info: UpdateInfo | None = None
        self._language = "en"

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Activate or cancel this update with Enter or Space."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activation_requested.emit()
            event.accept()
            return
        if event.key() in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            self.navigation_requested.emit(event.key())
            event.accept()
            return
        super().keyPressEvent(event)

    def set_info(self, info: UpdateInfo) -> None:
        """Render one update status without changing row geometry."""
        self.info = info
        if info.status is UpdateStatus.AVAILABLE:
            self.last_available_info = info
        labels = {
            UpdateStatus.CHECKING: tr(self._language, "checking"),
            UpdateStatus.UP_TO_DATE: tr(self._language, "up_to_date"),
            UpdateStatus.AVAILABLE: tr(self._language, "update_available"),
            UpdateStatus.UPDATING: tr(self._language, "updating"),
            UpdateStatus.UPDATED: tr(self._language, "updated"),
            UpdateStatus.PASSWORD_REQUIRED: tr(self._language, "authentication_required"),
            UpdateStatus.UNSUPPORTED: tr(self._language, "not_supported"),
            UpdateStatus.FAILED: info.message or tr(self._language, "update_failed"),
            UpdateStatus.CANCELLED: tr(self._language, "cancelled"),
        }
        self.status_label.setText(labels[info.status])
        updating = info.status is UpdateStatus.UPDATING
        self.spinner.setVisible(updating)
        self.action.setText(
            tr(self._language, "cancel")
            if updating
            else tr(self._language, "update")
            if info.status is UpdateStatus.AVAILABLE
            else ""
        )
        actionable = info.status in (UpdateStatus.AVAILABLE, UpdateStatus.UPDATING)
        self.action.setEnabled(actionable)
        self.action.setVisible(actionable)
        if updating:
            self._spinner_timer.start(120)
        else:
            self._spinner_timer.stop()
            self.spinner.clear()

    def set_language(self, language: str) -> None:
        """Re-render the current update status in the selected language."""
        self._language = language if language in {"en", "de"} else "en"
        self.set_info(self.info)

    def _advance_spinner(self) -> None:
        """Advance the update row spinner."""
        self.spinner.setText(self._spinner_frames[self._spinner_index])
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)


class PasswordPrompt(QDialog):
    """Themed, cancellable sudo password prompt for package updates."""

    def __init__(
        self,
        application_name: str,
        theme: ThemeDefinition,
        parent: QWidget,
        language: str = "en",
    ) -> None:
        """Create a modal password prompt without persisting the secret."""
        super().__init__(parent)
        self._language = language
        self.setWindowTitle(tr(language, "authentication_required"))
        self.setObjectName("password_prompt")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(16)
        title = QLabel(tr(language, "system_update"), self)
        title.setObjectName("settings_heading")
        message = QLabel(tr(language, "password_message", name=application_name), self)
        message.setObjectName("settings_description")
        message.setWordWrap(True)
        self.password_input = QLineEdit(self)
        self.password_input.setObjectName("password_input")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText(tr(language, "password_placeholder"))
        self.password_input.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.password_input.installEventFilter(self)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton(tr(language, "cancel"), self)
        self.cancel_button.setObjectName("settings_back")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cancel_button.clicked.connect(self.reject)
        self.continue_button = QPushButton(tr(language, "continue"), self)
        self.continue_button.setObjectName("settings_footer")
        self.continue_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.continue_button.clicked.connect(self.accept)
        self.cancel_button.installEventFilter(self)
        self.continue_button.installEventFilter(self)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.continue_button)
        layout.addWidget(title)
        layout.addWidget(message)
        layout.addWidget(self.password_input)
        layout.addLayout(buttons)
        apply_theme(self, theme)
        self.password_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """Provide predictable D-pad navigation for password entry."""
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return True
        if watched is self.password_input:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.accept()
                return True
            if key == Qt.Key.Key_Down:
                self.continue_button.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        elif watched is self.cancel_button:
            if key == Qt.Key.Key_Right:
                self.continue_button.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            if key == Qt.Key.Key_Up:
                self.password_input.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        elif watched is self.continue_button:
            if key == Qt.Key.Key_Left:
                self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            if key == Qt.Key.Key_Up:
                self.password_input.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)

    def password(self) -> str:
        """Return the entered password for immediate one-shot use."""
        return self.password_input.text()


class SettingsWindow(QDialog):
    """Edit launcher settings in a fullscreen, controller-friendly menu."""

    settings_submitted = Signal(object)
    settings_changed = Signal(object)
    update_status = Signal(object)

    def __init__(
        self,
        configuration: Configuration,
        theme: ThemeDefinition,
        asset_root: Path,
        parent: QWidget | None = None,
        update_service: ApplicationUpdateService | None = None,
    ) -> None:
        """Build the settings menu from the current validated configuration."""
        super().__init__(parent)
        self._configuration = configuration
        self._theme = theme
        self._language = configuration.settings.language
        self._asset_root = asset_root
        self._update_service = update_service
        self._update_rows: dict[str, UpdateRow] = {}
        self._rechecking_after_cancel: set[str] = set()
        self._update_applications = {
            application.identifier: application for application in configuration.applications
        }
        self._nav_buttons: list[QPushButton] = []
        self._page_controls: dict[int, list[QWidget]] = {}
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
        self._applications.itemChanged.connect(self._handle_application_item_changed)
        self.update_status.connect(self._handle_update_status)
        if self._update_service is not None:
            self._update_service.check_async(
                tuple(self._configuration.applications),
                lambda info: self.update_status.emit(info),
            )
        else:
            for application_id in self._update_rows:
                self._update_rows[application_id].set_info(
                    UpdateInfo(
                        application_id,
                        self._update_applications[application_id].name,
                        UpdateStatus.UNSUPPORTED,
                        message="Update service unavailable",
                    )
                )
        self._select_page(0, focus_first=False)
        self.apply_language(self._language)
        self._nav_buttons[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def _build_ui(self) -> None:
        """Create the settings rail and content pages."""
        root = QWidget(self)
        root.setObjectName("settings_root")
        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(56, 44, 56, 44)
        self.layout().setSpacing(32)
        self.layout().addWidget(root)

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(32)
        sidebar = self._build_sidebar(root)
        root_layout.addWidget(sidebar)

        content = QFrame(root)
        content.setObjectName("settings_content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(40, 34, 40, 28)
        content_layout.setSpacing(18)
        self._page_kicker = QLabel("PIDECK / SETTINGS", content)
        self._page_kicker.setObjectName("settings_kicker")
        self._page_title = QLabel(content)
        self._page_title.setObjectName("settings_heading")
        self._page_description = QLabel(content)
        self._page_description.setObjectName("settings_description")
        self._page_description.setWordWrap(True)
        content_layout.addWidget(self._page_kicker)
        content_layout.addWidget(self._page_title)
        content_layout.addWidget(self._page_description)

        self._pages = QStackedWidget(content)
        self._pages.setObjectName("settings_pages")
        self._pages.addWidget(self._build_appearance_page())
        self._pages.addWidget(self._build_home_page())
        self._pages.addWidget(self._build_updates_page())
        content_layout.addWidget(self._pages, stretch=1)

        self._error_label = QLabel("", content)
        self._error_label.setObjectName("settings_error")
        self._error_label.setWordWrap(True)
        content_layout.addWidget(self._error_label)
        root_layout.addWidget(content, stretch=1)

    def _build_sidebar(self, parent: QWidget) -> QFrame:
        """Create the vertical settings category rail."""
        sidebar = QFrame(parent)
        sidebar.setObjectName("settings_sidebar")
        sidebar.setFixedWidth(270)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(8)
        brand = QLabel("PiDeck", sidebar)
        brand.setObjectName("settings_brand")
        subtitle = QLabel("Launcher settings", sidebar)
        subtitle.setObjectName("settings_sidebar_hint")
        layout.addWidget(brand)
        layout.addWidget(subtitle)
        layout.addSpacing(28)
        self._add_nav_button(layout, "Appearance", 0)
        self._add_nav_button(layout, "Home screen", 1)
        self._add_nav_button(layout, "Updates", 2)
        layout.addStretch()
        self._back_button = QPushButton("Back", sidebar)
        self._back_button.setObjectName("settings_back")
        self._back_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._back_button.clicked.connect(self.reject)
        self._back_button.installEventFilter(self)
        layout.addWidget(self._back_button)
        hint = QLabel("Use the arrow keys or controller to navigate", sidebar)
        hint.setObjectName("settings_sidebar_hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._sidebar_subtitle = subtitle
        self._sidebar_hint = hint
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

    def _build_appearance_page(self) -> QWidget:
        """Create theme and motion controls."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        theme_row, self._theme_combo = self._control_row(
            page,
            "Theme",
            "Choose the visual style used throughout PiDeck.",
            QComboBox(page),
        )
        self._theme_combo.setObjectName("settings_control")
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
            QCheckBox(page),
        )
        self._reduced_motion.setObjectName("settings_control")
        self._motion_row = motion_row
        self._motion_labels = motion_row.findChildren(QLabel)
        self._reduced_motion.setText("On")
        self._reduced_motion.setChecked(self._configuration.settings.reduced_motion)
        clock_row, self._show_clock = self._control_row(
            page,
            "Clock",
            "Show the current date and time on the home screen.",
            QCheckBox(page),
        )
        self._show_clock.setObjectName("settings_control")
        self._clock_row = clock_row
        self._clock_labels = clock_row.findChildren(QLabel)
        self._show_clock.setText("On")
        self._show_clock.setChecked(self._configuration.settings.show_clock)
        language_row, self._language_combo = self._control_row(
            page,
            "Language",
            "Choose the language used by PiDeck.",
            QComboBox(page),
        )
        self._language_combo.setObjectName("settings_control")
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
        return page

    def _build_home_page(self) -> QWidget:
        """Create the home-screen application visibility list."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        self._applications = QListWidget(page)
        self._applications.setObjectName("settings_applications")
        self._applications.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._applications.installEventFilter(self)
        visible_ids = set(self._configuration.home.visible_applications)
        all_visible = not visible_ids
        for application in self._configuration.applications:
            item = QListWidgetItem(application.name, self._applications)
            item.setData(Qt.ItemDataRole.UserRole, application.identifier)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if all_visible or application.identifier in visible_ids
                else Qt.CheckState.Unchecked
            )
        layout.addWidget(self._applications, stretch=1)
        self._page_controls[1] = [self._applications]
        return page

    def _build_updates_page(self) -> QWidget:
        """Create one update entry for every configured application."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(10)
        controls: list[QWidget] = []
        for application in self._configuration.applications:
            row = UpdateRow(application.identifier, application.name, page)
            row.activation_requested.connect(
                lambda application_id=application.identifier: self._activate_update(application_id)
            )
            row.navigation_requested.connect(self._handle_update_navigation)
            self._update_rows[application.identifier] = row
            controls.append(row)
            layout.addWidget(row)
        layout.addStretch()
        self._page_controls[2] = controls
        return page

    def _control_row(
        self,
        parent: QWidget,
        title: str,
        description: str,
        control: QWidget,
    ) -> tuple[QFrame, QWidget]:
        """Create a consistent labelled settings row."""
        row = SettingsRow(parent)
        row.setObjectName("settings_row")
        row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row.navigation_requested.connect(self._handle_row_navigation)
        row.activation_requested.connect(
            lambda settings_row=row: self._handle_row_activation(settings_row)
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(20)
        text = QVBoxLayout()
        title_label = QLabel(title, row)
        title_label.setObjectName("settings_label")
        description_label = QLabel(description, row)
        description_label.setObjectName("settings_hint")
        description_label.setWordWrap(True)
        text.addWidget(title_label)
        text.addWidget(description_label)
        layout.addLayout(text, stretch=1)
        control.setMinimumWidth(220)
        control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(control)
        return row, control

    def _select_page(self, index: int, focus_first: bool = True) -> None:
        """Select a category and update active styling and content."""
        if index != self._current_page:
            self._clear_home_selection()
        self._current_page = index
        self._pages.setCurrentIndex(index)
        page_titles = {
            0: tr(self._language, "appearance"),
            1: tr(self._language, "home_screen"),
            2: tr(self._language, "updates"),
        }
        page_descriptions = {
            0: tr(self._language, "appearance_description"),
            1: tr(self._language, "home_description"),
            2: tr(self._language, "updates_description"),
        }
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
            if self._current_page == 1:
                self._applications.setCurrentRow(0)
            controls[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def _clear_home_selection(self) -> None:
        """Remove Home screen list selection while keeping its check states."""
        if hasattr(self, "_applications"):
            self._applications.clearSelection()
            self._applications.setCurrentRow(-1)

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
            if row_index == 0:
                self._focus_selected_nav()
            else:
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
            if current is self._applications:
                row = self._applications.currentRow()
                if key == Qt.Key.Key_Left or (key == Qt.Key.Key_Up and row == 0):
                    self._clear_home_selection()
                    self._focus_selected_nav()
                elif key == Qt.Key.Key_Up:
                    self._applications.setCurrentRow(max(row - 1, 0))
                elif key == Qt.Key.Key_Down:
                    self._applications.setCurrentRow(min(row + 1, self._applications.count() - 1))
                return
            if current in self._page_controls.get(self._current_page, []):
                if self._current_page == 2:
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
            elif current is self._applications:
                item = self._applications.item(self._applications.currentRow())
                if item is not None:
                    item.setCheckState(
                        Qt.CheckState.Unchecked
                        if item.checkState() is Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
            elif current in self._page_controls.get(0, []):
                self._handle_row_activation(current)
            elif current in self._page_controls.get(2, []):
                self._activate_update(current.application_id)
        elif event.action is InputAction.BACK:
            self.reject()

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
        self._nav_buttons[1].setText(tr(self._language, "home_screen"))
        self._nav_buttons[2].setText(tr(self._language, "updates"))
        self._back_button.setText(tr(self._language, "back"))
        self._sidebar_subtitle.setText(tr(self._language, "launcher_settings"))
        self._sidebar_hint.setText(tr(self._language, "navigation_hint"))
        self._page_kicker.setText(tr(self._language, "appearance_kicker"))
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
        self._language_combo.setItemText(0, tr(self._language, "english"))
        self._language_combo.setItemText(1, tr(self._language, "german"))
        for row in self._update_rows.values():
            row.set_language(self._language)

    def _activate_update(self, application_id: str) -> None:
        """Start or cancel the selected application update."""
        if self._update_service is None:
            return
        application = self._update_applications[application_id]
        row = self._update_rows[application_id]
        if row.info.status is UpdateStatus.UPDATING:
            self._rechecking_after_cancel.add(application_id)
            self._update_service.cancel(application_id)
            row.set_info(UpdateInfo(application_id, application.name, UpdateStatus.CHECKING))
            self._update_service.check_async(
                (application,),
                lambda info: self.update_status.emit(info),
            )
            return
        self._update_service.start_async(
            application,
            None,
            lambda info: self.update_status.emit(info),
        )

    def _handle_update_status(self, info: UpdateInfo) -> None:
        """Render update status and request a password only when required."""
        row = self._update_rows.get(info.application_id)
        if row is None:
            return
        if info.status is UpdateStatus.CANCELLED and info.application_id in self._rechecking_after_cancel:
            return
        if info.application_id in self._rechecking_after_cancel and info.status is not UpdateStatus.CANCELLED:
            self._rechecking_after_cancel.discard(info.application_id)
        row.set_info(info)
        if info.status is not UpdateStatus.PASSWORD_REQUIRED:
            return
        prompt = PasswordPrompt(info.application_name, self._theme, self, self._language)
        if prompt.exec() != QDialog.DialogCode.Accepted:
            row.set_info(
                row.last_available_info
                or UpdateInfo(info.application_id, info.application_name, UpdateStatus.AVAILABLE)
            )
            return
        password = prompt.password()
        prompt.deleteLater()
        if self._update_service is not None:
            self._update_service.start_async(
                self._update_applications[info.application_id],
                password,
                lambda result: self.update_status.emit(result),
            )

    def _handle_update_navigation(self, key: int) -> None:
        """Move through update rows or return to the active settings category."""
        rows = self._page_controls.get(2, [])
        current = self.focusWidget()
        if current not in rows:
            return
        index = rows.index(current)
        if key == Qt.Key.Key_Up:
            if index == 0:
                self._focus_selected_nav()
            else:
                rows[index - 1].setFocus(Qt.FocusReason.OtherFocusReason)
        elif key == Qt.Key.Key_Down and index < len(rows) - 1:
            rows[index + 1].setFocus(Qt.FocusReason.OtherFocusReason)
        elif key == Qt.Key.Key_Left:
            self._focus_selected_nav()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """Implement deterministic D-pad navigation across the settings surface."""
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        key_event = event
        if not isinstance(key_event, QKeyEvent):
            return super().eventFilter(watched, event)
        key = key_event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
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
            return super().eventFilter(watched, event)
        if watched is self._applications:
            current_row = self._applications.currentRow()
            if key == Qt.Key.Key_Left:
                self._clear_home_selection()
                self._focus_selected_nav()
                return True
            if key == Qt.Key.Key_Up and current_row == 0:
                self._clear_home_selection()
                self._focus_selected_nav()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                item = self._applications.item(current_row)
                if item is not None:
                    item.setCheckState(
                        Qt.CheckState.Unchecked
                        if item.checkState() is Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
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

    def _handle_application_item_changed(
        self,
        item: QListWidgetItem,
        state: Qt.CheckState | None = None,
    ) -> None:
        """Keep one application visible and emit valid list changes immediately."""
        state = item.checkState() if state is None else state
        if state is Qt.CheckState.Unchecked and not any(
            self._applications.item(index).checkState() is Qt.CheckState.Checked
            for index in range(self._applications.count())
        ):
            with QSignalBlocker(self._applications):
                item.setCheckState(Qt.CheckState.Checked)
            self.show_error(tr(self._language, "at_least_one"))
            return
        self._error_label.clear()
        self._emit_current_settings()

    def _build_update(self) -> SettingsUpdate | None:
        """Build the current typed update or show an inline validation error."""
        selected_applications = tuple(
            self._applications.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self._applications.count())
            if self._applications.item(index).checkState() is Qt.CheckState.Checked
        )
        if not selected_applications:
            self.show_error(tr(self._language, "at_least_one"))
            return None
        self._error_label.clear()
        return SettingsUpdate(
            theme=self._theme_combo.currentData(),
            reduced_motion=self._reduced_motion.isChecked(),
            visible_applications=selected_applications,
            show_clock=self._show_clock.isChecked(),
            language=self._language,
        )

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
