"""X11 display discovery, guarded mode changes, and confirmed-mode persistence."""

import logging
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import threading
import time

import yaml

from pideck.application.ports.display import DisplayError, DisplayMode, DisplayOutput


_LOGGER = logging.getLogger(__name__)
_OUTPUT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_MODE = re.compile(r"[0-9]{2,5}x[0-9]{2,5}[ip]?\Z")
_RATE = re.compile(r"([0-9]+(?:\.[0-9]+)?)([+*]*)\Z")
_TIMEOUT = 10.0
_DEFAULT_BRIGHTNESS = 100
_MIN_BRIGHTNESS = 10
_MAX_BRIGHTNESS = 100


def _validate(output: str, mode: DisplayMode) -> None:
    rate = _RATE.fullmatch(mode.rate)
    if not _OUTPUT.fullmatch(output) or not _MODE.fullmatch(mode.resolution) or rate is None or rate.group(2):
        raise DisplayError("Invalid display output or mode")


def clamp_brightness(percent: int) -> int:
    """Clamp software brightness to the supported interactive range."""
    return max(_MIN_BRIGHTNESS, min(_MAX_BRIGHTNESS, int(percent)))


def _parse_outputs(text: str) -> tuple[DisplayOutput, ...]:
    """Parse active XRandR outputs while preserving supported mode/rate pairs."""
    outputs: list[DisplayOutput] = []
    name: str | None = None
    modes: list[DisplayMode] = []
    current: DisplayMode | None = None

    def finish() -> None:
        if name is not None and current is not None:
            outputs.append(DisplayOutput(name, current, tuple(dict.fromkeys(modes))))

    for line in text.splitlines():
        if line and not line[0].isspace():
            finish()
            fields = line.split()
            name = fields[0] if len(fields) >= 2 and fields[1] == "connected" and _OUTPUT.fullmatch(fields[0]) else None
            modes = []
            current = None
            continue
        if name is None:
            continue
        fields = line.split()
        if not fields or not _MODE.fullmatch(fields[0]):
            continue
        for field in fields[1:]:
            match = _RATE.fullmatch(field)
            if match is None:
                continue
            mode = DisplayMode(fields[0], match.group(1))
            modes.append(mode)
            if "*" in match.group(2):
                current = mode
    finish()
    return tuple(outputs)


class DisplayPreferences:
    """Persist confirmed output modes and software brightness per user."""

    def __init__(self, path: Path) -> None:
        """Create a repository for confirmed display settings."""
        self.path = path

    def _read_document(self) -> dict[str, object]:
        """Load the raw YAML document or an empty defaults-ready structure."""
        try:
            document = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"outputs": {}}
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise DisplayError(f"Unable to read display settings: {error}") from error
        if document is None:
            return {"outputs": {}}
        if not isinstance(document, dict):
            raise DisplayError("Invalid saved display settings")
        allowed = {"outputs", "brightness"}
        if not set(document) <= allowed or "outputs" not in document:
            raise DisplayError("Invalid saved display settings")
        if not isinstance(document["outputs"], dict):
            raise DisplayError("Invalid saved display settings")
        if "brightness" in document:
            brightness = document["brightness"]
            if not isinstance(brightness, int) or isinstance(brightness, bool):
                raise DisplayError("Invalid saved display settings")
            if not _MIN_BRIGHTNESS <= brightness <= _MAX_BRIGHTNESS:
                raise DisplayError("Invalid saved display settings")
        return document

    def _parse_outputs(self, raw_outputs: object) -> dict[str, DisplayMode]:
        """Validate the outputs mapping from a preferences document."""
        if not isinstance(raw_outputs, dict):
            raise DisplayError("Invalid saved display settings")
        result: dict[str, DisplayMode] = {}
        for output, value in raw_outputs.items():
            if not isinstance(output, str) or not isinstance(value, dict) or set(value) != {"resolution", "rate"}:
                raise DisplayError("Invalid saved display settings")
            resolution, rate = value["resolution"], value["rate"]
            if not isinstance(resolution, str) or not isinstance(rate, str):
                raise DisplayError("Invalid saved display settings")
            mode = DisplayMode(resolution, rate)
            _validate(output, mode)
            result[output] = mode
        return result

    def load(self) -> dict[str, DisplayMode]:
        """Load validated output modes; missing preferences mean no saved modes."""
        return self._parse_outputs(self._read_document()["outputs"])

    def load_brightness(self) -> int:
        """Load persisted brightness percent, defaulting to full brightness."""
        document = self._read_document()
        brightness = document.get("brightness", _DEFAULT_BRIGHTNESS)
        if not isinstance(brightness, int) or isinstance(brightness, bool):
            return _DEFAULT_BRIGHTNESS
        return clamp_brightness(brightness)

    def _write(self, outputs: dict[str, DisplayMode], brightness: int) -> None:
        """Atomically persist modes and brightness together."""
        document: dict[str, object] = {
            "outputs": {
                name: {"resolution": value.resolution, "rate": value.rate}
                for name, value in outputs.items()
            },
            "brightness": clamp_brightness(brightness),
        }
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as file:
                temporary = Path(file.name)
                yaml.safe_dump(document, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except (OSError, yaml.YAMLError) as error:
            raise DisplayError(f"Unable to save display settings: {error}") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, output: str, mode: DisplayMode) -> None:
        """Atomically save a user-confirmed display mode."""
        _validate(output, mode)
        values = self.load()
        values[output] = mode
        self._write(values, self.load_brightness())

    def save_brightness(self, percent: int) -> None:
        """Atomically save software brightness without dropping saved modes."""
        self._write(self.load(), clamp_brightness(percent))


class X11DisplayGateway:
    """Apply XRandR modes using a separate watchdog for safe rollback."""

    def __init__(self, preferences: DisplayPreferences) -> None:
        """Create a display adapter with injected per-user preferences."""
        self._preferences = preferences
        self._startup_ready = threading.Event()
        self._startup_ready.set()
        self._restore_error: str | None = None
        self._change_lock = threading.Lock()
        self._starting_change = False
        self._active_change: X11DisplayChange | None = None

    def _run(self, *arguments: str) -> str:
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if (
            session == "wayland"
            or (session != "x11" and os.environ.get("WAYLAND_DISPLAY"))
            or not os.environ.get("DISPLAY")
        ):
            raise DisplayError("Display settings require an X11 session")
        try:
            result = subprocess.run(
                ["xrandr", *arguments], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=10, check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DisplayError("XRandR is unavailable or timed out") from error
        if result.returncode:
            raise DisplayError(result.stderr.strip() or "XRandR rejected the request")
        return result.stdout

    def inspect(self) -> tuple[DisplayOutput, ...]:
        """List active outputs and their supported mode/rate combinations."""
        if not self._startup_ready.wait(timeout=30):
            raise DisplayError("Display settings are still being restored")
        if self._restore_error is not None:
            error = self._restore_error
            self._restore_error = None
            raise DisplayError(error)
        return self._inspect()

    def _inspect(self) -> tuple[DisplayOutput, ...]:
        outputs = _parse_outputs(self._run("--query"))
        if not outputs:
            raise DisplayError("No configurable active X11 displays were found")
        return outputs

    def _apply(self, output: str, mode: DisplayMode) -> None:
        _validate(output, mode)
        self._run("--output", output, "--mode", mode.resolution, "--rate", mode.rate)

    def _apply_brightness(self, percent: int, output_names: tuple[str, ...] | None = None) -> None:
        """Apply clamped software brightness to one or every connected output."""
        value = clamp_brightness(percent) / 100.0
        names = output_names if output_names is not None else tuple(output.name for output in self._inspect())
        for name in names:
            if not _OUTPUT.fullmatch(name):
                raise DisplayError("Invalid display output or mode")
            self._run("--output", name, "--brightness", f"{value:.2f}")

    def get_brightness(self) -> int:
        """Return the persisted software brightness percent."""
        return self._preferences.load_brightness()

    def set_brightness(self, percent: int) -> None:
        """Apply software brightness to connected outputs and persist it."""
        if not self._startup_ready.wait(timeout=30):
            raise DisplayError("Display settings are still being restored")
        clamped = clamp_brightness(percent)
        self._apply_brightness(clamped)
        self._preferences.save_brightness(clamped)

    def begin(self, output: str, mode: DisplayMode) -> "X11DisplayChange":
        """Apply an offered mode with an independent ten-second rollback."""
        _validate(output, mode)
        with self._change_lock:
            active = self._active_change
            if self._starting_change or (
                active is not None and (active.resolving or active.process_running())
            ):
                raise DisplayError("Another display change is still in progress")
            self._active_change = None
            self._starting_change = True
        try:
            matches = [item for item in self.inspect() if item.name == output]
            if not matches or mode not in matches[0].modes:
                raise DisplayError("The selected display mode is no longer available")
            previous = matches[0].current
            if mode == previous:
                raise DisplayError("The selected display mode is already active")
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "pideck.infrastructure.display_watchdog",
                     "--output", output, "--mode", previous.resolution, "--rate", previous.rate,
                     "--new-mode", mode.resolution, "--new-rate", mode.rate,
                     "--timeout", str(_TIMEOUT)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1, start_new_session=True,
                )
            except OSError as error:
                raise DisplayError("Unable to start the display rollback watchdog") from error
            change = X11DisplayChange(self, self._preferences, process, output, previous, mode)
            with self._change_lock:
                self._active_change = change
            try:
                response = change.read_result(25)
                if not response.startswith("READY "):
                    raise DisplayError(response or "The display rollback watchdog did not start")
                try:
                    change.deadline = float(response.removeprefix("READY "))
                except ValueError as error:
                    raise DisplayError("The display rollback watchdog returned an invalid deadline") from error
                if not time.monotonic() < change.deadline <= time.monotonic() + _TIMEOUT:
                    raise DisplayError("The display confirmation period expired")
            except DisplayError:
                if process.stdin is not None:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
                raise
            return change
        finally:
            with self._change_lock:
                self._starting_change = False

    def _release_change(self, change: "X11DisplayChange") -> None:
        with self._change_lock:
            if self._active_change is change and not change.process_running():
                self._active_change = None

    def restore_saved(self) -> None:
        """Restore saved modes and software brightness on active X11 outputs."""
        preferences = self._preferences.load()
        brightness = self._preferences.load_brightness()
        outputs = {output.name: output for output in self._inspect()}
        unavailable: list[str] = []
        for name, mode in preferences.items():
            output = outputs.get(name)
            if output is None or mode not in output.modes:
                _LOGGER.warning("Saved display mode is unavailable for output %s", name)
                unavailable.append(name)
                continue
            if output.current != mode:
                self._apply(name, mode)
        self._apply_brightness(brightness, tuple(outputs))
        if unavailable:
            raise DisplayError(f"Saved display modes are unavailable for: {', '.join(unavailable)}")

    def restore_saved_async(self) -> None:
        """Restore saved modes off the UI thread before allowing new changes."""
        if not self._preferences.path.exists():
            return
        self._startup_ready.clear()

        def restore() -> None:
            try:
                self.restore_saved()
            except DisplayError as error:
                _LOGGER.warning("Unable to restore display settings: %s", error)
                self._restore_error = f"Unable to restore display settings: {error}"
            finally:
                self._startup_ready.set()

        threading.Thread(target=restore, name="pideck-display-restore", daemon=True).start()


class X11DisplayChange:
    """Own a single watchdog-protected display transaction."""

    def __init__(
        self, gateway: X11DisplayGateway, preferences: DisplayPreferences,
        process: subprocess.Popen[str], output: str, previous: DisplayMode,
        selected: DisplayMode,
    ) -> None:
        """Capture the original mode and running rollback process."""
        self._gateway = gateway
        self._preferences = preferences
        self._process = process
        self._output = output
        self._previous = previous
        self._selected = selected
        self.deadline = 0.0
        self._finished = False
        self.resolving = False

    def process_running(self) -> bool:
        """Return whether the rollback watchdog is still running."""
        return self._process.poll() is None

    def read_result(self, timeout: float) -> str:
        """Read a bounded response from the watchdog."""
        stream = self._process.stdout
        if stream is None or not select.select([stream], [], [], timeout)[0]:
            raise DisplayError("Display rollback watchdog did not respond")
        return stream.readline().strip()

    def _finish(self, command: str) -> str:
        if self._finished:
            raise DisplayError("Display change is already finished")
        self._finished = True
        stream = self._process.stdin
        if stream is not None:
            try:
                stream.write(command + "\n")
                stream.flush()
            except BrokenPipeError:
                pass
            finally:
                try:
                    stream.close()
                except BrokenPipeError:
                    pass
        try:
            result = self.read_result(12)
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired as error:
            raise DisplayError("Display rollback watchdog did not exit") from error
        finally:
            if self._process.stdout is not None:
                self._process.stdout.close()
        return result

    def confirm(self) -> None:
        """Confirm a timely change and persist it, restoring on save failure."""
        self.resolving = True
        try:
            result = self._finish("confirm")
            if result != "CONFIRMED":
                raise DisplayError(result or "Display confirmation expired")
            try:
                self._preferences.save(self._output, self._selected)
            except DisplayError as error:
                try:
                    self._gateway._apply(self._output, self._previous)
                except DisplayError as rollback_error:
                    raise DisplayError(f"{error}; rollback failed: {rollback_error}") from error
                raise
            try:
                self._gateway._apply_brightness(self._preferences.load_brightness(), (self._output,))
            except DisplayError:
                _LOGGER.warning("Unable to re-apply display brightness after mode confirm", exc_info=True)
        finally:
            self.resolving = False
            self._gateway._release_change(self)

    def cancel(self) -> None:
        """Ask the watchdog to revert and report any failure."""
        self.resolving = True
        try:
            result = self._finish("rollback")
            if result != "REVERTED":
                raise DisplayError(result or "Unable to restore the previous display mode")
            try:
                self._gateway._apply_brightness(self._preferences.load_brightness(), (self._output,))
            except DisplayError:
                _LOGGER.warning("Unable to re-apply display brightness after mode revert", exc_info=True)
        finally:
            self.resolving = False
            self._gateway._release_change(self)
