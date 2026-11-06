"""Update status rows and the package password prompt shared by settings pages."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pideck import __version__
from pideck.application.pideck_updates import PiDeckUpdateInfo, PiDeckUpdateStatus
from pideck.application.ports.updates import UpdateInfo, UpdateStatus
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .spinner import BusySpinner
from .theme import apply_theme
from .wallpaper import WallpaperDialog

_UPDATE_SPINNER_SIZE = 22


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
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout = layout
        layout.setContentsMargins(20, 14, 20, 14)
        name = QLabel(application_name, self)
        name.setObjectName("settings_label")
        name.setWordWrap(True)
        self.name_label = name
        self.detail_label = QLabel("", self)
        self.detail_label.setObjectName("settings_hint")
        self.detail_label.setWordWrap(True)
        self.detail_label.hide()
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("install_progress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        self.status_label = QLabel(tr("en", "checking"), self)
        self.status_label.setObjectName("update_status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.status_label.setWordWrap(True)
        self.version_label = QLabel("", self)
        self.version_label.setObjectName("settings_hint")
        self.version_label.setWordWrap(True)
        self.build_label = QLabel("", self)
        self.build_label.setObjectName("settings_hint")
        self.build_label.setWordWrap(True)
        self.spinner = BusySpinner(_UPDATE_SPINNER_SIZE, self)
        self.spinner.setObjectName("update_spinner")
        self.spinner.set_running(False)
        self.action = QPushButton("", self)
        self.action.setObjectName("update_action")
        self.action.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.action.clicked.connect(self.activation_requested)
        # Fixed stretch ratios keep the name, version and status columns aligned across rows.
        layout.setSpacing(16)
        name_layout = QVBoxLayout()
        name_layout.addWidget(name)
        name_layout.addWidget(self.detail_label)
        layout.addLayout(name_layout, stretch=2)
        version_layout = QVBoxLayout()
        version_layout.addWidget(self.version_label)
        version_layout.addWidget(self.build_label)
        version_layout.addWidget(self.progress_bar)
        layout.addLayout(version_layout, stretch=4)
        layout.addWidget(self.status_label, stretch=3)
        layout.addWidget(self.spinner)
        layout.addWidget(self.action)
        self.info = UpdateInfo(application_id, application_name, UpdateStatus.CHECKING)
        self.last_available_info: UpdateInfo | None = None
        self._current_version: str | None = None
        self._current_build_date: str | None = None
        self._installed_commit: str | None = None
        self._language = "en"

    def set_compact(self, compact: bool) -> None:
        """Stack update details when the settings content is narrow."""
        self._layout.setDirection(
            QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        )

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
        if info.status in (UpdateStatus.UP_TO_DATE, UpdateStatus.AVAILABLE, UpdateStatus.UNSUPPORTED) or (
            info.current_version or info.installed_commit or info.build_date
        ):
            self._current_version = info.current_version
            self._current_build_date = info.build_date
            self._installed_commit = info.installed_commit
        elif info.status is UpdateStatus.UPDATED:
            self._current_version = None
            self._current_build_date = None
            self._installed_commit = None
        if info.status is UpdateStatus.AVAILABLE:
            self.last_available_info = info
        labels = {
            UpdateStatus.CHECKING: tr(self._language, "checking"),
            UpdateStatus.UP_TO_DATE: tr(self._language, "up_to_date"),
            UpdateStatus.AVAILABLE: (
                tr(self._language, "update_available_version", version=info.available_version)
                if info.available_version
                else tr(self._language, "update_available")
            ),
            UpdateStatus.UPDATING: tr(self._language, "updating"),
            UpdateStatus.PREPARING: tr(self._language, "preparing_update"),
            UpdateStatus.RESTART_REQUIRED: tr(self._language, "restart_required"),
            UpdateStatus.UPDATED: tr(self._language, "updated"),
            UpdateStatus.PASSWORD_REQUIRED: tr(self._language, "authentication_required"),
            UpdateStatus.UNSUPPORTED: tr(self._language, "not_supported"),
            UpdateStatus.FAILED: info.message or tr(self._language, "update_failed"),
            UpdateStatus.CANCELLED: tr(self._language, "cancelled"),
        }
        self.status_label.setText(labels[info.status])
        if self._installed_commit and not self._current_version:
            self.version_label.setText(tr(
                self._language, "installed_commit", commit=self._installed_commit,
            ))
        else:
            self.version_label.setText(tr(
                self._language, "installed_version",
                version=self._current_version or tr(self._language, "unknown"),
            ))
        self.build_label.setVisible(bool(self._current_build_date))
        self.build_label.setText(
            tr(self._language, "build_date", date=self._current_build_date)
            if self._current_build_date else ""
        )
        updating = info.status is UpdateStatus.UPDATING
        busy = updating or info.status is UpdateStatus.PREPARING
        self.spinner.set_running(busy)
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

    def set_language(self, language: str) -> None:
        """Re-render the current update status in the selected language."""
        self._language = language if language in {"en", "de"} else "en"
        self.set_info(self.info)


class PiDeckUpdateRow(UpdateRow):
    """Render the image's own update state separately from configured applications."""

    def __init__(self, info: PiDeckUpdateInfo, parent: QWidget) -> None:
        """Create a dedicated row for PiDeck's installed release."""
        self._pideck_info = info
        super().__init__("", "PiDeck", parent)
        self.build_label.hide()
        self.set_pideck_info(info)

    def set_pideck_info(self, info: PiDeckUpdateInfo) -> None:
        """Display release status and retain failure details for the user."""
        self._pideck_info = info
        statuses = {
            PiDeckUpdateStatus.UNSUPPORTED: UpdateStatus.UNSUPPORTED,
            PiDeckUpdateStatus.CHECKING: UpdateStatus.CHECKING,
            PiDeckUpdateStatus.UP_TO_DATE: UpdateStatus.UP_TO_DATE,
            PiDeckUpdateStatus.AVAILABLE: UpdateStatus.AVAILABLE,
            PiDeckUpdateStatus.PREPARING: UpdateStatus.PREPARING,
            PiDeckUpdateStatus.PENDING: UpdateStatus.RESTART_REQUIRED,
            PiDeckUpdateStatus.FAILED: UpdateStatus.FAILED,
        }
        self.set_info(UpdateInfo(
            "", "PiDeck", statuses[info.status],
            info.current_version or __version__, info.available_version, info.message,
        ))
        if info.status is PiDeckUpdateStatus.AVAILABLE and info.message:
            self.status_label.setText(f"{self.status_label.text()} - {info.message}")
        if info.status is PiDeckUpdateStatus.FAILED:
            self.action.setText(tr(self._language, "retry"))
            self.action.setEnabled(True)
            self.action.setVisible(True)

    def set_language(self, language: str) -> None:
        """Re-render PiDeck update state when the language changes."""
        self._language = language if language in {"en", "de"} else "en"
        self.set_pideck_info(self._pideck_info)


class PasswordPrompt(WallpaperDialog):
    """Themed, cancellable sudo password prompt for package updates."""

    def __init__(
        self,
        application_name: str,
        theme: ThemeDefinition,
        parent: QWidget,
        language: str = "en",
        installing: bool = False,
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
        message = QLabel(tr(language, "install_password_message" if installing else "password_message", name=application_name), self)
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
