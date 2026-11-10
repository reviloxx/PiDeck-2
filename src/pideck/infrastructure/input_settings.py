"""Atomic per-user storage for HDMI-CEC preferences."""

import os
from pathlib import Path
import tempfile

import yaml

from pideck.domain.input_settings import CecSettings

_BASE_KEYS = frozenset({"version", "enabled", "auto_connect", "hdmi_port", "switch_input"})
_WAKE_KEY = "wake_tv"


class InputSettingsError(Exception):
    """Report invalid or inaccessible input preferences."""


class InputPreferences:
    """Store CEC settings separately from the launcher's strict main schema."""

    def __init__(self, path: Path) -> None:
        """Create a repository for one user's input settings."""
        self.path = path

    def load(self) -> CecSettings:
        """Load preferences, enabling CEC once when no preferences exist."""
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            settings = CecSettings()
            self.save(settings)
            return settings
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise InputSettingsError(f"Unable to read input settings: {error}") from error
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise InputSettingsError("Invalid input settings")
        keys = set(data)
        if keys != _BASE_KEYS and keys != _BASE_KEYS | {_WAKE_KEY}:
            raise InputSettingsError("Invalid input settings")
        wake_tv = data.get(_WAKE_KEY, True)
        try:
            return CecSettings(
                enabled=data["enabled"],
                auto_connect=data["auto_connect"],
                hdmi_port=data["hdmi_port"],
                switch_input=data["switch_input"],
                wake_tv=wake_tv,
            )
        except ValueError as error:
            raise InputSettingsError(f"Invalid input settings: {error}") from error

    def save(self, settings: CecSettings) -> None:
        """Atomically persist validated preferences."""
        document = {
            "version": 1,
            "enabled": settings.enabled,
            "auto_connect": settings.auto_connect,
            "hdmi_port": settings.hdmi_port,
            "switch_input": settings.switch_input,
            "wake_tv": settings.wake_tv,
        }
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as file:
                temporary = Path(file.name)
                yaml.safe_dump(document, file, sort_keys=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except (OSError, yaml.YAMLError) as error:
            raise InputSettingsError(f"Unable to save input settings: {error}") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
