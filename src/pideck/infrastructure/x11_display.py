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


def _validate(output: str, mode: DisplayMode) -> None:
    rate = _RATE.fullmatch(mode.rate)
    if not _OUTPUT.fullmatch(output) or not _MODE.fullmatch(mode.resolution) or rate is None or rate.group(2):
        raise DisplayError("Invalid display output or mode")


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
    """Persist confirmed output modes in a separate per-user YAML file."""

    def __init__(self, path: Path) -> None:
        """Create a repository for confirmed display settings."""
        self.path = path

    def load(self) -> dict[str, DisplayMode]:
        """Load validated settings; missing preferences mean no saved modes."""
        try:
            document = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise DisplayError(f"Unable to read display settings: {error}") from error
        if not isinstance(document, dict) or set(document) != {"outputs"} or not isinstance(document["outputs"], dict):
            raise DisplayError("Invalid saved display settings")
        result: dict[str, DisplayMode] = {}
        for output, value in document["outputs"].items():
            if not isinstance(output, str) or not isinstance(value, dict) or set(value) != {"resolution", "rate"}:
                raise DisplayError("Invalid saved display settings")
            resolution, rate = value["resolution"], value["rate"]
            if not isinstance(resolution, str) or not isinstance(rate, str):
                raise DisplayError("Invalid saved display settings")
            mode = DisplayMode(resolution, rate)
            _validate(output, mode)
            result[output] = mode
        return result

    def save(self, output: str, mode: DisplayMode) -> None:
        """Atomically save a user-confirmed display mode."""
        _validate(output, mode)
        values = self.load()
        values[output] = mode
        document = {"outputs": {
            name: {"resolution": value.resolution, "rate": value.rate}
            for name, value in values.items()
        }}
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
        """Restore only saved modes still offered by their active X11 outputs."""
        preferences = self._preferences.load()
        if not preferences:
            return
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
        finally:
            self.resolving = False
            self._gateway._release_change(self)
