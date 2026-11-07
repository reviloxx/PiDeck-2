"""Optional libCEC client adapter for HDMI remote navigation."""

import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from typing import IO

from pideck.application.ports.input import InputAction, InputEvent, InputSink
from pideck.domain.errors import ProcessError
from pideck.domain.input_settings import CecSettings
from pideck.infrastructure.cec_kernel import CecKernelError, prepare_kernel_cec

_LOGGER = logging.getLogger(__name__)
_KEY_PRESS = re.compile(r"\bkey pressed: .* \(([0-9a-f]+), (\d+)\)$")
_KEY_RELEASE = re.compile(r"\bkey released: .* \(([0-9a-f]+)\) D:\d+ms$")
_KEY_IDLE = re.compile(r"\bKey .*: idle \(duration:\d+\) \(ff\) timeout:")
_ACTIONS = {
    0x00: InputAction.ACTIVATE,
    0x01: InputAction.UP,
    0x02: InputAction.DOWN,
    0x03: InputAction.LEFT,
    0x04: InputAction.RIGHT,
    0x09: InputAction.MENU,
    0x0D: InputAction.MENU,
    0x44: InputAction.PLAY,
    0x45: InputAction.STOP,
    0x46: InputAction.PAUSE,
    0x48: InputAction.REWIND,
    0x49: InputAction.FAST_FORWARD,
    0x60: InputAction.PLAY,
    0x61: InputAction.PLAY_PAUSE,
    0x91: InputAction.BACK,  # Samsung Anynet+ Return / libCEC AN_RETURN
}
_DIRECTIONS = {InputAction.UP, InputAction.DOWN, InputAction.LEFT, InputAction.RIGHT}


class CecClientAdapter:
    """Own a cec-client process only while the launcher may receive input."""

    def __init__(
        self, retry_interval: float = 3.0, *, settings: CecSettings | None = None,
        status_changed: Callable[[str], None] | None = None,
    ) -> None:
        """Create an adapter with bounded retry intervals for missing hardware."""
        self._retry_interval = retry_interval
        self._condition = threading.Condition()
        self._process: subprocess.Popen[str] | None = None
        self._sink: InputSink | None = None
        self._thread: threading.Thread | None = None
        self._preparing = False
        self._suspended = True
        self._closed = False
        self._generation = 0
        self._settings = settings
        self._desired = settings is None or (settings.enabled and settings.auto_connect)
        self._status_changed = status_changed
        self._status = "Disconnected"
        self._stop_lock = threading.Lock()

    @property
    def status(self) -> str:
        """Return the latest connection status or error."""
        with self._condition:
            return self._status

    def _set_status(self, status: str) -> None:
        """Publish a changed status to the presentation bridge."""
        with self._condition:
            if self._status == status:
                return
            self._status = status
        if self._status_changed is not None:
            self._status_changed(status)

    def configure(self, settings: CecSettings) -> None:
        """Apply saved preferences and reconnect without blocking the caller."""
        with self._condition:
            previous = self._settings
            self._settings = settings
            if not settings.enabled:
                self._desired = False
            elif previous is None or not previous.enabled:
                self._desired = settings.auto_connect
            elif settings.auto_connect and not previous.auto_connect:
                self._desired = True
            elif not settings.auto_connect and self._process is None:
                self._desired = False
            reconnect = previous != settings
            process = self._process if reconnect else None
            if reconnect:
                self._generation += 1
            self._condition.notify_all()
        if process is not None:
            self._stop_async(process)
        elif not self._desired:
            self._set_status("Disconnected")

    def connect(self) -> None:
        """Request a connection, including when auto-connect is disabled."""
        with self._condition:
            if self._settings is not None and not self._settings.enabled:
                self._set_status("Enable HDMI-CEC before connecting")
                return
            self._desired = True
            self._condition.notify_all()

    def disconnect(self) -> None:
        """Disconnect until another explicit connection or launcher restart."""
        with self._condition:
            self._desired = False
            self._generation += 1
            process = self._process
            self._condition.notify_all()
        if process is not None:
            self._stop_async(process)
        else:
            self._set_status("Disconnected")

    def _stop_async(self, process: subprocess.Popen[str]) -> None:
        """Stop an obsolete process without blocking Qt."""
        def stop() -> None:
            try:
                self._stop_process(process)
            except ProcessError as error:
                _LOGGER.error("Unable to stop CEC client: %s", error)
                self._set_status(str(error))

        threading.Thread(target=stop, name="pideck-cec-stop", daemon=True).start()

    @property
    def generation(self) -> int:
        """Return the generation used to reject queued events after handoff."""
        with self._condition:
            return self._generation

    def accepts(self, generation: int) -> bool:
        """Check whether an event still belongs to the active launcher session."""
        with self._condition:
            return not self._closed and not self._suspended and generation == self._generation

    def start(self, sink: InputSink) -> None:
        """Start CEC discovery and deliver events to the supplied sink."""
        with self._condition:
            if self._thread is not None or self._closed:
                raise RuntimeError("CEC adapter has already been started or closed")
            self._sink = sink
            self._suspended = False
            self._thread = threading.Thread(target=self._run, name="pideck-cec", daemon=True)
            self._thread.start()

    def suspend(self) -> None:
        """Release the adapter and wait for the client process to exit."""
        with self._condition:
            self._suspended = True
            self._generation += 1
            self._condition.notify_all()
            if not self._condition.wait_for(lambda: not self._preparing, timeout=5):
                raise ProcessError("CEC adapter preparation did not stop")
            process = self._process
        if process is not None:
            self._stop_process(process)

    def resume(self) -> None:
        """Allow the worker to rediscover CEC after an application exits."""
        with self._condition:
            if not self._closed:
                if self._process is not None and self._process.poll() is None:
                    _LOGGER.error("Cannot resume CEC while its previous client is still running")
                    return
                self._suspended = False
                self._condition.notify_all()

    def close(self) -> None:
        """Stop the client permanently and release its resources."""
        with self._condition:
            self._closed = True
            self._suspended = True
            self._generation += 1
            process = self._process
            self._condition.notify_all()
        if process is not None:
            self._stop_process(process)
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                _LOGGER.error("CEC worker did not stop")

    def _stop_process(self, process: subprocess.Popen[str]) -> None:
        """Stop the client before allowing another process to own the adapter."""
        with self._stop_lock:
            self._stop_process_locked(process)

    @staticmethod
    def _stop_process_locked(process: subprocess.Popen[str]) -> None:
        """Terminate one child with a bounded wait."""
        if process.poll() is not None:
            return
        try:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _LOGGER.warning("CEC client did not exit after SIGINT; terminating it")
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as error:
                raise ProcessError("CEC client could not be stopped") from error
        except ProcessLookupError:
            process.wait()
        except OSError as error:
            raise ProcessError(f"Unable to stop CEC client: {error}") from error

    def _run(self) -> None:
        """Run the client, retrying discovery without blocking the Qt thread."""
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._closed or (not self._suspended and self._desired)
                )
                if self._closed:
                    return
                generation = self._generation
                settings = self._settings
                self._preparing = True
                self._set_status("Connecting")
            try:
                port = prepare_kernel_cec(settings) if settings is not None else None
                preparation_error: CecKernelError | None = None
            except CecKernelError as error:
                port = None
                preparation_error = error
            with self._condition:
                self._preparing = False
                self._condition.notify_all()
                if self._closed:
                    return
                if self._suspended or not self._desired or generation != self._generation:
                    continue
                if preparation_error is not None:
                    message = f"CEC unavailable: {preparation_error}"
                    _LOGGER.warning("%s", message)
                    self._set_status(message)
                    if settings is not None and not settings.auto_connect:
                        self._desired = False
                    else:
                        self._condition.wait(timeout=self._retry_interval)
                    continue
                try:
                    command = ["cec-client", "-d", "31", "-t", "p", "-o", "PiDeck"]
                    if port is not None:
                        command.append(port)
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env={**os.environ, "LC_ALL": "C"},
                    )
                except OSError as error:
                    message = f"CEC unavailable: {error}"
                    _LOGGER.warning("%s", message)
                    self._set_status(message)
                    if settings is not None and not settings.auto_connect:
                        self._desired = False
                    else:
                        self._condition.wait(timeout=self._retry_interval)
                    continue
                self._process = process
            try:
                if process.stdout is not None:
                    self._read_output(process.stdout, process, generation)
                if process.poll() is None:
                    self._stop_process(process)
                return_code = process.wait()
                with self._condition:
                    expected = self._closed or self._suspended or not self._desired or generation != self._generation
                if not expected:
                    _LOGGER.warning("CEC client exited with status %s; retrying", return_code)
                    self._set_status(f"CEC client exited with status {return_code}")
                    with self._condition:
                        if self._settings is not None and not self._settings.auto_connect:
                            self._desired = False
            except (OSError, ValueError, ProcessError):
                _LOGGER.exception("CEC client reader failed")
                if process.poll() is None:
                    try:
                        self._stop_process(process)
                    except ProcessError:
                        _LOGGER.exception("Unable to stop failed CEC client")
                        with self._condition:
                            self._suspended = True
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
                with self._condition:
                    if self._process is process and process.poll() is not None:
                        self._process = None
                    if not self._desired and not self._closed and (
                        self._suspended or generation != self._generation
                    ):
                        self._set_status("Disconnected")
                    if not self._closed and not self._suspended and self._desired and generation == self._generation:
                        self._condition.wait(timeout=self._retry_interval)

    def _read_output(
        self, output: IO[str], process: subprocess.Popen[str], generation: int,
    ) -> None:
        """Translate libCEC's delivered-key logs, ignoring its duplicate raw logs."""
        held: dict[int, float] = {}
        connected = False
        for line in output:
            line = line.strip()
            if self._settings is not None and not connected and line == "waiting for input":
                with self._condition:
                    active = (
                        self._process is process and not self._suspended
                        and self._desired and generation == self._generation
                    )
                    if active:
                        connected = True
                        self._set_status("Connected")
                        if self._settings.switch_input and process.stdin is not None:
                            process.stdin.write("as\n")
                            process.stdin.flush()
                continue
            if _KEY_IDLE.search(line):
                held.clear()
                continue
            release = _KEY_RELEASE.search(line)
            if release:
                held.pop(int(release.group(1), 16), None)
                continue
            match = _KEY_PRESS.search(line)
            if not match:
                if "ERROR:" in line or "unable to open" in line or "FAILED" in line:
                    _LOGGER.warning("CEC client: %s", line)
                    with self._condition:
                        if self._process is process and generation == self._generation:
                            self._set_status(f"CEC error: {line}")
                continue
            code = int(match.group(1), 16)
            action = _ACTIONS.get(code)
            if action is None:
                continue
            now = time.monotonic()
            previous = held.get(code)
            if previous is not None and (action not in _DIRECTIONS or now - previous < 0.18):
                continue
            held[code] = now
            with self._condition:
                if (
                    self._closed or self._suspended or self._process is not process
                    or self._generation != generation
                ):
                    continue
                sink = self._sink
                if sink is not None:
                    sink.handle_input(InputEvent(action, "cec"))
