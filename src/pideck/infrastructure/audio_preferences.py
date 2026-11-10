"""Persist the system default audio output and HDMI mode."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from pideck.application.ports.audio import AudioError, HdmiAudioMode


@dataclass(frozen=True, slots=True)
class SavedAudioPreferences:
    """Validated contents of audio.yaml."""

    default: str | None
    hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND


class AudioPreferencesStore:
    """Load and save audio settings next to pideck.yaml."""

    def __init__(self, path: Path) -> None:
        """Create a repository for system audio preferences."""
        self.path = path

    def load(self) -> SavedAudioPreferences:
        """Load preferences; a missing file means surround mode and no output override.

        An obsolete ``applications`` key from older releases is ignored.
        """
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
        mode_raw = document.get("hdmi_mode", HdmiAudioMode.SURROUND.value)
        if not isinstance(mode_raw, str):
            raise AudioError("Invalid saved audio settings")
        try:
            hdmi_mode = HdmiAudioMode(mode_raw.strip())
        except ValueError as error:
            raise AudioError("Invalid saved audio settings") from error
        return SavedAudioPreferences(
            default.strip() if isinstance(default, str) else None,
            hdmi_mode,
        )

    def save(
        self,
        *,
        default: str | None,
        hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND,
    ) -> None:
        """Atomically replace audio preferences (without per-app layouts)."""
        if default is not None and (not isinstance(default, str) or not default.strip()):
            raise AudioError("Invalid audio output")
        if not isinstance(hdmi_mode, HdmiAudioMode):
            raise AudioError("Invalid HDMI audio mode")
        document: dict[str, object] = {
            "version": 1,
            "hdmi_mode": hdmi_mode.value,
        }
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
        current = self.load()
        self.save(default=identifier, hdmi_mode=current.hdmi_mode)

    def set_hdmi_mode(self, hdmi_mode: HdmiAudioMode) -> None:
        """Update the HDMI audio mode."""
        if not isinstance(hdmi_mode, HdmiAudioMode):
            raise AudioError("Invalid HDMI audio mode")
        current = self.load()
        self.save(default=current.default, hdmi_mode=hdmi_mode)
