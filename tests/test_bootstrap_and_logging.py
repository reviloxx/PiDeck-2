"""Tests for logging setup and dependency injection."""

from pathlib import Path
from dataclasses import replace

from pideck.bootstrap import (
    _apply_window_modes,
    _migrate_catalog_launch_arguments,
    build_dependencies,
)
from pideck.domain.catalog import CatalogEntry
from pideck.domain.configuration import ApplicationDefinition, WindowMode
from pideck.infrastructure.config.defaults import default_configuration
from pideck.infrastructure.config.repository import FileConfigurationRepository
from pideck.infrastructure.config.window_modes import load_window_modes
from pideck.domain.errors import ConfigurationError
import pytest
from pideck.infrastructure.logging import configure_logging


def test_bootstrap_builds_dependencies_without_qt(tmp_path: Path) -> None:
    """The composition root loads defaults without requiring a display server."""
    dependencies = build_dependencies(tmp_path / "missing.yaml", log_level="WARNING")

    assert dependencies.configuration.home.theme == "default"
    assert dependencies.theme_repository.get("default").identifier == "default"


def test_window_modes_do_not_change_saved_configuration(tmp_path: Path) -> None:
    """Window policies do not change the YAML schema used by earlier releases."""
    saved = replace(
        default_configuration(),
        applications=(
            ApplicationDefinition("browser", "Browser", "browser"),
            ApplicationDefinition("retroarch", "RetroArch", "retroarch"),
            ApplicationDefinition("custom", "Custom", "custom"),
        ),
    )
    result = _apply_window_modes(saved, {"browser": WindowMode.MAXIMIZED, "retroarch": WindowMode.NORMAL})
    assert [app.window_mode for app in result.applications] == [
        WindowMode.MAXIMIZED, WindowMode.NORMAL, None,
    ]
    assert saved.applications[0].window_mode is None
    repository = FileConfigurationRepository(tmp_path / "pideck.yaml")
    repository.save(result)
    assert "window_mode" not in repository.path.read_text()
    assert repository.load().applications[0].window_mode is None


def test_window_policy_validation(tmp_path: Path) -> None:
    """Only supported named window modes are accepted."""
    path = tmp_path / "window-modes.yaml"
    path.write_text("browser: maximized\nretroarch: normal\nnomachine: fullscreen\n")
    assert load_window_modes(path) == {
        "browser": WindowMode.MAXIMIZED,
        "retroarch": WindowMode.NORMAL,
        "nomachine": WindowMode.FULLSCREEN,
    }
    path.write_text("browser: invalid\n")
    with pytest.raises(ConfigurationError, match="Invalid window mode"):
        load_window_modes(path)


def test_bootstrap_reads_separate_window_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Local window policies are applied without modifying the saved launcher YAML."""
    monkeypatch.delenv("PIDECK_IMAGE", raising=False)
    config = replace(
        default_configuration(),
        applications=(ApplicationDefinition("browser", "Browser", "browser"),),
    )
    repository = FileConfigurationRepository(tmp_path / "pideck.yaml")
    repository.save(config)
    original = repository.path.read_bytes()
    (tmp_path / "window-modes.yaml").write_text("browser: maximized\n")
    result = build_dependencies(repository.path, log_level="WARNING")
    assert result.configuration.applications[0].window_mode is WindowMode.MAXIMIZED
    assert repository.path.read_bytes() == original


def test_migrate_catalog_launch_arguments_adds_start_maximized() -> None:
    """Existing Brave installs pick up catalog --start-maximized after OTA."""
    saved = replace(
        default_configuration(),
        applications=(
            ApplicationDefinition(
                "brave",
                "Brave",
                "brave-browser",
                arguments=(
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--password-store=basic",
                    "--ignore-gpu-blocklist",
                ),
            ),
            ApplicationDefinition("youtube", "YouTube", "flatpak", arguments=("run", "com.ktechpit.red")),
        ),
    )
    catalog = (
        CatalogEntry(
            application=ApplicationDefinition(
                "brave",
                "Brave",
                "brave-browser",
                arguments=(
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--password-store=basic",
                    "--start-maximized",
                ),
            ),
        ),
    )
    migrated = _migrate_catalog_launch_arguments(saved, catalog, "amd64")
    brave = next(app for app in migrated.applications if app.identifier == "brave")
    assert "--start-maximized" in brave.arguments
    youtube = next(app for app in migrated.applications if app.identifier == "youtube")
    assert youtube.arguments == ("run", "com.ktechpit.red")


def test_migrate_catalog_launch_arguments_is_noop_when_present() -> None:
    """Already-migrated browser tiles are left unchanged."""
    args = (
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--start-maximized",
    )
    saved = replace(
        default_configuration(),
        applications=(ApplicationDefinition("brave", "Brave", "brave-browser", arguments=args),),
    )
    catalog = (
        CatalogEntry(
            application=ApplicationDefinition(
                "brave", "Brave", "brave-browser", arguments=args + ("--ignore-gpu-blocklist",),
            ),
        ),
    )
    assert _migrate_catalog_launch_arguments(saved, catalog, "amd64") is saved


def test_logging_redacts_sensitive_values(tmp_path: Path) -> None:
    """Common secret-shaped log values are not written in clear text."""
    log_path = tmp_path / "pideck.log"
    logger = configure_logging(level="INFO", log_file=log_path, console=False)

    logger.info("connecting with token=super-secret password=hunter2")
    for handler in logger.handlers:
        handler.flush()
        handler.close()

    output = log_path.read_text(encoding="utf-8")
    assert "super-secret" not in output
    assert "hunter2" not in output
    assert output.count("[REDACTED]") == 2
