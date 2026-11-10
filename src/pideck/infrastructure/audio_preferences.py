"""Persist the system default audio output, HDMI mode, and per-app PCM layouts."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

from pideck.application.ports.audio import AppPcmLayout, AudioError, HdmiAudioMode


@dataclass(frozen=True, slots=True)
class SavedAudioPreferences:
    """Validated contents of audio.yaml."""

    default: str | None
    hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND
    applications: Mapping[str, AppPcmLayout] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze the applications mapping."""
        object.__setattr__(self, "applications", MappingProxyType(dict(self.applications)))


class AudioPreferencesStore:
    """Load and save audio settings next to pideck.yaml."""

    def __init__(self, path: Path) -> None:
        """Create a repository for system audio preferences."""
        self.path = path

    def load(self) -> SavedAudioPreferences:
        """Load preferences; a missing file means surround mode and no output override."""
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
        applications = self._parse_applications(document.get("applications"))
        return SavedAudioPreferences(
            default.strip() if isinstance(default, str) else None,
            hdmi_mode,
            applications,
        )

    def save(
        self,
        *,
        default: str | None,
        hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND,
        applications: Mapping[str, AppPcmLayout] | None = None,
    ) -> None:
        """Atomically replace audio preferences."""
        if default is not None and (not isinstance(default, str) or not default.strip()):
            raise AudioError("Invalid audio output")
        if not isinstance(hdmi_mode, HdmiAudioMode):
            raise AudioError("Invalid HDMI audio mode")
        if applications is None:
            applications = {}
        parsed = self._validate_applications(applications)
        document: dict[str, object] = {
            "version": 1,
            "hdmi_mode": hdmi_mode.value,
        }
        if default is not None:
            document["default"] = default
        if parsed:
            document["applications"] = {
                app_id: layout.value for app_id, layout in sorted(parsed.items())
            }
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
        self.save(
            default=identifier,
            hdmi_mode=current.hdmi_mode,
            applications=current.applications,
        )

    def set_hdmi_mode(self, hdmi_mode: HdmiAudioMode) -> None:
        """Update the HDMI audio mode."""
        if not isinstance(hdmi_mode, HdmiAudioMode):
            raise AudioError("Invalid HDMI audio mode")
        current = self.load()
        self.save(
            default=current.default,
            hdmi_mode=hdmi_mode,
            applications=current.applications,
        )

    def set_application_layout(self, app_id: str, layout: AppPcmLayout | None) -> None:
        """Set or clear a per-application PCM layout override."""
        if not isinstance(app_id, str) or not app_id.strip():
            raise AudioError("Invalid application identifier")
        if layout is not None and not isinstance(layout, AppPcmLayout):
            raise AudioError("Invalid PCM layout")
        current = self.load()
        applications = dict(current.applications)
        key = app_id.strip()
        if layout is None:
            applications.pop(key, None)
        else:
            applications[key] = layout
        self.save(
            default=current.default,
            hdmi_mode=current.hdmi_mode,
            applications=applications,
        )

    @staticmethod
    def _parse_applications(raw: object) -> dict[str, AppPcmLayout]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise AudioError("Invalid saved audio settings")
        return AudioPreferencesStore._validate_applications(raw)

    @staticmethod
    def _validate_applications(raw: Mapping[object, object]) -> dict[str, AppPcmLayout]:
        applications: dict[str, AppPcmLayout] = {}
        for app_id, layout_raw in raw.items():
            if not isinstance(app_id, str) or not app_id.strip():
                raise AudioError("Invalid saved audio settings")
            if isinstance(layout_raw, AppPcmLayout):
                layout = layout_raw
            elif isinstance(layout_raw, str):
                try:
                    layout = AppPcmLayout(layout_raw.strip())
                except ValueError as error:
                    raise AudioError("Invalid saved audio settings") from error
            else:
                raise AudioError("Invalid saved audio settings")
            applications[app_id.strip()] = layout
        return applications
