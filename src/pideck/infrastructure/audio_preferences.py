"""Persist the system default audio output selection."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from pideck.application.ports.audio import AudioError


@dataclass(frozen=True, slots=True)
class SavedAudioPreferences:
    """Validated contents of audio.yaml."""

    default: str | None


class AudioPreferencesStore:
    """Load and save the system default audio output next to pideck.yaml."""

    def __init__(self, path: Path) -> None:
        """Create a repository for the system default audio output."""
        self.path = path

    def load(self) -> SavedAudioPreferences:
        """Load preferences; a missing file means no override."""
        try:
            document = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return SavedAudioPreferences(None)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise AudioError(f"Unable to read audio settings: {error}") from error
        if document is None:
            return SavedAudioPreferences(None)
        if not isinstance(document, dict):
            raise AudioError("Invalid saved audio settings")
        version = document.get("version")
        if version != 1:
            raise AudioError("Invalid saved audio settings")
        default = document.get("default")
        if default is not None and (not isinstance(default, str) or not default.strip()):
            raise AudioError("Invalid saved audio settings")
        return SavedAudioPreferences(default.strip() if isinstance(default, str) else None)

    def save(self, *, default: str | None) -> None:
        """Atomically replace audio routing preferences."""
        if default is not None and (not isinstance(default, str) or not default.strip()):
            raise AudioError("Invalid audio output")
        document: dict[str, object] = {"version": 1}
        if default is not None:
            document["default"] = default
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary = Path(file.name)
                yaml.safe_dump(document, file, sort_keys=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except (OSError, yaml.YAMLError) as error:
            raise AudioError(f"Unable to save audio settings: {error}") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def set_default(self, identifier: str | None) -> None:
        """Update the system default output token."""
        self.save(default=identifier)
