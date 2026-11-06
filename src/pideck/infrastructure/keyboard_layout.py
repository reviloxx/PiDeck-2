"""Per-user keyboard layout preference and its X11 applier.

The preference lives in its own ``keyboard.yaml`` because ``pideck.yaml`` and
``input.yaml`` are validated strictly and must remain readable by older
releases after a rollback.
"""

import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from collections.abc import Callable

import yaml

from pideck.domain.keyboard_layout import DEFAULT_KEYBOARD_LAYOUT, KeyboardLayout


_LOGGER = logging.getLogger(__name__)


class KeyboardPreferences:
    """Load and atomically save the selected keyboard layout."""

    def __init__(self, path: Path) -> None:
        """Create a repository for one user's keyboard preference."""
        self.path = path

    def load(self) -> KeyboardLayout:
        """Return the saved layout, or the German default when missing or invalid."""
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return DEFAULT_KEYBOARD_LAYOUT
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            _LOGGER.warning("Unable to read keyboard settings path=%s error=%s", self.path, error)
            return DEFAULT_KEYBOARD_LAYOUT
        if isinstance(data, dict) and data.get("version") == 1:
            try:
                return KeyboardLayout(data.get("layout"))
            except ValueError:
                pass
        _LOGGER.warning("Invalid keyboard settings path=%s", self.path)
        return DEFAULT_KEYBOARD_LAYOUT

    def save(self, layout: KeyboardLayout) -> None:
        """Atomically persist ``layout``; raises ``OSError`` when the file cannot be written."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as file:
                temporary = Path(file.name)
                yaml.safe_dump({"version": 1, "layout": KeyboardLayout(layout).value}, file, sort_keys=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class X11KeyboardLayout:
    """Apply a layout to the running X server with ``setxkbmap``."""

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Create an applier; ``runner`` and ``which`` are injectable for tests."""
        self._runner = runner
        self._which = which

    def apply(self, layout: KeyboardLayout) -> bool:
        """Switch every keyboard in the X session to ``layout``."""
        executable = self._which("setxkbmap")
        if executable is None:
            _LOGGER.warning("setxkbmap is unavailable; keyboard layout %s not applied", layout.value)
            return False
        try:
            result = self._runner(
                [executable, "-layout", KeyboardLayout(layout).value],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            _LOGGER.warning("Unable to apply keyboard layout %s: %s", layout.value, error)
            return False
        if result.returncode:
            _LOGGER.warning("setxkbmap rejected layout %s: %s", layout.value, result.stderr.strip())
            return False
        _LOGGER.info("Applied keyboard layout %s", layout.value)
        return True
