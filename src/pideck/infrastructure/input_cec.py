"""Optional libCEC client adapter for HDMI remote navigation."""

import logging
import os
import re
import signal
import subprocess
import threading
import time
from typing import IO

from pideck.application.ports.input import InputAction, InputEvent, InputSink
from pideck.domain.errors import ProcessError

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
    0x09: InputAction.HOME,
    0x0D: InputAction.BACK,
}
_DIRECTIONS = {InputAction.UP, InputAction.DOWN, InputAction.LEFT, InputAction.RIGHT}


class CecClientAdapter:
    """Own a cec-client process only while the launcher may receive input."""

    def __init__(self, retry_interval: float = 3.0) -> None:
        """Create an adapter with bounded retry intervals for missing hardware."""
        self._retry_interval = retry_interval
        self._condition = threading.Condition()
        self._process: subprocess.Popen[str] | None = None
        self._sink: InputSink | None = None
        self._thread: threading.Thread | None = None
        self._suspended = True
        self._closed = False
        self._generation = 0

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
            process = self._process
            self._condition.notify_all()
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

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        """Stop the client before allowing another process to own the adapter."""
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
                self._condition.wait_for(lambda: self._closed or not self._suspended)
                if self._closed:
                    return
                try:
                    process = subprocess.Popen(
                        ["cec-client", "-d", "31", "-t", "p", "-o", "PiDeck"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env={**os.environ, "LC_ALL": "C"},
                    )
                except OSError as error:
                    _LOGGER.warning("CEC client unavailable: %s", error)
                    self._condition.wait(timeout=self._retry_interval)
                    continue
                self._process = process
                generation = self._generation
            try:
                if process.stdout is not None:
                    self._read_output(process.stdout, process, generation)
                if process.poll() is None:
                    self._stop_process(process)
                return_code = process.wait()
                with self._condition:
                    expected = self._closed or self._suspended
                if not expected:
                    _LOGGER.warning("CEC client exited with status %s; retrying", return_code)
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
                    if not self._closed and not self._suspended:
                        self._condition.wait(timeout=self._retry_interval)

    def _read_output(
        self, output: IO[str], process: subprocess.Popen[str], generation: int,
    ) -> None:
        """Translate libCEC's delivered-key logs, ignoring its duplicate raw logs."""
        held: dict[int, float] = {}
        for line in output:
            line = line.strip()
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
