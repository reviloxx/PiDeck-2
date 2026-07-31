"""Tests for logging setup and dependency injection."""

from pathlib import Path

from pideck.bootstrap import build_dependencies
from pideck.infrastructure.logging import configure_logging


def test_bootstrap_builds_dependencies_without_qt(tmp_path: Path) -> None:
    """The composition root loads defaults without requiring a display server."""
    dependencies = build_dependencies(tmp_path / "missing.yaml", log_level="WARNING")

    assert dependencies.configuration.home.theme == "default"
    assert dependencies.theme_repository.get("default").identifier == "default"


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
