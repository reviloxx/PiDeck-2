"""Qt tests for the Milestone 5 settings dialog."""

import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from pideck.application.app_management import CatalogItemStatus
from pideck.application.application_items import ApplicationAction
from pideck.application.settings import SettingsUpdate
from pideck.application.pideck_updates import PiDeckUpdateInfo, PiDeckUpdateStatus
from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.installation import InstallProgress, InstallStage
from pideck.application.ports.updates import UpdateInfo, UpdateStatus
from pideck.domain.catalog import InstallState
from pideck.domain.configuration import Configuration
from pideck.domain.input_settings import CecSettings
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.install_row import install_status_text
from pideck.presentation.qt.settings_window import PasswordPrompt, SettingsWindow
from tests.test_configuration import configuration_document
from pideck.infrastructure.config.catalog import load_catalog


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for settings tests."""
    return QApplication.instance() or QApplication([])


def settings_window(application: QApplication) -> SettingsWindow:
    """Build a settings dialog from representative configuration."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd())
    window.show()
    application.processEvents()
    return window


def test_input_settings_are_accessible_and_immediate(application: QApplication) -> None:
    """CEC controls emit updates and manual requests from controller navigation."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), input_settings=CecSettings())
    changes: list[CecSettings] = []
    requests: list[bool] = []
    window.input_settings_changed.connect(changes.append)
    window.cec_connection_requested.connect(requests.append)
    window.show()
    application.processEvents()
    assert window._input_page is not None
    window._select_page(window._input_page)
    window._input_rows["cec_auto"].setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert changes[-1].auto_connect is False
    window._input_port.setCurrentIndex(window._input_port.findData(2))
    assert changes[-1].hdmi_port == 2
    window._input_rows["cec_connect"].setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requests == [True]
    window.apply_language("de")
    window.set_cec_status("Connected")
    assert window._input_status.text() == "Verbunden"
    window.close()


def catalog_statuses() -> tuple[CatalogItemStatus, ...]:
    """Represent every install state with real catalog entries."""
    entries = {entry.identifier: entry for entry in load_catalog(Path.cwd() / "assets/catalog/applications.yaml")}
    return (
        CatalogItemStatus(entries["kodi"], InstallState.NOT_INSTALLED),
        CatalogItemStatus(entries["youtube"], InstallState.NOT_INSTALLED),
        CatalogItemStatus(entries["chrome"], InstallState.UNAVAILABLE),
        CatalogItemStatus(entries["brave"], InstallState.REQUIRES_NEWER_IMAGE),
        CatalogItemStatus(entries["firefox"], InstallState.INSTALLED),
    )


def applications_window(application: QApplication) -> SettingsWindow:
    """Open settings on the Applications page."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), catalog_statuses=catalog_statuses())
    window.show()
    window.select_applications_page()
    application.processEvents()
    return window


def row_texts(row) -> tuple[str, str]:
    """Return a row's detail and status texts."""
    return row.detail_label.text(), row.status_label.text()


def open_menu(window: SettingsWindow, application: QApplication, key: str):
    """Activate a row with the controller and return its action menu."""
    window._apps.row(key).setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    return window._apps.dialog


def test_applications_page_lists_configured_and_catalog_applications(application: QApplication) -> None:
    """Configured apps come first with update state, then every other supported app."""
    window = applications_window(application)
    page = window.applications_page
    assert window._page_title.text() == "Applications"
    assert [row.item.key for row in page.rows()] == ["browser", "kodi", "youtube", "chrome", "brave", "firefox"]
    assert window.focusWidget() is page.rows()[0]
    assert row_texts(page.row("browser"))[0] == "Shown on the home screen"
    assert row_texts(page.row("youtube")) == ("", "Not installed")
    assert row_texts(page.row("chrome"))[1] == "Not available on this device"
    assert row_texts(page.row("brave"))[1] == "Requires a newer PiDeck image"
    assert row_texts(page.row("firefox"))[1] == "Installed · not on the home screen"
    window.apply_language("de")
    assert window._nav_buttons[1].text() == "Anwendungen"
    assert row_texts(page.row("youtube"))[1] == "Nicht installiert"
    window.close()


def test_action_menu_offers_state_dependent_actions(application: QApplication) -> None:
    """Menus only contain actions that apply to the application's state."""
    window = applications_window(application)
    assert open_menu(window, application, "browser").actions() == ("hide",)
    window._apps.dialog.reject()
    application.processEvents()
    assert window.focusWidget() is window._apps.row("browser")
    assert open_menu(window, application, "kodi").actions() == ("install",)
    window._apps.dialog.reject()
    assert open_menu(window, application, "firefox").actions() == ("show",)
    window._apps.dialog.reject()
    window._apps.row("chrome").setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert window._apps.dialog is None
    window.close()


def sorting_window(application: QApplication, identifiers: tuple[str, ...]):
    """Open Applications with the given configured apps, saving requested orders like the composition root."""
    from pideck.application.home_layout import set_application_order

    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier} for identifier in identifiers
    ]
    document["home"]["visible_applications"] = list(identifiers)
    configuration = YamlConfigurationParser().parse(document)
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), catalog_statuses=catalog_statuses())
    orders: list[tuple[str, ...]] = []

    def save(order: tuple[str, ...]) -> None:
        orders.append(order)
        window.set_configuration(set_application_order(window._configuration, order))

    window.application_order_requested.connect(save)
    window.show()
    window.select_applications_page()
    application.processEvents()
    return window, orders


def configured_order(window: SettingsWindow) -> list[str]:
    """Return the configured row keys in display order."""
    return [row.item.key for row in window._apps.rows() if row.item.configured]


def press(window: SettingsWindow, key: Qt.Key) -> None:
    """Send a key press to the focused widget like a keyboard does."""
    QTest.keyClick(window.focusWidget(), key)


def test_menu_requests_install_cancel_visibility_and_moves(application: QApplication) -> None:
    """Menu actions emit intents and close the menu; Move starts sorting in the list."""
    window, orders = sorting_window(application, ("one", "two"))
    visibility, installs = [], []
    window.application_visibility_requested.connect(lambda *args: visibility.append(args))
    window.application_install_requested.connect(installs.append)
    menu = open_menu(window, application, "one")
    assert menu.actions() == ("hide", "move")
    menu.choose(ApplicationAction.MOVE)
    application.processEvents()
    assert window._apps.dialog is None and window._apps.is_sorting
    assert window.focusWidget() is window._apps.row("one")
    assert window._apps.row("one").detail_label.text().startswith("Up/Down to move")
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert not window._apps.is_sorting and orders == []
    menu = open_menu(window, application, "one")
    menu.choose(ApplicationAction.HIDE)
    application.processEvents()
    assert visibility == [("one", False)] and window._apps.dialog is None
    open_menu(window, application, "kodi").choose(ApplicationAction.INSTALL)
    assert installs == ["kodi"]
    window.set_application_busy("kodi")
    row = window._apps.row("kodi")
    assert row.detail_label.text() == "Installing Kodi" and row.spinner.running
    assert row.progress_bar.isVisible() and row.progress_bar.maximum() == 0
    window.set_application_progress("kodi", InstallProgress(InstallStage.DOWNLOADING, 42, 2, 3))
    assert row.progress_bar.value() == 42 and row.status_label.text() == "Step 2 of 3 · 42%"
    window.set_application_progress("other", InstallProgress(InstallStage.FINISHING, 100))
    assert row.progress_bar.value() == 42
    window.set_catalog_statuses(catalog_statuses())
    assert window._apps.row("kodi").progress_bar.value() == 42
    menu = open_menu(window, application, "kodi")
    assert menu.actions() == ("cancel_install",)
    menu.choose(ApplicationAction.CANCEL_INSTALL)
    assert installs == ["kodi", "kodi"]
    assert open_menu(window, application, "youtube") is None or "install" not in window._apps.dialog.actions()
    window.close()


def start_sorting(window: SettingsWindow, application: QApplication, key: str) -> None:
    """Choose Move in a row's menu."""
    open_menu(window, application, key).choose(ApplicationAction.MOVE)
    application.processEvents()
    assert window._apps.is_sorting


def test_sorting_moves_rows_in_place_and_saves_once(application: QApplication) -> None:
    """Up/Down reorder the same row widgets; Activate saves once and navigation keeps working."""
    window, orders = sorting_window(application, ("one", "two", "three"))
    moved = window._apps.row("one")
    start_sorting(window, application, "one")
    window.handle_input(InputEvent(InputAction.UP, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert configured_order(window) == ["two", "three", "one"] and orders == []
    assert window._apps.row("one") is moved and window.focusWidget() is moved
    assert [row.item.key for row in window._apps.rows()][3] == "kodi"
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert orders == [("two", "three", "one")] and not window._apps.is_sorting
    assert configured_order(window) == ["two", "three", "one"]
    assert window._configuration.home.visible_applications == ("two", "three", "one")
    assert window.focusWidget() is window._apps.row("one")
    assert window._apps.row("one").detail_label.text() == "Shown on the home screen"
    window.handle_input(InputEvent(InputAction.UP, "gamepad"))
    assert window.focusWidget() is window._apps.row("three")
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert window._apps.dialog is not None and window._apps.dialog.focusWidget() is not None
    window.close()


def test_sorting_cancel_restores_order_and_keyboard_works(application: QApplication) -> None:
    """Back and Escape restore the order without saving or closing settings; Enter saves."""
    window, orders = sorting_window(application, ("one", "two", "three"))
    start_sorting(window, application, "three")
    window.handle_input(InputEvent(InputAction.UP, "gamepad"))
    assert configured_order(window) == ["one", "three", "two"]
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert configured_order(window) == ["one", "two", "three"] and orders == []
    assert window.isVisible() and window.focusWidget() is window._apps.row("three")
    start_sorting(window, application, "three")
    press(window, Qt.Key.Key_Up)
    press(window, Qt.Key.Key_Up)
    assert configured_order(window) == ["three", "one", "two"]
    press(window, Qt.Key.Key_Escape)
    assert window.isVisible() and configured_order(window) == ["one", "two", "three"] and orders == []
    start_sorting(window, application, "one")
    press(window, Qt.Key.Key_Down)
    press(window, Qt.Key.Key_Return)
    application.processEvents()
    assert orders == [("two", "one", "three")] and window.focusWidget() is window._apps.row("one")
    press(window, Qt.Key.Key_Down)
    assert window.focusWidget() is window._apps.row("three")
    window.close()


def test_rebuild_while_menu_is_open_keeps_focus_on_live_row(application: QApplication) -> None:
    """A model refresh behind an open menu hands the window's focus to the rebuilt row."""
    window, _orders = sorting_window(application, ("one", "two"))
    menu = open_menu(window, application, "two")
    window.set_configuration(window._configuration)
    application.processEvents()
    assert window.focusWidget() is window._apps.row("two")
    menu.reject()
    application.processEvents()
    assert window.focusWidget() is window._apps.row("two")
    window.handle_input(InputEvent(InputAction.UP, "gamepad"))
    assert window.focusWidget() is window._apps.row("one")
    window.close()


@pytest.mark.parametrize("accept", [True, False])
def test_removal_requires_confirmation(application: QApplication, accept: bool) -> None:
    """Removing asks first, explains that data is kept and cannot be cancelled midway."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    statuses = tuple(
        replace(status, removable=True) if status.identifier == "firefox" else status for status in catalog_statuses()
    )
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), catalog_statuses=statuses)
    window.show()
    removals: list[str] = []
    window.application_remove_requested.connect(removals.append)
    menu = open_menu(window, application, "firefox")
    assert menu.actions() == ("show", "remove")
    menu.choose(ApplicationAction.REMOVE)
    application.processEvents()
    dialog = window._apps.dialog
    assert dialog is not None and "settings and data are kept" in dialog.findChildren(QLabel)[1].text()
    window.handle_input(InputEvent(InputAction.BACK if not accept else InputAction.RIGHT, "gamepad"))
    if accept:
        window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert removals == (["firefox"] if accept else [])
    assert window._apps.dialog is None
    window.set_application_busy("firefox", removing=True)
    row = window._apps.row("firefox")
    assert row.detail_label.text() == "Removing Firefox"
    assert open_menu(window, application, "firefox") is None
    window.close()


def test_applications_page_navigation_returns_to_rail(application: QApplication) -> None:
    """Up from the first row and Left return to the category rail."""
    window = applications_window(application)
    rows = window._apps.rows()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert rows[1].hasFocus()
    window.handle_input(InputEvent(InputAction.LEFT, "gamepad"))
    assert window.focusWidget() is window._nav_buttons[1]
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert rows[0].hasFocus()
    QTest.keyClick(rows[0], Qt.Key.Key_Up)
    assert window.focusWidget() is window._nav_buttons[1]
    window.close()


@pytest.mark.parametrize(
    ("progress", "text"),
    [
        (InstallProgress(InstallStage.PREPARING), "Preparing..."),
        (InstallProgress(InstallStage.INSTALLING_FLATPAK), "Installing Flatpak..."),
        (InstallProgress(InstallStage.DOWNLOADING), "Starting download..."),
        (InstallProgress(InstallStage.DOWNLOADING, 7, 1, 1), "Downloading · 7%"),
        (InstallProgress(InstallStage.FINISHING, 100), "Finishing..."),
    ],
)
def test_install_status_texts(progress: InstallProgress, text: str) -> None:
    """Every installation stage has a readable status."""
    assert install_status_text("en", progress) == text


@pytest.mark.parametrize(("width", "height"), [(1280, 720), (1920, 1080)])
def test_settings_wallpaper_covers_dialog(
    application: QApplication, tmp_path: Path, width: int, height: int,
) -> None:
    """Wallpaper fills the actual dialog, including its outer margins."""
    image = QImage(100, 50, QImage.Format.Format_RGB32)
    image.fill(QColor("#ab4512"))
    wallpaper = tmp_path / "wallpaper.png"
    assert image.save(str(wallpaper))
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, replace(DEFAULT_THEME, wallpaper=wallpaper), tmp_path)
    window.resize(width, height)
    window.show()
    application.processEvents()
    rendered = window.grab().toImage()
    for x, y in ((0, 0), (rendered.width() - 1, 0), (0, rendered.height() - 1)):
        assert rendered.pixelColor(x, y) == QColor("#ab4512")
    window.close()


@pytest.mark.parametrize(
    "status",
    [status for status in UpdateStatus if status not in (UpdateStatus.AVAILABLE, UpdateStatus.UPDATING)],
)
def test_non_actionable_update_cannot_start(
    application: QApplication, status: UpdateStatus,
) -> None:
    """Neither keyboard nor gamepad starts updates in non-actionable states."""
    class Service:
        def __init__(self) -> None:
            self.starts = 0

        def check_async(self, applications, callback) -> None:
            pass

        def start_async(self, application, password, callback) -> None:
            self.starts += 1

    service = Service()
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), update_service=service)
    window.show()
    window._select_page(1)
    row = window._apps._rows["browser"]
    row.set_info(UpdateInfo("browser", "Browser", status))
    application.processEvents()
    QTest.keyClick(row, Qt.Key.Key_Return)
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert service.starts == 0
    window.close()


@pytest.mark.parametrize("status", list(PiDeckUpdateStatus))
def test_pideck_activation_is_guarded(
    application: QApplication, status: PiDeckUpdateStatus,
) -> None:
    """PiDeck requests are only emitted for available updates and retries."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(
        configuration, DEFAULT_THEME, Path.cwd(),
        pideck_update_info=PiDeckUpdateInfo(status, "1.0.0", "1.1.0"),
    )
    requests: list[bool] = []
    window.pideck_update_requested.connect(lambda: requests.append(True))
    window.show()
    window._select_page(2)
    application.processEvents()
    assert window._pideck_row is not None
    QTest.keyClick(window._pideck_row, Qt.Key.Key_Return)
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    expected = 2 if status in (PiDeckUpdateStatus.AVAILABLE, PiDeckUpdateStatus.FAILED) else 0
    assert len(requests) == expected
    window.close()


def test_pideck_update_row_supports_controller_and_localization(application: QApplication) -> None:
    """PiDeck's row is independent of configured IDs and exposes update progress."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(
        configuration, DEFAULT_THEME, Path.cwd(),
        pideck_update_info=PiDeckUpdateInfo(PiDeckUpdateStatus.AVAILABLE, "1.0.0", "1.1.0"),
    )
    requests = []
    window.pideck_update_requested.connect(lambda: requests.append(True))
    window.show()
    application.processEvents()
    window._select_page(2)
    row = window._pideck_row
    assert row is not None
    assert row.hasFocus()
    assert "1.0.0" in row.version_label.text()
    assert "1.1.0" in row.status_label.text()
    QTest.keyClick(row, Qt.Key.Key_Return)
    assert requests == [True]
    window.set_pideck_update_info(PiDeckUpdateInfo(PiDeckUpdateStatus.PREPARING, "1.0.0", "1.1.0"))
    assert not row.action.isVisible()
    window.set_pideck_update_info(PiDeckUpdateInfo(PiDeckUpdateStatus.PENDING, "1.0.0", "1.1.0"))
    window.apply_language("de")
    assert "neu starten" in row.status_label.text()
    QTest.keyClick(row, Qt.Key.Key_Down)
    assert row.hasFocus()
    window._select_page(1)
    assert window._apps._rows["browser"].hasFocus()
    window.close()


@pytest.mark.parametrize("status", list(PiDeckUpdateStatus))
def test_pideck_manual_check_is_guarded(application: QApplication, status: PiDeckUpdateStatus) -> None:
    """Manual checks are available only in stable, supported updater states."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(
        configuration, DEFAULT_THEME, Path.cwd(),
        pideck_update_info=PiDeckUpdateInfo(status, "1.0.0", "1.1.0"),
    )
    checks: list[bool] = []
    updates: list[bool] = []
    window.pideck_check_requested.connect(lambda: checks.append(True))
    window.pideck_update_requested.connect(lambda: updates.append(True))
    window.show()
    window._select_page(2)
    application.processEvents()
    enabled = status in (PiDeckUpdateStatus.UP_TO_DATE, PiDeckUpdateStatus.AVAILABLE, PiDeckUpdateStatus.FAILED)
    assert window._pideck_check_button.isEnabled() is enabled
    window._pideck_check_button.click()
    window._request_pideck_check()
    assert len(checks) == (2 if enabled else 0)
    assert not updates
    window.close()


def test_pideck_manual_check_navigation_and_language(application: QApplication) -> None:
    """Keyboard, mouse, and gamepad can reach and activate the localized check button."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(
        configuration, DEFAULT_THEME, Path.cwd(),
        pideck_update_info=PiDeckUpdateInfo(PiDeckUpdateStatus.UP_TO_DATE, "1.0.0"),
    )
    checks: list[bool] = []
    window.pideck_check_requested.connect(lambda: checks.append(True))
    window.show()
    window._select_page(2)
    application.processEvents()
    row = window._pideck_row
    assert row is not None
    button = window._pideck_check_button
    assert button.text() == "Check for updates"
    QTest.keyClick(row, Qt.Key.Key_Down)
    assert button.hasFocus()
    QTest.keyClick(button, Qt.Key.Key_Return)
    QTest.keyClick(button, Qt.Key.Key_Space)
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert len(checks) == 4
    window.apply_language("de")
    assert button.text() == "Nach Aktualisierungen suchen"
    window.set_pideck_update_info(PiDeckUpdateInfo(PiDeckUpdateStatus.CHECKING, "1.0.0"))
    assert not button.isEnabled()
    assert row.hasFocus()
    QTest.keyClick(row, Qt.Key.Key_Down)
    assert row.hasFocus()
    window.set_pideck_update_info(PiDeckUpdateInfo(PiDeckUpdateStatus.UP_TO_DATE, "1.0.0"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert button.hasFocus()
    window.handle_input(InputEvent(InputAction.UP, "gamepad"))
    assert row.hasFocus()
    window.close()


def test_versions_and_build_date_are_visible(application: QApplication) -> None:
    """Each update page shows installed versions and the PiDeck build date."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(
        configuration, DEFAULT_THEME, Path.cwd(),
        pideck_update_info=PiDeckUpdateInfo(PiDeckUpdateStatus.UP_TO_DATE, "1.2.3"),
        build_date="2026-01-01T10:00:00+00:00",
    )
    window.show()
    window._select_page(2)
    assert "January 2026" in window._build_date_label.text()
    assert "T10:00" not in window._build_date_label.text()
    assert "1.2.3" in window._pideck_row.version_label.text()
    row = window._apps._rows["browser"]
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UP_TO_DATE, "2.3"))
    assert "2.3" in row.version_label.text()
    assert row.build_label.isHidden()
    assert not row.build_label.text()
    row.set_info(UpdateInfo(
        "browser", "Browser", UpdateStatus.UP_TO_DATE, "2.3", build_date="2026-01-01",
    ))
    assert "2026-01-01" in row.build_label.text()
    assert not row.build_label.isHidden()
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UPDATING))
    assert not row.build_label.isHidden()
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UPDATED))
    assert row.build_label.isHidden()
    row.set_info(UpdateInfo(
        "browser", "Browser", UpdateStatus.UP_TO_DATE, installed_commit="abcdef123456",
    ))
    assert row.version_label.text() == "Installed commit: abcdef123456"
    window.apply_language("de")
    assert row.version_label.text() == "Installierter Commit: abcdef123456"
    assert row.build_label.isHidden()
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UP_TO_DATE))
    assert "abcdef123456" not in row.version_label.text()
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UP_TO_DATE, build_date="2026-02-01"))
    assert "2026-02-01" in row.build_label.text()
    assert not row.build_label.isHidden()
    window.close()


def test_sidebar_navigation_hint_is_removed(application: QApplication) -> None:
    """Only the redundant navigation instruction disappears in both languages."""
    window = settings_window(application)
    for language in ("en", "de"):
        window.apply_language(language)
        texts = [label.text() for label in window.findChildren(QLabel)]
        assert "Use the arrow keys or controller to navigate" not in texts
        assert "Mit Pfeiltasten oder Controller navigieren" not in texts
    window.close()


def test_settings_window_loads_current_values(application: QApplication) -> None:
    """The dialog reflects the current theme, motion, and applications."""
    window = settings_window(application)

    assert window._theme_combo.currentData() == "default"
    assert window._reduced_motion.isChecked() is False
    assert [row.item.key for row in window._apps.rows()] == ["browser"]
    assert window._apps.row("browser").item.visible

    window.close()


def test_settings_window_emits_typed_update(application: QApplication) -> None:
    """Saving the dialog emits a typed update instead of editing YAML directly."""
    window = settings_window(application)
    updates: list[SettingsUpdate] = []
    window.settings_submitted.connect(updates.append)
    window._reduced_motion.setChecked(True)

    window._submit()

    assert updates == [SettingsUpdate("default", True, ("browser",))]
    window.close()


def test_settings_window_emits_changes_immediately(application: QApplication) -> None:
    """Changing a value emits the update without a Save action."""
    window = settings_window(application)
    updates: list[SettingsUpdate] = []
    window.settings_changed.connect(updates.append)

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Right)
    QTest.keyClick(window._theme_row, Qt.Key.Key_Down)
    QTest.keyClick(window._motion_row, Qt.Key.Key_Space)

    assert updates
    assert updates[-1].reduced_motion is True
    assert not hasattr(window, "_save_button")
    assert not hasattr(window, "_cancel_button")
    window.close()


def test_german_language_updates_settings_labels(application: QApplication) -> None:
    """Selecting German translates the visible settings navigation and controls."""
    window = settings_window(application)

    window._language_combo.setCurrentIndex(1)
    application.processEvents()

    assert window._nav_buttons[0].text() == "Darstellung"
    assert window._nav_buttons[1].text() == "Anwendungen"
    assert window._clock_labels[0].text() == "Uhr"
    assert window._language_labels[0].text() == "Sprache"
    assert window._show_clock.state_text() == "Ein"
    assert window._reduced_motion.state_text() == ("Ein" if window._reduced_motion.isChecked() else "Aus")
    window.close()


def test_settings_text_uses_configurable_theme_sizes(application: QApplication) -> None:
    """Settings text consistently uses the dedicated TV-friendly theme font size."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    theme = replace(
        DEFAULT_THEME,
        fonts=replace(
            DEFAULT_THEME.fonts, family="Liberation Serif",
            settings_body_size=24, settings_heading_size=34,
        ),
    )
    window = SettingsWindow(configuration, theme, Path.cwd())
    window.resize(1280, 720)
    window.show()
    window._select_page(0)
    application.processEvents()
    for widget in (
        window._nav_buttons[0], window._back_button, window._page_description,
        window._theme_labels[0], window._theme_labels[1], window._theme_combo,
    ):
        assert widget.font().pixelSize() == 24
        assert widget.font().family() == "Liberation Serif"
    assert window._page_title.font().pixelSize() == 34
    window.close()


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize(("width", "height"), [(800, 600), (1280, 720)])
def test_settings_are_responsive_and_controls_reachable(
    application: QApplication, language: str, width: int, height: int,
) -> None:
    """Full settings remain readable and keyboard-reachable at both screen sizes."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(
        configuration, DEFAULT_THEME, Path.cwd(),
        pideck_update_info=PiDeckUpdateInfo(PiDeckUpdateStatus.AVAILABLE, "1.0", "1.1"),
        system_enabled=True,
    )
    window.resize(width, height)
    window.show()
    window.apply_language(language)
    application.processEvents()
    for button in window._nav_buttons:
        assert button.width() >= button.sizeHint().width()
    for page in range(len(window._nav_buttons)):
        window._select_page(page)
        application.processEvents()
        assert window.size().toTuple() == (width, height)

    window._select_page(2)
    window._pideck_check_button.setFocus()
    application.processEvents()
    viewport = window._pideck_scroll.viewport()
    button = window._pideck_check_button
    assert viewport.rect().contains(button.mapTo(viewport, QPoint(button.width() - 1, button.height() - 1)))

    window._select_page(0)
    for row, control in (
        (window._theme_row, window._theme_combo),
        (window._motion_row, window._reduced_motion),
        (window._clock_row, window._show_clock),
        (window._language_row, window._language_combo),
    ):
        row.setFocus()
        application.processEvents()
        viewport = window._appearance_scroll.viewport()
        top_left = control.mapTo(viewport, QPoint(0, 0))
        bottom_right = control.mapTo(viewport, QPoint(control.width() - 1, control.height() - 1))
        assert viewport.rect().contains(top_left)
        assert viewport.rect().contains(bottom_right)
    QTest.keyClick(window._language_row, Qt.Key.Key_Return)
    assert window._language_combo.view().isVisible()
    QTest.keyClick(window._language_combo.view(), Qt.Key.Key_Escape)
    window.close()


def test_gamepad_language_dropdown_confirms_or_cancels_selection(application: QApplication) -> None:
    """Browsing a language does not persist it until the controller confirms."""
    window = settings_window(application)
    window._select_page(0)
    window._language_row.setFocus()
    changes: list[SettingsUpdate] = []
    window.settings_changed.connect(changes.append)
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert window._language_combo.view().isVisible()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window._language_combo.currentData() == "en"
    assert changes == []
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert window._language_combo.currentData() == "en"
    assert window.isVisible()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert window._language_combo.currentData() == "de"
    assert len(changes) == 1
    window.close()


def test_language_dropdown_renders_visible_selected_item(application: QApplication) -> None:
    """The selected popup item is visibly distinct while navigating."""
    window = settings_window(application)
    window._select_page(0)
    window._language_row.setFocus()
    QTest.keyClick(window._language_row, Qt.Key.Key_Return)
    view = window._language_combo.view()
    QTest.keyClick(view, Qt.Key.Key_Down)
    application.processEvents()
    image = view.grab().toImage()
    first = view.visualRect(view.model().index(0, 0))
    selected = view.visualRect(view.model().index(1, 0))
    assert selected.height() >= 42
    assert image.pixelColor(first.right() - 10, first.center().y()) != image.pixelColor(
        selected.right() - 10, selected.center().y()
    )
    window._language_combo.hidePopup()
    window.close()


def test_appearance_changes_keep_saved_visibility(application: QApplication) -> None:
    """Appearance updates carry the saved tiles; the all-visible sentinel is resolved."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two")
    ]
    document["home"]["visible_applications"] = []
    configuration = YamlConfigurationParser().parse(document)
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd())
    updates: list[SettingsUpdate] = []
    window.settings_changed.connect(updates.append)
    window.show()
    window._reduced_motion.setChecked(True)
    assert updates[-1].visible_applications == ("one", "two")
    from pideck.application.home_layout import set_visibility

    window.set_configuration(set_visibility(configuration, "one", False))
    window._reduced_motion.setChecked(False)
    assert updates[-1].visible_applications == ("two",)
    assert window._apps.row("one").detail_label.text() == "Hidden from the home screen"
    window.close()


def test_settings_window_supports_dpad_navigation(application: QApplication) -> None:
    """Arrow keys move through categories, controls, applications, and actions."""
    window = settings_window(application)

    assert window._nav_buttons[0].hasFocus()
    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Left)
    assert window._nav_buttons[0].hasFocus()
    assert window._nav_buttons[0].property("active") is True
    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Right)
    assert window._theme_row.hasFocus()
    QTest.keyClick(window._theme_row, Qt.Key.Key_Down)
    assert window._motion_row.hasFocus()
    QTest.keyClick(window._motion_row, Qt.Key.Key_Space)
    assert window._reduced_motion.isChecked() is True
    QTest.keyClick(window._motion_row, Qt.Key.Key_Return)
    assert window._reduced_motion.isChecked() is False
    QTest.keyClick(window._motion_row, Qt.Key.Key_Down)
    assert window._clock_row.hasFocus()
    QTest.keyClick(window._clock_row, Qt.Key.Key_Space)
    assert window._show_clock.isChecked() is False
    QTest.keyClick(window._clock_row, Qt.Key.Key_Up)
    QTest.keyClick(window._motion_row, Qt.Key.Key_Up)
    QTest.keyClick(window._theme_row, Qt.Key.Key_Up)
    assert window._nav_buttons[0].hasFocus()
    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    assert window._nav_buttons[1].hasFocus()
    assert window._nav_buttons[1].property("active") is True
    assert window._nav_buttons[0].property("active") is False
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Down)
    assert window._back_button.hasFocus()
    QTest.keyClick(window._back_button, Qt.Key.Key_Space)
    assert window.isVisible() is False
    return


def test_application_rows_accept_keyboard_navigation(application: QApplication) -> None:
    """Application rows move vertically before handing focus back to the rail."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two", "three")
    ]
    document["home"]["visible_applications"] = []
    configuration = YamlConfigurationParser().parse(document)
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd())
    window.show()
    application.processEvents()

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Right)
    rows = window._apps.rows()
    assert rows[0].hasFocus()
    QTest.keyClick(rows[0], Qt.Key.Key_Down)
    assert rows[1].hasFocus()
    QTest.keyClick(rows[1], Qt.Key.Key_Up)
    assert rows[0].hasFocus()
    QTest.keyClick(rows[0], Qt.Key.Key_Left)
    assert window._nav_buttons[1].hasFocus()
    assert [button.property("active") for button in window._nav_buttons] == [False, True]
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Down)
    assert window._back_button.hasFocus()
    assert [button.property("active") for button in window._nav_buttons] == [False, False]

    window.close()





def test_application_rows_render_update_status(application: QApplication) -> None:
    """Rows show availability and spinner state; the action lives in the menu."""
    window = settings_window(application)

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Right)
    row = window._apps._rows["browser"]
    assert row.hasFocus()

    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.AVAILABLE, available_version="21.3-Omega"))
    assert row.action.isHidden()
    assert row.status_label.text() == "Update available: 21.3-Omega"
    assert row.detail_label.text() == "Shown on the home screen"
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UPDATING))
    assert row.spinner.isVisible()
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UP_TO_DATE))
    QTest.keyClick(row, Qt.Key.Key_Down)
    assert window._apps._rows["browser"].hasFocus()

    window.close()


def test_long_update_list_scrolls_with_keyboard_focus(application: QApplication) -> None:
    """Every update remains visible when navigating a long application list."""
    document = configuration_document()
    document["applications"] = [
        {"id": f"app{index}", "name": f"Application {index}", "executable": f"app{index}"}
        for index in range(12)
    ]
    document["home"]["visible_applications"] = ["app0"]
    configuration = YamlConfigurationParser().parse(document)
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd())
    window.resize(1280, 720)
    window.show()
    window._select_page(1)
    application.processEvents()
    for index in range(11):
        QTest.keyClick(window._apps._rows[f"app{index}"], Qt.Key.Key_Down)
        application.processEvents()
    assert window._apps._rows["app11"].hasFocus()
    assert window._apps.verticalScrollBar().value() > 0
    window.close()


def test_gamepad_actions_navigate_settings(application: QApplication) -> None:
    """Normalized gamepad actions follow the same Settings focus rules."""
    window = settings_window(application)

    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window._nav_buttons[1].hasFocus()
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert window._apps.rows()[0].hasFocus()
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert window.isVisible() is False


def test_gamepad_arrows_navigate_open_language_dropdown(application: QApplication) -> None:
    """Gamepad arrows select a popup choice and activation applies it."""
    window = settings_window(application)
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window._language_row.hasFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert window._language_combo.view().isVisible()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window._language_combo.view().currentIndex().row() == 1
    assert window._language_combo.currentData() == "en"
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert window._language_combo.view().isVisible() is False
    assert window._language_combo.currentData() == "de"
    window.close()


def test_password_prompt_abort_restores_available_update(application: QApplication, monkeypatch) -> None:
    """Cancelling authentication leaves the update available and actionable."""
    window = settings_window(application)
    row = window._apps._rows["browser"]
    available = UpdateInfo("browser", "Browser", UpdateStatus.AVAILABLE)
    row.set_info(available)
    monkeypatch.setattr(
        "pideck.presentation.qt.update_rows.PasswordPrompt.exec",
        lambda self: QDialog.DialogCode.Rejected,
    )

    window._apps._handle_update_status(
        UpdateInfo("browser", "Browser", UpdateStatus.PASSWORD_REQUIRED)
    )

    assert row.status_label.text() == "Update available"
    assert row.info.status is UpdateStatus.AVAILABLE
    window.close()


def test_cancelled_update_is_rechecked(application: QApplication) -> None:
    """Cancelling an update rechecks and restores its actual availability."""
    class FakeUpdateService:
        """Minimal update service double for cancellation behavior."""

        def __init__(self) -> None:
            self.cancelled: list[str] = []
            self.check_callback = None

        def check_async(self, applications, callback) -> None:
            """Capture the recheck callback."""
            self.check_callback = callback

        def start_async(self, application, password, callback) -> None:
            """Provide the service protocol method."""

        def cancel(self, application_id: str) -> None:
            """Record cancellation."""
            self.cancelled.append(application_id)

    service = FakeUpdateService()
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), update_service=service)
    window.show()
    application.processEvents()
    row = window._apps._rows["browser"]
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UPDATING))

    open_menu(window, application, "browser").choose(ApplicationAction.CANCEL_UPDATE)
    assert service.cancelled == ["browser"]
    assert row.status_label.text() == "Checking..."

    service.check_callback(UpdateInfo("browser", "Browser", UpdateStatus.AVAILABLE))
    application.processEvents()
    assert row.status_label.text() == "Update available"
    assert open_menu(window, application, "browser").actions()[0] == "update"
    window.close()


def test_completed_update_refreshes_installed_version(application: QApplication) -> None:
    """A completed update rechecks the installed version instead of showing stale data."""
    class Service:
        def __init__(self) -> None:
            self.checks = []

        def check_async(self, applications, callback) -> None:
            self.checks.append((applications, callback))

    service = Service()
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), update_service=service)
    window.show()
    row = window._apps._rows["browser"]
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.AVAILABLE, "1.0", "2.0"))
    window._apps._handle_update_status(UpdateInfo("browser", "Browser", UpdateStatus.UPDATED))
    assert "1.0" not in row.version_label.text()
    assert len(service.checks) == 2
    service.checks[-1][1](UpdateInfo("browser", "Browser", UpdateStatus.UP_TO_DATE, "2.0"))
    application.processEvents()
    assert "2.0" in row.version_label.text()
    window.close()


def test_password_prompt_supports_keyboard_navigation(application: QApplication) -> None:
    """Password entry and both prompt actions are reachable with arrows."""
    prompt = PasswordPrompt("Firefox", DEFAULT_THEME, None)
    prompt.show()
    application.processEvents()

    assert prompt.password_input.hasFocus()
    QTest.keyClick(prompt.password_input, Qt.Key.Key_Down)
    assert prompt.continue_button.hasFocus()
    QTest.keyClick(prompt.continue_button, Qt.Key.Key_Left)
    assert prompt.cancel_button.hasFocus()
    QTest.keyClick(prompt.cancel_button, Qt.Key.Key_Right)
    assert prompt.continue_button.hasFocus()
    QTest.keyClick(prompt.continue_button, Qt.Key.Key_Up)
    assert prompt.password_input.hasFocus()
    QTest.keyClick(prompt.password_input, Qt.Key.Key_Escape)
    assert prompt.isVisible() is False


def test_about_page_shows_scrollable_release_notes(application: QApplication) -> None:
    """Available updates show combined notes; controller input scrolls before moving on."""
    from pideck.application.pideck_updates import ReleaseNote

    long_text = "\n".join(f"- change {index}" for index in range(80))
    info = PiDeckUpdateInfo(
        PiDeckUpdateStatus.AVAILABLE, "1.0.0", "1.2.0",
        release_notes=(ReleaseNote("1.2.0", "- **Bold** [link](https://example.org)"), ReleaseNote("1.1.0", long_text)),
    )
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), pideck_update_info=info)
    window.resize(1280, 720)
    window.show()
    window._select_page(window._pideck_page)
    application.processEvents()
    notes = window._release_notes
    assert notes.isVisible() and window._release_notes_title.text() == "What's new in PiDeck 1.2.0"
    text = notes.toPlainText()
    assert text.index("PiDeck 1.2.0") < text.index("PiDeck 1.1.0") and "Bold" in text and "**" not in text
    window._pideck_row.setFocus()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert notes.hasFocus()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert notes.hasFocus() and notes.verticalScrollBar().value() > 0
    notes.verticalScrollBar().setValue(notes.verticalScrollBar().maximum())
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window._pideck_check_button.hasFocus()
    window.apply_language("de")
    assert window._release_notes_title.text() == "Neu in PiDeck 1.2.0 (auf Englisch)"
    window.set_pideck_update_info(PiDeckUpdateInfo(PiDeckUpdateStatus.UP_TO_DATE, "1.2.0"))
    assert notes.isHidden() and window._release_notes_title.isHidden()
    window._pideck_row.setFocus()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window._pideck_check_button.hasFocus()
    window.close()
