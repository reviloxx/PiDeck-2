"""Read metadata embedded in the currently running PiDeck application build."""

from datetime import datetime
import json
import logging
from pathlib import Path


_LOGGER = logging.getLogger(__name__)


def installed_build_date() -> str | None:
    """Return the recorded UTC build time, or None when no metadata is available."""
    path = Path(__file__).resolve().parent.parent / "_build_info.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _LOGGER.warning("Unable to read PiDeck build metadata: %s", error)
        return None
    value = data.get("built_at") if isinstance(data, dict) else None
    if not isinstance(value, str):
        _LOGGER.warning("Invalid PiDeck build metadata")
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        _LOGGER.warning("Invalid PiDeck build timestamp")
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        _LOGGER.warning("PiDeck build timestamp lacks a timezone")
        return None
    return value
