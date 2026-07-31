"""Centralized logging configuration for PiDeck."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re

_SENSITIVE_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)[^\s,;]+"
)


class RedactingFormatter(logging.Formatter):
    """Remove common secret-shaped values from rendered log messages."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a record and redact sensitive key/value pairs."""
        return _SENSITIVE_PATTERN.sub(r"\1\2[REDACTED]", super().format(record))


def configure_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    console: bool = True,
) -> logging.Logger:
    """Configure the PiDeck logger and return its logger instance."""
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unknown logging level: {level}")

    logger = logging.getLogger("pideck")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(numeric_level)
    logger.propagate = False
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger
