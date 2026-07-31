"""Console-style settings surface for Milestone 5."""

from pathlib import Path

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pideck.application.settings import SettingsUpdate
from pideck.domain.configuration import Configuration
from pideck.domain.theme import ThemeDefinition

from .theme import apply_theme, icon_for_path


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


class SettingsWindow(QDialog):
    """Edit launcher settings in a fullscreen, controller-friendly menu."""

    settings_submitted = Signal(object)
    settings_changed = Signal(object)

    def __init__(
        self,
        configuration: Configuration,
        theme: ThemeDefinition,
        asset_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        """Build the settings menu from the current validated configuration."""
        super().__init__(parent)
        self._configuration = configuration
        self._theme = theme
        self._asset_root = asset_root
        self._nav_buttons: list[QPushButton] = []
        self._page_controls: dict[int, list[QWidget]] = {}
        self._current_page = 0
        self.setWindowTitle("PiDeck Settings")
        self.setObjectName("settings_window")
        self.setModal(True)
        self._build_ui()
        apply_theme(self, theme)
        self._theme_combo.currentIndexChanged.connect(self._emit_current_settings)
        self._reduced_motion.toggled.connect(self._emit_current_settings)
        self._applications.itemChanged.connect(self._handle_application_item_changed)
        self._select_page(0, focus_first=False)
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
        self._reduced_motion.setText("On")
        self._reduced_motion.setChecked(self._configuration.settings.reduced_motion)
        layout.addWidget(theme_row)
        layout.addWidget(motion_row)
        layout.addStretch()
        self._page_controls[0] = [theme_row, motion_row]
        return page

    def _build_home_page(self) -> QWidget:
        """Create the home-screen application visibility list."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        hint = QLabel("Select the applications shown on the home screen.", page)
        hint.setObjectName("settings_description")
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
        layout.addWidget(hint)
        layout.addWidget(self._applications, stretch=1)
        self._page_controls[1] = [self._applications]
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
        self._page_title.setText("Appearance" if index == 0 else "Home screen")
        self._page_description.setText(
            "Personalize the look and motion of your launcher."
            if index == 0
            else "Choose which applications are available from the home screen."
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

    def _handle_row_activation(self, row: SettingsRow) -> None:
        """Adjust a row or open its selector with Enter/Space."""
        if row is self._theme_row:
            self._theme_combo.showPopup()
        elif row is self._motion_row:
            self._reduced_motion.toggle()

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
        state: Qt.CheckState,
    ) -> None:
        """Keep one application visible and emit valid list changes immediately."""
        if state is Qt.CheckState.Unchecked and not any(
            self._applications.item(index).checkState() is Qt.CheckState.Checked
            for index in range(self._applications.count())
        ):
            with QSignalBlocker(self._applications):
                item.setCheckState(Qt.CheckState.Checked)
            self.show_error("At least one application must remain visible.")
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
            self.show_error("Select at least one application for the home screen.")
            return None
        self._error_label.clear()
        return SettingsUpdate(
            theme=self._theme_combo.currentData(),
            reduced_motion=self._reduced_motion.isChecked(),
            visible_applications=selected_applications,
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
