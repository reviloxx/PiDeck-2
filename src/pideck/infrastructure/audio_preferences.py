"""Persist system default and per-application audio output selections."""

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
    applications: dict[str, str]


class AudioPreferencesStore:
    """Load and save audio routing preferences next to pideck.yaml."""

    def __init__(self, path: Path) -> None:
        """Create a repository for system and per-application audio outputs."""
        self.path = path

    def load(self) -> SavedAudioPreferences:
        """Load preferences; a missing file means no overrides."""
        try:
            document = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return SavedAudioPreferences(None, {})
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise AudioError(f"Unable to read audio settings: {error}") from error
        if document is None:
            return SavedAudioPreferences(None, {})
        if not isinstance(document, dict):
            raise AudioError("Invalid saved audio settings")
        version = document.get("version")
        if version != 1:
            raise AudioError("Invalid saved audio settings")
        default = document.get("default")
        if default is not None and (not isinstance(default, str) or not default.strip()):
            raise AudioError("Invalid saved audio settings")
        applications_raw = document.get("applications", {})
        if applications_raw is None:
            applications_raw = {}
        if not isinstance(applications_raw, dict):
            raise AudioError("Invalid saved audio settings")
        applications: dict[str, str] = {}
        for key, value in applications_raw.items():
            if not isinstance(key, str) or not key.strip():
                raise AudioError("Invalid saved audio settings")
            if not isinstance(value, str) or not value.strip():
                raise AudioError("Invalid saved audio settings")
            applications[key] = value
        return SavedAudioPreferences(default.strip() if isinstance(default, str) else None, applications)

    def save(self, *, default: str | None, applications: dict[str, str]) -> None:
        """Atomically replace audio routing preferences."""
        if default is not None and (not isinstance(default, str) or not default.strip()):
            raise AudioError("Invalid audio output")
        cleaned: dict[str, str] = {}
        for key, value in applications.items():
            if not isinstance(key, str) or not key.strip():
                raise AudioError("Invalid application identifier")
            if not isinstance(value, str) or not value.strip():
                raise AudioError("Invalid audio output")
            cleaned[key] = value
        document: dict[str, object] = {"version": 1, "applications": dict(sorted(cleaned.items()))}
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
        """Update only the system default output token."""
        current = self.load()
        self.save(default=identifier, applications=current.applications)

    def set_application(self, application_id: str, identifier: str | None) -> None:
        """Set or clear one application's output override."""
        if not isinstance(application_id, str) or not application_id.strip():
            raise AudioError("Invalid application identifier")
        current = self.load()
        applications = dict(current.applications)
        if identifier is None:
            applications.pop(application_id, None)
        else:
            applications[application_id] = identifier
        self.save(default=current.default, applications=applications)

    def resolve(self, application_id: str) -> str | None:
        """Return the effective output token for an application, if any."""
        try:
            current = self.load()
        except AudioError:
            return None
        return current.applications.get(application_id) or current.default
