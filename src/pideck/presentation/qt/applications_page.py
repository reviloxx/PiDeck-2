"""Settings list showing every supported application and its install action.

Each catalog application is one list item; the list stays a single focus
target so keyboard, controller and CEC navigation work like the Home screen
list. While an application is installed or removed, its item hosts an
``InstallProgressRow``.
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QWidget

from pideck.application.app_management import CatalogItemStatus
from pideck.application.ports.installation import InstallProgress
from pideck.domain.catalog import InstallState

from .install_row import InstallProgressRow
from .localization import tr

# Vertical space taken by the list's ``::item`` padding and border (theme.py).
_LIST_ITEM_CHROME = 2 * (12 + 1)
_STATE_TEXT = {
    InstallState.NOT_INSTALLED: "install_application",
    InstallState.UNAVAILABLE: "application_unavailable",
    InstallState.REQUIRES_NEWER_IMAGE: "application_requires_image",
}


class ApplicationsList(QListWidget):
    """Render install states and emit install, removal or cancel requests."""

    install_requested = Signal(str)
    remove_requested = Signal(str)
    cancel_requested = Signal(str)

    def __init__(
        self, statuses: tuple[CatalogItemStatus, ...], language: str, spinner_color: str,
        parent: QWidget | None = None,
    ) -> None:
        """Create one item per supported application."""
        super().__init__(parent)
        self.setObjectName("settings_catalog")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._language = language
        self._spinner_color = spinner_color
        self._statuses: dict[str, CatalogItemStatus] = {}
        self._busy_id: str | None = None
        self._busy_removing = False
        self._busy_row: InstallProgressRow | None = None
        self._busy_progress: InstallProgress | None = None
        self.itemClicked.connect(self._activate_item)
        self.set_statuses(statuses)

    @property
    def busy_id(self) -> str | None:
        """Return the application currently being installed or removed."""
        return self._busy_id

    def set_statuses(self, statuses: tuple[CatalogItemStatus, ...]) -> None:
        """Replace all items, keeping the current row and any progress row."""
        row = self.currentRow()
        busy, removing, progress = self._busy_id, self._busy_removing, self._busy_progress
        self._clear_busy_row()
        self.clear()
        self._statuses = {status.identifier: status for status in statuses}
        for status in statuses:
            item = QListWidgetItem(self)
            item.setData(Qt.ItemDataRole.UserRole, status.identifier)
        self._busy_id = None
        self.set_busy(busy, removing=removing)
        if busy is not None and progress is not None:
            self.set_progress(busy, progress)
        self._render_texts()
        if 0 <= row < self.count():
            self.setCurrentRow(row)

    def set_language(self, language: str) -> None:
        """Translate item texts and the progress row."""
        self._language = language
        if self._busy_row is not None:
            self._busy_row.set_language(language)
        self._render_texts()

    def set_busy(self, application_id: str | None, *, removing: bool = False) -> None:
        """Show or clear the progress row for the running operation."""
        if application_id == self._busy_id and removing == self._busy_removing:
            return
        self._clear_busy_row()
        self._busy_id, self._busy_removing = application_id, removing
        self._busy_progress = None
        item = self._item(application_id) if application_id is not None else None
        status = self._statuses.get(application_id or "")
        if item is not None and status is not None:
            row = InstallProgressRow(status.entry.name, self._language, removing=removing)
            row.spinner.set_color(self._spinner_color)
            self._busy_row = row
            self.setItemWidget(item, row)
            row.ensurePolished()
            for label in row.findChildren(QLabel):
                label.ensurePolished()
            hint = row.layout().sizeHint()
            item.setSizeHint(QSize(hint.width(), hint.height() + _LIST_ITEM_CHROME))
        self._render_texts()

    def set_progress(self, application_id: str, progress: InstallProgress) -> None:
        """Render progress for the running operation only."""
        if application_id == self._busy_id and self._busy_row is not None:
            self._busy_progress = progress
            self._busy_row.set_progress(progress)

    def progress_row(self) -> InstallProgressRow | None:
        """Return the visible progress row, if any."""
        return self._busy_row

    def activate_current(self) -> None:
        """Activate the focused item as if it was clicked."""
        item = self.currentItem()
        if item is not None:
            self._activate_item(item)

    def _activate_item(self, item: QListWidgetItem) -> None:
        application_id = item.data(Qt.ItemDataRole.UserRole)
        status = self._statuses.get(application_id)
        if status is None:
            return
        if application_id == self._busy_id:
            if not self._busy_removing:
                self.cancel_requested.emit(application_id)
            return
        if self._busy_id is not None:
            return
        if status.state is InstallState.NOT_INSTALLED:
            self.install_requested.emit(application_id)
        elif status.state is InstallState.INSTALLED and status.removable:
            self.remove_requested.emit(application_id)

    def _render_texts(self) -> None:
        for index in range(self.count()):
            item = self.item(index)
            status = self._statuses[item.data(Qt.ItemDataRole.UserRole)]
            item.setText("" if status.identifier == self._busy_id else self._text(status))

    def _text(self, status: CatalogItemStatus) -> str:
        name = status.entry.name
        if status.state is InstallState.INSTALLED:
            key = "remove_application" if status.removable else "application_installed"
            return tr(self._language, key, name=name)
        return tr(self._language, _STATE_TEXT[status.state], name=name)

    def _item(self, application_id: str) -> QListWidgetItem | None:
        for index in range(self.count()):
            item = self.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == application_id:
                return item
        return None

    def _clear_busy_row(self) -> None:
        if self._busy_row is None:
            return
        item = self._item(self._busy_id or "")
        if item is not None:
            self.removeItemWidget(item)
            item.setSizeHint(QSize())
        self._busy_row = None
