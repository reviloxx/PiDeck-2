"""Tests for the optional libCEC input adapter."""

import io
import subprocess
import sys
import threading
from unittest.mock import MagicMock

import pytest

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.input_cec import CecClientAdapter


class RecordingSink:
    """Collect input events from the CEC worker."""

    def __init__(self) -> None:
        self.events: list[InputEvent] = []
        self.received = threading.Event()

    def handle_input(self, event: InputEvent) -> None:
        """Record one event and notify the test."""
        self.events.append(event)
        self.received.set()


def test_cec_parses_delivered_keys_without_duplicate_raw_logs() -> None:
    """Only libCEC's delivered-key log is translated, with release tracking."""
    adapter = CecClientAdapter()
    sink = RecordingSink()
    process = MagicMock(spec=subprocess.Popen)
    adapter._sink = sink
    adapter._suspended = False
    adapter._process = process
    output = io.StringIO(
        "DEBUG: [1] key pressed: up (1) current(100) duration(0)\n"
        "DEBUG: [1] key pressed: up (1, 0)\n"
        "DEBUG: [2] key pressed: up (1, 0)\n"
        "DEBUG: [3] key released: up (1) D:25ms\n"
        "DEBUG: [4] key pressed: up (1, 0)\n"
        "DEBUG: [5] key pressed: select (0, 0)\n"
        "DEBUG: [6] key pressed: select (0, 0)\n"
        "DEBUG: [7] key released: select (0) D:10ms\n"
        "DEBUG: [8] key pressed: select (0, 0)\n"
        "DEBUG: [9] key pressed: exit (d, 0)\n"
        "DEBUG: [10] key pressed: root menu (9, 0)\n"
        "DEBUG: [11] key pressed: power (40, 0)\n"
        "DEBUG: [12] key pressed: down (2, 0)\n"
        "DEBUG: [13] key pressed: left (3, 0)\n"
        "DEBUG: [14] key pressed: right (4, 0)\n"
        "DEBUG: [15] Key unknown: idle (duration:0) (ff) timeout:50ms (rel:0,rep:0,prs:0,rel:0)\n"
        "DEBUG: [16] key pressed: select (0, 0)\n"
    )

    adapter._read_output(output, process, adapter.generation)

    assert [event.action for event in sink.events] == [
        InputAction.UP, InputAction.UP, InputAction.ACTIVATE, InputAction.ACTIVATE,
        InputAction.BACK, InputAction.HOME, InputAction.DOWN, InputAction.LEFT,
        InputAction.RIGHT, InputAction.ACTIVATE,
    ]
    assert all(event.source == "cec" for event in sink.events)


def test_cec_rejects_old_client_output_after_handoff() -> None:
    """Buffered output from a previous client cannot activate the launcher."""
    adapter = CecClientAdapter()
    sink = RecordingSink()
    process = MagicMock(spec=subprocess.Popen)
    adapter._sink = sink
    adapter._suspended = False
    adapter._process = process
    old_generation = adapter.generation
    adapter._generation += 1

    adapter._read_output(io.StringIO("DEBUG: [1] key pressed: select (0, 0)\n"), process, old_generation)

    assert not sink.events


def test_cec_releases_process_and_restarts_after_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual child exits before suspend returns and can be restarted."""
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[str]] = []
    commands: list[list[str]] = []

    def start_client(
        command: list[str], *, stdin: int, stdout: int, stderr: int,
        text: bool, encoding: str, errors: str, env: dict[str, str],
    ) -> subprocess.Popen[str]:
        commands.append(command)
        process = real_popen(
            [sys.executable, "-u", "-c",
             "import time; print('DEBUG: [1] key pressed: up (1, 0)', flush=True); time.sleep(60)"],
            stdin=stdin, stdout=stdout, stderr=stderr, text=text,
            encoding=encoding, errors=errors, env=env,
        )
        children.append(process)
        return process

    monkeypatch.setattr("pideck.infrastructure.input_cec.subprocess.Popen", start_client)
    sink = RecordingSink()
    adapter = CecClientAdapter(retry_interval=0.01)
    adapter.start(sink)
    try:
        assert sink.received.wait(3)
        old_generation = adapter.generation
        adapter.suspend()
        assert children[0].poll() is not None
        assert not adapter.accepts(old_generation)
        assert commands[0] == ["cec-client", "-d", "31", "-t", "p", "-o", "PiDeck"]
        sink.received.clear()
        adapter.resume()
        assert sink.received.wait(3)
        assert len(children) == 2
        assert not adapter.accepts(old_generation)
    finally:
        adapter.close()
    assert all(child.poll() is not None for child in children)


def test_missing_cec_client_does_not_break_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unavailable optional executable is reported and remains recoverable."""
    attempted = threading.Event()

    def missing_client(*args: object, **kwargs: object) -> None:
        attempted.set()
        raise FileNotFoundError("cec-client")

    monkeypatch.setattr("pideck.infrastructure.input_cec.subprocess.Popen", missing_client)
    adapter = CecClientAdapter(retry_interval=60)
    adapter.start(RecordingSink())
    assert attempted.wait(2)
    adapter.suspend()
    adapter.close()


def test_cec_retries_after_failed_connection(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed client is reported and retried when an adapter becomes available."""
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[str]] = []

    def start_client(
        command: list[str], *, stdin: int, stdout: int, stderr: int,
        text: bool, encoding: str, errors: str, env: dict[str, str],
    ) -> subprocess.Popen[str]:
        script = (
            "print('unable to open the device', flush=True); raise SystemExit(1)"
            if not children else
            "import time; print('DEBUG: [1] key pressed: down (2, 0)', flush=True); time.sleep(60)"
        )
        process = real_popen(
            [sys.executable, "-u", "-c", script],
            stdin=stdin, stdout=stdout, stderr=stderr, text=text,
            encoding=encoding, errors=errors, env=env,
        )
        children.append(process)
        return process

    monkeypatch.setattr("pideck.infrastructure.input_cec.subprocess.Popen", start_client)
    sink = RecordingSink()
    adapter = CecClientAdapter(retry_interval=0.01)
    adapter.start(sink)
    try:
        assert sink.received.wait(3)
        assert [event.action for event in sink.events] == [InputAction.DOWN]
        assert len(children) == 2
        assert "unable to open the device" in caplog.text
    finally:
        adapter.close()
    assert all(child.poll() is not None for child in children)
