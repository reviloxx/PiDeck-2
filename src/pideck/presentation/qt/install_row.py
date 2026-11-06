"""Progress presentation for a catalog application being installed or removed.

The row is embedded into the Applications list as an item widget, so list
focus and keyboard/controller/CEC navigation stay unchanged. It ignores
pointer input; activating the underlying list item cancels an installation.
Removals change the package database and cannot be cancelled.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from pideck.application.ports.installation import InstallProgress, InstallStage

from .localization import tr
from .spinner import BusySpinner

_SPINNER_SIZE = 22


def install_status_text(language: str, progress: InstallProgress) -> str:
    """Describe one progress snapshot in the selected language."""
    if progress.stage is InstallStage.DOWNLOADING and progress.percent is not None:
        if progress.operations and progress.operations > 1:
            return tr(
                language, "install_downloading_step",
                step=progress.operation, steps=progress.operations, percent=progress.percent,
            )
        return tr(language, "install_downloading", percent=progress.percent)
    keys = {
        InstallStage.PREPARING: "install_preparing",
        InstallStage.INSTALLING_FLATPAK: "install_installing_flatpak",
        InstallStage.DOWNLOADING: "install_starting_download",
        InstallStage.INSTALLING: "install_installing",
        InstallStage.REMOVING: "install_removing",
        InstallStage.FINISHING: "install_finishing",
    }
    return tr(language, keys[progress.stage])


class InstallProgressRow(QWidget):
    """Show name, stage, a progress bar, a spinner and the cancel hint."""

    def __init__(
        self, application_name: str, language: str, parent: QWidget | None = None, *, removing: bool = False,
    ) -> None:
        """Create an indeterminate row until the first progress report arrives."""
        super().__init__(parent)
        self.setObjectName("install_progress_row")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._application_name = application_name
        self._language = language
        self._removing = removing
        self._progress = InstallProgress(InstallStage.PREPARING)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("install_title")
        self.spinner = BusySpinner(_SPINNER_SIZE, self)
        self.spinner.set_running(True)
        self.cancel_label = QLabel(self)
        self.cancel_label.setObjectName("install_cancel")
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("install_progress")
        self.progress_bar.setTextVisible(False)
        self.status_label = QLabel(self)
        self.status_label.setObjectName("install_status")
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(self.spinner)
        header.addWidget(self.title_label, stretch=1)
        header.addWidget(self.cancel_label)
        details = QHBoxLayout()
        details.setSpacing(12)
        details.addWidget(self.progress_bar, stretch=1)
        details.addWidget(self.status_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(details)
        self.set_progress(self._progress)

    @property
    def progress(self) -> InstallProgress:
        """Return the progress currently shown."""
        return self._progress

    def set_progress(self, progress: InstallProgress) -> None:
        """Render a progress snapshot; unknown percentages show a busy bar."""
        self._progress = progress
        if progress.percent is None:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(max(0, min(100, progress.percent)))
        self._render_text()

    def set_language(self, language: str) -> None:
        """Re-render all texts in the selected language."""
        self._language = language
        self._render_text()

    def _render_text(self) -> None:
        title = "removing_application" if self._removing else "installing_application"
        self.title_label.setText(tr(self._language, title, name=self._application_name))
        self.cancel_label.setText("" if self._removing else tr(self._language, "install_cancel_hint"))
        self.status_label.setText(install_status_text(self._language, self._progress))
