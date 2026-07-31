"""Console-style settings surface for Milestone 5."""

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
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
    adjustment_requested = Signal(object)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Route directional keys without moving focus into child controls."""
        direction_by_key = {
            Qt.Key.Key_Left: Qt.Key.Key_Left,
            Qt.Key.Key_Right: Qt.Key.Key_Right,
            Qt.Key.Key_Up: Qt.Key.Key_Up,
            Qt.Key.Key_Down: Qt.Key.Key_Down,
        }
        key = direction_by_key.get(event.key())
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.adjustment_requested.emit(key)
            event.accept()
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
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
        self._select_page(0, focus_first=False)
        self._nav_buttons[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def _build_ui(self) -> None:
        """Create the settings rail, content pages, and persistent footer."""
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
        footer = self._build_footer(content)
        content_layout.addLayout(footer)
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
        row.adjustment_requested.connect(
            lambda key, settings_row=row: self._handle_row_adjustment(settings_row, key)
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

    def _build_footer(self, parent: QWidget) -> QHBoxLayout:
        """Create consistently styled Cancel and Save actions."""
        layout = QHBoxLayout()
        layout.setSpacing(12)
        layout.addStretch()
        self._cancel_button = QPushButton("Cancel", parent)
        self._cancel_button.setObjectName("settings_footer")
        self._cancel_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._cancel_button.clicked.connect(self.reject)
        self._save_button = QPushButton("Save", parent)
        self._save_button.setObjectName("settings_footer")
        self._save_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._save_button.setIcon(icon_for_path(self._theme.icons.get("settings")))
        self._save_button.clicked.connect(self._submit)
        self._cancel_button.installEventFilter(self)
        self._save_button.installEventFilter(self)
        layout.addWidget(self._cancel_button)
        layout.addWidget(self._save_button)
        return layout

    def _select_page(self, index: int, focus_first: bool = True) -> None:
        """Select a category and update active styling and content."""
        self._current_page = index
        self._pages.setCurrentIndex(index)
        self._page_title.setText("Appearance" if index == 0 else "Home screen")
        self._page_description.setText(
            "Personalize the look and motion of your launcher."
            if index == 0
            else "Choose which applications are available from the home screen."
        )
        for button_index, button in enumerate(self._nav_buttons):
            button.setProperty("active", button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)
        if focus_first:
            self._focus_first_page_control()

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
        """Move between whole settings rows and the category rail/footer."""
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
            if row_index == len(rows) - 1:
                self._save_button.setFocus(Qt.FocusReason.OtherFocusReason)
            else:
                rows[row_index + 1].setFocus(Qt.FocusReason.OtherFocusReason)

    def _handle_row_adjustment(self, row: SettingsRow, key: object) -> None:
        """Adjust a row's child value using left and right input."""
        if row is self._theme_row and isinstance(self._theme_combo, QComboBox):
            step = -1 if key == Qt.Key.Key_Left else 1
            self._theme_combo.setCurrentIndex(
                max(0, min(self._theme_combo.count() - 1, self._theme_combo.currentIndex() + step))
            )
        elif row is self._motion_row and isinstance(self._reduced_motion, QCheckBox):
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
                self._focus_nav(min(nav_index + 1, len(self._nav_buttons) - 1))
                return True
            if key == Qt.Key.Key_Up:
                self._focus_nav(max(nav_index - 1, 0))
                return True
            if key == Qt.Key.Key_Right:
                self._select_page(nav_index)
                self._focus_first_page_control()
                return True
        if watched in (self._cancel_button, self._save_button):
            if key == Qt.Key.Key_Left:
                self._cancel_button.setFocus()
                return True
            if key == Qt.Key.Key_Right:
                self._save_button.setFocus()
                return True
            if key == Qt.Key.Key_Up:
                controls = self._page_controls.get(self._current_page, [])
                if controls:
                    controls[-1].setFocus()
                    return True
        if watched is self._applications:
            current_row = self._applications.currentRow()
            last_row = self._applications.count() - 1
            if key == Qt.Key.Key_Left:
                self._focus_selected_nav()
                return True
            if key == Qt.Key.Key_Up and current_row == 0:
                self._focus_selected_nav()
                return True
            if key == Qt.Key.Key_Down and current_row == last_row:
                self._save_button.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            return super().eventFilter(watched, event)
        controls = self._page_controls.get(self._current_page, [])
        if watched in controls:
            return super().eventFilter(watched, event)
        return super().eventFilter(watched, event)

    def _submit(self) -> None:
        """Validate visible application selection before emitting the update."""
        selected_applications = tuple(
            self._applications.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self._applications.count())
            if self._applications.item(index).checkState() is Qt.CheckState.Checked
        )
        if not selected_applications:
            self.show_error("Select at least one application for the home screen.")
            return
        self._error_label.clear()
        self.settings_submitted.emit(
            SettingsUpdate(
                theme=self._theme_combo.currentData(),
                reduced_motion=self._reduced_motion.isChecked(),
                visible_applications=selected_applications,
            )
        )

    def show_fullscreen(self) -> None:
        """Open the settings surface fullscreen and focus its category rail."""
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._nav_buttons[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def show_error(self, message: str) -> None:
        """Display a recoverable error inside the settings content area."""
        self._error_label.setText(message)
        self._error_label.setFocus(Qt.FocusReason.OtherFocusReason)
