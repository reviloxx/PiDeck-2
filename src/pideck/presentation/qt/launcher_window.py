"""Fullscreen Qt launcher window for Milestone 2."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QInputDialog,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pideck.application.launcher import LauncherController, NavigationDirection
from pideck.domain.configuration import ApplicationDefinition, ApplicationProfile, PreferredInput
from pideck.domain.theme import ThemeDefinition

from .theme import apply_theme, configure_tile_effect, icon_for_path


class LauncherTile(QPushButton):
    """Focusable application tile that emits the configured app on activation."""

    activated = Signal(object)

    def __init__(
        self,
        application: ApplicationDefinition,
        theme: ThemeDefinition,
        asset_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        """Create one themed tile for an application definition."""
        super().__init__(parent)
        self.application = application
        self.setObjectName("launcher_tile")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(theme.tile.width, theme.tile.height)
        self.setIcon(icon_for_path(_resolve_asset(application.icon, asset_root)))
        self.setIconSize(self.sizeHint())
        self.setText(f"{application.name}\n{_preferred_input_label(application.preferred_input)}")
        self.clicked.connect(lambda: self.activated.emit(application))
        configure_tile_effect(self, theme)

    def focusInEvent(self, event: object) -> None:
        """Enable the theme glow when the tile receives keyboard focus."""
        super().focusInEvent(event)
        effect = self.property("focus_effect")
        if effect is not None:
            effect.setEnabled(True)

    def focusOutEvent(self, event: object) -> None:
        """Disable the theme glow when the tile loses keyboard focus."""
        super().focusOutEvent(event)
        effect = self.property("focus_effect")
        if effect is not None:
            effect.setEnabled(False)


class LauncherWindow(QMainWindow):
    """Present configurable application tiles with TV-friendly keyboard focus."""

    application_requested = Signal(object)
    session_started = Signal(object, object)
    session_finished = Signal(object, int)
    session_failed = Signal(object, str)
    settings_requested = Signal()
    shutdown_requested = Signal()

    def __init__(
        self,
        controller: LauncherController,
        theme: ThemeDefinition,
        asset_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        """Build the launcher presentation around injected application state."""
        super().__init__(parent)
        self._controller = controller
        self._theme = theme
        self._asset_root = asset_root
        self._tiles: list[LauncherTile] = []
        self.setObjectName("launcher_window")
        self.setWindowTitle("PiDeck")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._root = QWidget(self)
        self._root.setObjectName("launcher_root")
        self.setCentralWidget(self._root)
        self._root_layout = QVBoxLayout(self._root)
        self._root_layout.setContentsMargins(48, 36, 48, 28)
        self._root_layout.setSpacing(12)
        self.session_started.connect(self._handle_session_started)
        self.session_finished.connect(self._handle_session_finished)
        self.session_failed.connect(self._handle_session_failed)
        self._build_header()
        self._grid_container = QWidget(self._root)
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(0, 20, 0, 12)
        self._grid_layout.setHorizontalSpacing(theme.tile.gap)
        self._grid_layout.setVerticalSpacing(theme.tile.gap)
        self._root_layout.addWidget(self._grid_container, stretch=1)
        self._build_footer()
        apply_theme(self, theme)
        self._rebuild_tiles()

    def show_launcher(self) -> None:
        """Show the launcher fullscreen and focus its first available tile."""
        self.showFullScreen()
        self._focus_current_tile()

    def resizeEvent(self, event: object) -> None:
        """Reflow tiles when the available screen geometry changes."""
        super().resizeEvent(event)
        self._rebuild_tiles()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Translate arrow, activation, and escape keys into launcher intents."""
        direction_by_key = {
            Qt.Key.Key_Left: NavigationDirection.LEFT,
            Qt.Key.Key_Right: NavigationDirection.RIGHT,
            Qt.Key.Key_Up: NavigationDirection.UP,
            Qt.Key.Key_Down: NavigationDirection.DOWN,
        }
        direction = direction_by_key.get(event.key())
        if direction is not None:
            self._controller.move(direction)
            self._focus_current_tile()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            application = self._controller.activate()
            if application is not None:
                self.application_requested.emit(application)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.shutdown_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_header(self) -> None:
        """Create the launcher title and contextual subtitle."""
        title = QLabel("PiDeck", self._root)
        title.setObjectName("launcher_title")
        subtitle = QLabel("Choose an application", self._root)
        subtitle.setObjectName("launcher_subtitle")
        self._status_label = QLabel("", self._root)
        self._status_label.setObjectName("launcher_subtitle")
        self._root_layout.addWidget(title)
        self._root_layout.addWidget(subtitle)
        self._root_layout.addWidget(self._status_label)

    def _build_footer(self) -> None:
        """Create intent-only settings and shutdown controls."""
        footer = QHBoxLayout()
        footer.addStretch()
        settings = QPushButton("Settings", self._root)
        settings.setObjectName("launcher_action")
        settings.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        settings.clicked.connect(self.settings_requested)
        shutdown = QPushButton("Shutdown", self._root)
        shutdown.setObjectName("launcher_action")
        shutdown.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        shutdown.clicked.connect(self.shutdown_requested)
        footer.addWidget(settings)
        footer.addWidget(shutdown)
        self._root_layout.addLayout(footer)

    def _rebuild_tiles(self) -> None:
        """Rebuild the responsive grid while preserving the current selection."""
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._tiles.clear()
        applications = self._controller.state.applications
        if not applications:
            empty_state = QLabel("No applications configured", self._grid_container)
            empty_state.setObjectName("empty_state")
            empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._grid_layout.addWidget(empty_state, 0, 0)
            self._controller.set_columns(1)
            return
        columns = self._column_count()
        self._controller.set_columns(columns)
        for index, application in enumerate(applications):
            tile = LauncherTile(application, self._theme, self._asset_root, self._grid_container)
            tile.activated.connect(self.application_requested)
            self._tiles.append(tile)
            self._grid_layout.addWidget(tile, index // columns, index % columns)
        self._focus_current_tile()

    def _column_count(self) -> int:
        """Calculate tile columns from current width and theme tile dimensions."""
        available_width = max(self.width() - 96, self._theme.tile.width)
        return max(1, (available_width + self._theme.tile.gap) // (self._theme.tile.width + self._theme.tile.gap))

    def _focus_current_tile(self) -> None:
        """Focus the tile selected by the pure launcher controller."""
        if self._tiles:
            self._tiles[self._controller.state.focused_index].setFocus(Qt.FocusReason.OtherFocusReason)

    def notify_session_started(
        self,
        application: ApplicationDefinition,
        profile: ApplicationProfile | None,
    ) -> None:
        """Publish a process-start event safely from any supervisor thread."""
        self.session_started.emit(application, profile)

    def notify_session_finished(self, application: ApplicationDefinition, return_code: int) -> None:
        """Publish a process-exit event safely from any supervisor thread."""
        self.session_finished.emit(application, return_code)

    def notify_session_failed(self, application: ApplicationDefinition, message: str) -> None:
        """Publish a process-error event safely from any supervisor thread."""
        self.session_failed.emit(application, message)

    def choose_profile(
        self,
        profiles: tuple[ApplicationProfile, ...],
    ) -> ApplicationProfile | None:
        """Ask the user to choose an application profile."""
        if not profiles:
            return None
        names = [profile.name for profile in profiles]
        selected_name, accepted = QInputDialog.getItem(
            self,
            "Choose profile",
            "Application profile:",
            names,
            0,
            False,
        )
        if not accepted:
            return None
        return next(profile for profile in profiles if profile.name == selected_name)

    def confirm_application_replacement(self, application: ApplicationDefinition) -> bool:
        """Ask whether the running application should be stopped."""
        answer = QMessageBox.question(
            self,
            "Application already running",
            f"Stop the current application and start {application.name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _handle_session_started(
        self,
        application: ApplicationDefinition,
        profile: ApplicationProfile | None,
    ) -> None:
        """Hide the launcher after a process has started successfully."""
        del profile
        self._status_label.setText(f"Running {application.name}")
        self.hide()

    def _handle_session_finished(self, application: ApplicationDefinition, return_code: int) -> None:
        """Restore the launcher after an application exits."""
        self._status_label.setText(f"{application.name} exited ({return_code})")
        self.show_launcher()

    def _handle_session_failed(self, application: ApplicationDefinition, message: str) -> None:
        """Keep the launcher visible and show a safe launch error."""
        self._status_label.setText(f"Could not start {application.name}: {message}")
        self.show_launcher()


def _preferred_input_label(preferred_input: PreferredInput) -> str:
    """Return compact text for the tile's preferred input indicator."""
    return {
        PreferredInput.CEC: "CEC remote",
        PreferredInput.GAMEPAD: "Game controller",
        PreferredInput.KEYBOARD: "Keyboard",
        PreferredInput.MOUSE: "Mouse",
    }[preferred_input]


def _resolve_asset(path: Path | None, asset_root: Path) -> Path | None:
    """Resolve a relative application icon below the configured asset root."""
    if path is None:
        return None
    return path if path.is_absolute() else asset_root / path
