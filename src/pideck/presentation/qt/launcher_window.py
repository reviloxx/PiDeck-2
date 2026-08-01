"""Fullscreen Qt launcher window for Milestone 2."""

import math
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QApplication,
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
from pideck.domain.configuration import Configuration
from pideck.domain.configuration import ApplicationDefinition, ApplicationProfile, PreferredInput
from pideck.domain.theme import ThemeDefinition

from .theme import apply_theme, configure_tile_effect, icon_for_path
from .localization import clock_text, tr


class LauncherTile(QPushButton):
    """Focusable application tile that emits the configured app on activation."""

    activated = Signal(object)
    navigation_requested = Signal(object)

    def __init__(
        self,
        application: ApplicationDefinition,
        theme: ThemeDefinition,
        asset_root: Path,
        input_icon: Path | None = None,
        reduced_motion: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """Create one themed tile for an application definition."""
        super().__init__(parent)
        self.application = application
        self.setObjectName("launcher_tile")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(theme.tile.width, theme.tile.height)
        self.setText("")
        self._build_content(application, asset_root, input_icon)
        self._spinner = QLabel(self)
        self._spinner.setObjectName("tile_spinner")
        self._spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner.setGeometry(self.width() - 32, 8, 20, 20)
        self._spinner.setVisible(False)
        self._spinner_timer = QTimer(self)
        self._spinner_timer.timeout.connect(self._advance_spinner)
        self._spinner_frames = ("|", "/", "-", "\\")
        self._spinner_index = 0
        self.clicked.connect(lambda: self.activated.emit(application))
        configure_tile_effect(self, theme, reduced_motion)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Forward directional keys to the launcher's grid controller."""
        direction_by_key = {
            Qt.Key.Key_Left: NavigationDirection.LEFT,
            Qt.Key.Key_Right: NavigationDirection.RIGHT,
            Qt.Key.Key_Up: NavigationDirection.UP,
            Qt.Key.Key_Down: NavigationDirection.DOWN,
        }
        direction = direction_by_key.get(event.key())
        if direction is not None:
            self.navigation_requested.emit(direction)
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_content(
        self,
        application: ApplicationDefinition,
        asset_root: Path,
        input_icon: Path | None,
    ) -> None:
        """Build the compact icon, title, and input-indicator tile content."""
        content = QVBoxLayout(self)
        content.setContentsMargins(12, 10, 12, 10)
        content.setSpacing(6)
        application_icon = QLabel(self)
        application_icon.setObjectName("tile_icon")
        application_icon.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        application_icon.setPixmap(
            icon_for_path(_resolve_asset(application.icon, asset_root)).pixmap(56, 56)
        )
        content.addWidget(application_icon)

        name = QLabel(application.name, self)
        name.setObjectName("tile_name")
        name.setWordWrap(True)
        content.addWidget(name)
        content.addStretch()

        input_indicator = QLabel(self)
        input_indicator.setObjectName("tile_input")
        input_indicator.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        input_indicator.setToolTip(_preferred_input_tooltip(application.preferred_input))
        if input_icon is not None:
            input_indicator.setPixmap(icon_for_path(input_icon).pixmap(24, 24))
        content.addWidget(input_indicator)
    def resizeEvent(self, event: object) -> None:
        """Keep the spinner anchored to the tile's top-right corner."""
        super().resizeEvent(event)
        self._spinner.setGeometry(self.width() - 32, 8, 20, 20)

    def focusInEvent(self, event: object) -> None:
        """Enable the theme glow when the tile receives keyboard focus."""
        super().focusInEvent(event)
        effect = self.property("focus_effect")
        animation = self.property("focus_animation")
        if effect is not None and animation is not None:
            effect.setEnabled(True)
            animation.stop()
            animation.setStartValue(effect.blurRadius())
            animation.setEndValue(26)
            animation.start()

    def focusOutEvent(self, event: object) -> None:
        """Disable the theme glow when the tile loses keyboard focus."""
        super().focusOutEvent(event)
        effect = self.property("focus_effect")
        animation = self.property("focus_animation")
        if effect is not None and animation is not None:
            animation.stop()
            animation.setStartValue(effect.blurRadius())
            animation.setEndValue(8)
            animation.finished.connect(lambda: effect.setEnabled(False), Qt.ConnectionType.SingleShotConnection)
            animation.start()

    def set_running(self, running: bool) -> None:
        """Apply the theme's running state to this tile."""
        self.setProperty("running", running)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_loading(self, loading: bool) -> None:
        """Show or hide the animated loading spinner on this tile."""
        self._spinner.setVisible(loading)
        if loading:
            self._spinner_timer.start(120)
        else:
            self._spinner_timer.stop()

    def _advance_spinner(self) -> None:
        """Advance the spinner without changing the tile geometry."""
        self._spinner.setText(self._spinner_frames[self._spinner_index])
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)


class LauncherAction(QPushButton):
    """Focusable footer action that participates in directional navigation."""

    navigation_requested = Signal(object)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Forward directional keys to the launcher footer navigation."""
        direction_by_key = {
            Qt.Key.Key_Left: NavigationDirection.LEFT,
            Qt.Key.Key_Right: NavigationDirection.RIGHT,
            Qt.Key.Key_Up: NavigationDirection.UP,
            Qt.Key.Key_Down: NavigationDirection.DOWN,
        }
        direction = direction_by_key.get(event.key())
        if direction is not None:
            self.navigation_requested.emit(direction)
            event.accept()
            return
        super().keyPressEvent(event)


class LauncherWindow(QMainWindow):
    """Present configurable application tiles with TV-friendly keyboard focus."""

    application_requested = Signal(object)
    session_started = Signal(object, object)
    session_finished = Signal(object, int)
    session_failed = Signal(object, str)
    session_visible = Signal(object)
    visibility_timed_out = Signal(object)
    settings_requested = Signal()
    shutdown_requested = Signal()

    def __init__(
        self,
        controller: LauncherController,
        theme: ThemeDefinition,
        asset_root: Path,
        reduced_motion: bool = False,
        show_clock: bool = True,
        language: str = "en",
        parent: QWidget | None = None,
    ) -> None:
        """Build the launcher presentation around injected application state."""
        super().__init__(parent)
        self._controller = controller
        self._theme = theme
        self._asset_root = asset_root
        self._reduced_motion = reduced_motion
        self._show_clock = show_clock
        self._language = language if language in {"en", "de"} else "en"
        self._running_identifier: str | None = None
        self._has_been_shown = False
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
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self.session_started.connect(self._handle_session_started)
        self.session_finished.connect(self._handle_session_finished)
        self.session_failed.connect(self._handle_session_failed)
        self.session_visible.connect(self._handle_session_visible)
        self.visibility_timed_out.connect(self._handle_visibility_timeout)
        self._build_header()
        self._grid_container = QWidget(self._root)
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(0, 20, 0, 12)
        self._grid_layout.setHorizontalSpacing(theme.tile.gap)
        self._grid_layout.setVerticalSpacing(theme.tile.gap)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._root_layout.addWidget(self._grid_container, stretch=1)
        self._build_footer()
        apply_theme(self, theme)
        self._rebuild_tiles()

    def show_launcher(self) -> None:
        """Show the launcher fullscreen and focus its first available tile."""
        if not self._has_been_shown:
            self._controller.reset_focus()
            self._has_been_shown = True
        self.showFullScreen()
        self._focus_current_tile()
        QTimer.singleShot(0, self._enforce_fullscreen)

    def resizeEvent(self, event: object) -> None:
        """Reflow tiles when the available screen geometry changes."""
        super().resizeEvent(event)
        if self._controller.state.applications and self._column_count() != self._controller.state.columns:
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
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("PiDeck", self._root)
        title.setObjectName("launcher_title")
        subtitle = QLabel(tr(self._language, "choose_application"), self._root)
        self._subtitle_label = subtitle
        subtitle.setObjectName("launcher_subtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        self._clock_label = QLabel(self._root)
        self._clock_label.setObjectName("launcher_clock")
        self._clock_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        header.addWidget(self._clock_label)
        self._status_label = QLabel("", self._root)
        self._status_label.setObjectName("launcher_subtitle")
        self._root_layout.addLayout(header)
        self._root_layout.addWidget(self._status_label)
        self.set_clock_visible(self._show_clock)

    def set_clock_visible(self, visible: bool) -> None:
        """Show or hide the live home-screen clock."""
        self._show_clock = visible
        self._clock_label.setVisible(visible)
        if visible:
            self._update_clock()
            self._clock_timer.start(1000)
        else:
            self._clock_timer.stop()

    def _update_clock(self) -> None:
        """Refresh the date and time using the local system timezone."""
        self._clock_label.setText(clock_text(self._language))

    def set_language(self, language: str) -> None:
        """Translate launcher controls and switch the clock date format."""
        self._language = language if language in {"en", "de"} else "en"
        self._subtitle_label.setText(tr(self._language, "choose_application"))
        self._settings_button.setText(tr(self._language, "settings"))
        self._shutdown_button.setText(tr(self._language, "shutdown"))
        self._update_clock()

    def _build_footer(self) -> None:
        """Create intent-only settings and shutdown controls."""
        footer = QHBoxLayout()
        footer.addStretch()
        settings = LauncherAction(tr(self._language, "settings"), self._root)
        settings.setObjectName("launcher_action")
        settings.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        settings.setIcon(icon_for_path(self._theme.icons.get("settings")))
        settings.clicked.connect(self.settings_requested)
        settings.navigation_requested.connect(
            lambda direction: self._handle_action_navigation(settings, direction)
        )
        shutdown = LauncherAction(tr(self._language, "shutdown"), self._root)
        shutdown.setObjectName("launcher_action")
        shutdown.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        shutdown.setIcon(icon_for_path(self._theme.icons.get("power")))
        shutdown.clicked.connect(self.shutdown_requested)
        shutdown.navigation_requested.connect(
            lambda direction: self._handle_action_navigation(shutdown, direction)
        )
        self._settings_button = settings
        self._shutdown_button = shutdown
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
            empty_state = QLabel(tr(self._language, "no_applications"), self._grid_container)
            empty_state.setObjectName("empty_state")
            empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._grid_layout.addWidget(empty_state, 0, 0)
            self._controller.set_columns(1)
            return
        columns = self._column_count()
        self._controller.set_columns(columns)
        for index, application in enumerate(applications):
            tile = LauncherTile(
                application,
                self._theme,
                self._asset_root,
                self._theme.icons.get(_preferred_input_icon_key(application.preferred_input)),
                self._reduced_motion,
                self._grid_container,
            )
            tile.set_running(application.identifier == self._running_identifier)
            tile.activated.connect(self.application_requested)
            tile.navigation_requested.connect(self._handle_tile_navigation)
            self._tiles.append(tile)
            self._grid_layout.addWidget(tile, index // columns, index % columns)
        self._focus_current_tile()

    def _column_count(self) -> int:
        """Calculate balanced tile columns from count and available geometry."""
        available_width, available_height = self._available_grid_size()
        return calculate_tile_columns(
            len(self._controller.state.applications),
            available_width,
            available_height,
            self._theme.tile.width,
            self._theme.tile.height,
            self._theme.tile.gap,
        )

    def _available_grid_size(self) -> tuple[int, int]:
        """Return usable dimensions before or after the window is visible."""
        if self.isVisible():
            return max(self.width() - 96, 1), max(self.height() - 180, 1)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return self._theme.tile.width, self._theme.tile.height
        geometry = screen.availableGeometry()
        return max(geometry.width() - 96, 1), max(geometry.height() - 180, 1)

    def _enforce_fullscreen(self) -> None:
        """Reassert fullscreen after the window manager maps the launcher."""
        if not self.isVisible():
            return
        if not self.isFullScreen():
            self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._focus_current_tile()

    def _focus_current_tile(self) -> None:
        """Focus the tile selected by the pure launcher controller."""
        if self._tiles:
            self._tiles[self._controller.state.focused_index].setFocus(Qt.FocusReason.OtherFocusReason)

    def _handle_tile_navigation(self, direction: NavigationDirection) -> None:
        """Move the controller and focus the resulting tile."""
        current_index = self._controller.state.focused_index
        columns = self._controller.state.columns
        if (
            direction is NavigationDirection.DOWN
            and current_index + columns >= len(self._controller.state.applications)
        ):
            self._settings_button.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._controller.move(direction)
        self._focus_current_tile()

    def _handle_action_navigation(
        self,
        action: LauncherAction,
        direction: NavigationDirection,
    ) -> None:
        """Move between footer actions or return to the selected tile."""
        if action is self._settings_button and direction is NavigationDirection.RIGHT:
            self._shutdown_button.setFocus(Qt.FocusReason.OtherFocusReason)
        elif action is self._shutdown_button and direction is NavigationDirection.LEFT:
            self._settings_button.setFocus(Qt.FocusReason.OtherFocusReason)
        elif direction is NavigationDirection.UP:
            self._focus_current_tile()

    def apply_configuration(
        self,
        configuration: Configuration,
        theme: ThemeDefinition,
    ) -> None:
        """Apply saved settings and rebuild the launcher presentation."""
        self._controller.update_configuration(configuration)
        self._theme = theme
        self._reduced_motion = configuration.settings.reduced_motion
        self.set_clock_visible(configuration.settings.show_clock)
        self.set_language(configuration.settings.language)
        self._grid_layout.setHorizontalSpacing(theme.tile.gap)
        self._grid_layout.setVerticalSpacing(theme.tile.gap)
        self._settings_button.setIcon(icon_for_path(theme.icons.get("settings")))
        self._shutdown_button.setIcon(icon_for_path(theme.icons.get("power")))
        apply_theme(self, theme)
        self._rebuild_tiles()

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

    def notify_session_visible(self, application: ApplicationDefinition) -> None:
        """Publish external application-window readiness safely to Qt."""
        self.session_visible.emit(application)

    def notify_visibility_timeout(self, application: ApplicationDefinition) -> None:
        """Publish a readiness timeout while keeping the launcher visible."""
        self.visibility_timed_out.emit(application)

    def set_running_application(self, identifier: str | None) -> None:
        """Mark one tile as running or clear the running tile state."""
        self._running_identifier = identifier
        for tile in self._tiles:
            tile.set_running(tile.application.identifier == identifier)

    def set_loading_application(self, identifier: str | None) -> None:
        """Mark one tile as waiting for its external window."""
        for tile in self._tiles:
            tile.set_loading(tile.application.identifier == identifier)

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
            tr(self._language, "choose_profile"),
            tr(self._language, "profile_label"),
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
            tr(self._language, "replacement_title"),
            tr(self._language, "replacement_message", name=application.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _handle_session_started(
        self,
        application: ApplicationDefinition,
        profile: ApplicationProfile | None,
    ) -> None:
        """Keep the launcher raised and show a spinner until the app is visible."""
        del profile
        self.set_loading_application(application.identifier)
        self.set_running_application(None)
        self._status_label.setText(tr(self._language, "launching", name=application.name))
        self.show_launcher()

    def _handle_session_visible(self, application: ApplicationDefinition) -> None:
        """Hide the launcher only after the external application window is visible."""
        self.set_loading_application(None)
        self.set_running_application(application.identifier)
        self._status_label.setText(tr(self._language, "running", name=application.name))
        self.hide()

    def _handle_visibility_timeout(self, application: ApplicationDefinition) -> None:
        """Keep the launcher visible and spinner active when readiness is unverified."""
        self.set_loading_application(application.identifier)
        self._status_label.setText(tr(self._language, "waiting", name=application.name))
        self.show_launcher()

    def _handle_session_finished(self, application: ApplicationDefinition, return_code: int) -> None:
        """Restore the launcher after an application exits."""
        self.set_running_application(None)
        self.set_loading_application(None)
        self._status_label.setText(
            tr(self._language, "exited", name=application.name, code=return_code)
        )
        self.show_launcher()

    def _handle_session_failed(self, application: ApplicationDefinition, message: str) -> None:
        """Keep the launcher visible and show a safe launch error."""
        self.set_running_application(None)
        self.set_loading_application(None)
        self._status_label.setText(
            tr(self._language, "could_not_start", name=application.name, message=message)
        )
        self.show_launcher()


def _preferred_input_icon_key(preferred_input: PreferredInput) -> str:
    """Return the configured theme icon key for an input source."""
    return {
        PreferredInput.CEC: "input_cec",
        PreferredInput.GAMEPAD: "input_gamepad",
        PreferredInput.KEYBOARD: "input_keyboard",
        PreferredInput.MOUSE: "input_mouse",
    }[preferred_input]


def _preferred_input_tooltip(preferred_input: PreferredInput) -> str:
    """Return an accessible description for the input indicator icon."""
    return {
        PreferredInput.CEC: "HDMI-CEC remote",
        PreferredInput.GAMEPAD: "Game controller",
        PreferredInput.KEYBOARD: "Keyboard",
        PreferredInput.MOUSE: "Mouse",
    }[preferred_input]


def calculate_tile_columns(
    application_count: int,
    available_width: int,
    available_height: int,
    tile_width: int,
    tile_height: int,
    gap: int,
) -> int:
    """Choose a compact grid shape that respects screen width and height."""
    if application_count <= 0:
        return 1
    max_columns = max(1, (available_width + gap) // (tile_width + gap))
    aspect_adjustment = (available_width * tile_height) / max(
        available_height * tile_width,
        1,
    )
    ideal_columns = max(1, math.ceil(math.sqrt(application_count * aspect_adjustment)))
    return min(application_count, max_columns, ideal_columns)


def _resolve_asset(path: Path | None, asset_root: Path) -> Path | None:
    """Resolve a relative application icon below the configured asset root."""
    if path is None:
        return None
    return path if path.is_absolute() else asset_root / path
