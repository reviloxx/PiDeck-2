"""TV-friendly settings dialog for Milestone 5."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from pideck.application.settings import SettingsUpdate
from pideck.domain.configuration import Configuration
from pideck.domain.theme import ThemeDefinition

from .theme import apply_theme, icon_for_path


class SettingsWindow(QDialog):
    """Edit persisted launcher appearance and home-screen settings."""

    settings_submitted = Signal(object)

    def __init__(
        self,
        configuration: Configuration,
        theme: ThemeDefinition,
        asset_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        """Build a settings dialog from the current validated configuration."""
        super().__init__(parent)
        self._configuration = configuration
        self._theme = theme
        self._asset_root = asset_root
        self.setWindowTitle("PiDeck Settings")
        self.setObjectName("settings_window")
        self.setModal(True)
        self._build_ui()
        apply_theme(self, theme)

    def _build_ui(self) -> None:
        """Create settings controls with keyboard-friendly tab order."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 36, 48, 36)
        layout.setSpacing(20)

        title = QLabel("Settings", self)
        title.setObjectName("launcher_title")
        layout.addWidget(title)

        appearance_label = QLabel("Appearance", self)
        appearance_label.setObjectName("launcher_subtitle")
        layout.addWidget(appearance_label)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        self._theme_combo = QComboBox(self)
        self._theme_combo.setObjectName("settings_theme")
        for theme in self._configuration.themes:
            self._theme_combo.addItem(theme.name, theme.identifier)
        current_theme_index = self._theme_combo.findData(self._configuration.home.theme)
        if current_theme_index >= 0:
            self._theme_combo.setCurrentIndex(current_theme_index)
        form.addRow("Theme", self._theme_combo)
        self._reduced_motion = QCheckBox("Reduce animation", self)
        self._reduced_motion.setObjectName("settings_reduced_motion")
        self._reduced_motion.setChecked(self._configuration.settings.reduced_motion)
        form.addRow("Motion", self._reduced_motion)
        layout.addLayout(form)

        home_label = QLabel("Home screen applications", self)
        home_label.setObjectName("launcher_subtitle")
        layout.addWidget(home_label)
        self._applications = QListWidget(self)
        self._applications.setObjectName("settings_applications")
        visible_ids = set(self._configuration.home.visible_applications)
        all_visible = not visible_ids
        for application in self._configuration.applications:
            item = QListWidgetItem(application.name, self._applications)
            item.setData(Qt.ItemDataRole.UserRole, application.identifier)
            item.setCheckState(
                Qt.CheckState.Checked
                if all_visible or application.identifier in visible_ids
                else Qt.CheckState.Unchecked
            )
        layout.addWidget(self._applications, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        if self._save_button is not None:
            self._save_button.setIcon(icon_for_path(self._theme.icons.get("settings")))

    def _submit(self) -> None:
        """Validate visible application selection before emitting the update."""
        selected_applications = tuple(
            self._applications.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self._applications.count())
            if self._applications.item(index).checkState() is Qt.CheckState.Checked
        )
        if not selected_applications:
            QMessageBox.warning(
                self,
                "Application required",
                "Select at least one application for the home screen.",
            )
            return
        update = SettingsUpdate(
            theme=self._theme_combo.currentData(),
            reduced_motion=self._reduced_motion.isChecked(),
            visible_applications=selected_applications,
        )
        self.settings_submitted.emit(update)

    def show_fullscreen(self) -> None:
        """Open the settings surface as a fullscreen TV-friendly view."""
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def show_error(self, message: str) -> None:
        """Display a recoverable persistence or validation error."""
        QMessageBox.critical(self, "Settings not saved", message)
