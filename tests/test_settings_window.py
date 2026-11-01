"""Qt tests for the Milestone 5 settings dialog."""

import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from pideck.application.settings import SettingsUpdate
from pideck.application.pideck_updates import PiDeckUpdateInfo, PiDeckUpdateStatus
from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.updates import UpdateInfo, UpdateStatus
from pideck.domain.configuration import Configuration
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
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


def test_catalog_item_installs_without_changing_visibility(application: QApplication) -> None:
    """Keyboard and controller activate an installable item without toggling settings."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    catalog = load_catalog(Path.cwd() / "assets/catalog/applications.yaml")
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), catalog=catalog)
    requests = []
    changes = []
    window.application_install_requested.connect(requests.append)
    window.settings_changed.connect(changes.append)
    window.show()
    window._select_page(1)
    window._applications.setCurrentRow(window._applications.count() - 1)
    window._applications.setFocus()
    QTest.keyClick(window._applications, Qt.Key.Key_Return)
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requests == ["youtube", "youtube"]
    assert changes == []
    window.set_installing("youtube")
    assert "Installing" in window._applications.currentItem().text()
    window.close()


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
    window._select_page(2)
    row = window._update_rows["browser"]
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
    window._select_page(3)
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
    window._select_page(3)
    row = window._pideck_row
    assert row is not None
    assert row.hasFocus()
    assert "1.0.0" in row.status_label.text()
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
    window._select_page(2)
    assert window._update_rows["browser"].hasFocus()
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
    window._select_page(3)
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
    window._select_page(3)
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
    window._select_page(3)
    assert "2026-01-01" in window._build_date_label.text()
    assert "1.2.3" in window._pideck_row.version_label.text()
    row = window._update_rows["browser"]
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UP_TO_DATE, "2.3"))
    assert "2.3" in row.version_label.text()
    assert "Unknown" in row.build_label.text()
    row.set_info(UpdateInfo(
        "browser", "Browser", UpdateStatus.UP_TO_DATE, "2.3", build_date="2026-01-01",
    ))
    assert "2026-01-01" in row.build_label.text()
    window.close()


def test_settings_window_loads_current_values(application: QApplication) -> None:
    """The dialog reflects the current theme, motion, and application selection."""
    window = settings_window(application)

    assert window._theme_combo.currentData() == "default"
    assert window._reduced_motion.isChecked() is False
    assert window._applications.count() == 1
    assert window._applications.item(0).checkState() is Qt.CheckState.Checked

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
    assert window._nav_buttons[1].text() == "Startbildschirm"
    assert window._nav_buttons[2].text() == "Aktualisierungen"
    assert window._clock_labels[0].text() == "Uhr"
    assert window._language_labels[0].text() == "Sprache"
    assert window._show_clock.text() == "Ein"
    assert window._reduced_motion.text() == "Ein"
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


def test_application_visibility_change_emits_immediately(application: QApplication) -> None:
    """Toggling a home-screen application emits a persisted settings update."""
    document = configuration_document()
    document["applications"] = [
        {"id": identifier, "name": identifier.title(), "executable": identifier}
        for identifier in ("one", "two")
    ]
    document["home"]["visible_applications"] = ["one", "two"]
    configuration = YamlConfigurationParser().parse(document)
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd())
    updates: list[SettingsUpdate] = []
    window.settings_changed.connect(updates.append)
    window.show()
    application.processEvents()

    window._applications.item(0).setCheckState(Qt.CheckState.Unchecked)
    application.processEvents()

    assert updates
    assert updates[-1].visible_applications == ("two",)
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
    assert window._nav_buttons[2].hasFocus()
    QTest.keyClick(window._nav_buttons[2], Qt.Key.Key_Down)
    assert window._back_button.hasFocus()
    QTest.keyClick(window._back_button, Qt.Key.Key_Space)
    assert window.isVisible() is False
    return


def test_home_application_list_accepts_keyboard_navigation(application: QApplication) -> None:
    """The home-screen list moves between application rows before handing off."""
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
    assert window._applications.hasFocus()
    assert window._applications.currentRow() == 0
    QTest.keyClick(window._applications, Qt.Key.Key_Down)
    assert window._applications.currentRow() == 1
    QTest.keyClick(window._applications, Qt.Key.Key_Up)
    assert window._applications.currentRow() == 0
    QTest.keyClick(window._applications, Qt.Key.Key_Left)
    assert window._nav_buttons[1].hasFocus()
    assert [button.property("active") for button in window._nav_buttons] == [False, True, False]
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Down)
    assert window._nav_buttons[2].hasFocus()
    QTest.keyClick(window._nav_buttons[2], Qt.Key.Key_Down)
    assert window._back_button.hasFocus()
    assert [button.property("active") for button in window._nav_buttons] == [False, False, False]

    window.close()


def test_home_page_entry_and_exit_control_list_selection(application: QApplication) -> None:
    """Rail navigation leaves the list empty; Right enters its first item."""
    window = settings_window(application)

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    assert window._nav_buttons[1].hasFocus()
    assert window._applications.currentRow() == -1

    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Right)
    assert window._applications.hasFocus()
    assert window._applications.currentRow() == 0

    QTest.keyClick(window._applications, Qt.Key.Key_Left)
    assert window._nav_buttons[1].hasFocus()
    assert window._applications.currentRow() == -1
    assert window._nav_buttons[1].property("active") is True

    window.close()


def test_updates_page_renders_status_spinner_and_navigates(application: QApplication) -> None:
    """Updates rows show availability, spinner state, and support vertical focus."""
    window = settings_window(application)

    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Down)
    QTest.keyClick(window._nav_buttons[1], Qt.Key.Key_Down)
    QTest.keyClick(window._nav_buttons[2], Qt.Key.Key_Right)
    row = window._update_rows["browser"]
    assert row.hasFocus()

    row.set_info(
        UpdateInfo(
            "browser",
            "Browser",
            UpdateStatus.AVAILABLE,
            available_version="21.3-Omega",
        )
    )
    assert row.action.isEnabled()
    assert row.action.text() == "Update"
    assert row.status_label.text() == "Update available: 21.3-Omega"
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UPDATING))
    assert row.spinner.isVisible()
    assert row.action.text() == "Cancel"
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UP_TO_DATE))
    assert row.action.isHidden() is True
    QTest.keyClick(row, Qt.Key.Key_Down)
    assert window._update_rows["browser"].hasFocus()

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
    window._select_page(2)
    application.processEvents()
    for index in range(11):
        QTest.keyClick(window._update_rows[f"app{index}"], Qt.Key.Key_Down)
        application.processEvents()
    assert window._update_rows["app11"].hasFocus()
    assert window._update_scroll.verticalScrollBar().value() > 0
    window.close()


def test_gamepad_actions_navigate_settings(application: QApplication) -> None:
    """Normalized gamepad actions follow the same Settings focus rules."""
    window = settings_window(application)

    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window._nav_buttons[1].hasFocus()
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert window._applications.hasFocus()
    assert window._applications.currentRow() == 0
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
    row = window._update_rows["browser"]
    available = UpdateInfo("browser", "Browser", UpdateStatus.AVAILABLE)
    row.set_info(available)
    monkeypatch.setattr(
        "pideck.presentation.qt.settings_window.PasswordPrompt.exec",
        lambda self: QDialog.DialogCode.Rejected,
    )

    window._handle_update_status(
        UpdateInfo("browser", "Browser", UpdateStatus.PASSWORD_REQUIRED)
    )

    assert row.status_label.text() == "Update available"
    assert row.action.text() == "Update"
    assert row.action.isHidden() is False
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
    row = window._update_rows["browser"]
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.UPDATING))

    window._activate_update("browser")
    assert service.cancelled == ["browser"]
    assert row.status_label.text() == "Checking..."

    service.check_callback(UpdateInfo("browser", "Browser", UpdateStatus.AVAILABLE))
    application.processEvents()
    assert row.status_label.text() == "Update available"
    assert row.action.text() == "Update"
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
    row = window._update_rows["browser"]
    row.set_info(UpdateInfo("browser", "Browser", UpdateStatus.AVAILABLE, "1.0", "2.0"))
    window._handle_update_status(UpdateInfo("browser", "Browser", UpdateStatus.UPDATED))
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
