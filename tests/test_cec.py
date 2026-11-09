"""Tests for the optional libCEC input adapter."""

import io
import subprocess
import sys
import threading
from unittest.mock import MagicMock

import pytest

from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.input_settings import CecSettings
from pideck.infrastructure.cec_kernel import CecKernelError
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
        "DEBUG: [11] key pressed: return (91, 0)\n"
        "DEBUG: [12] key pressed: power (40, 0)\n"
        "DEBUG: [13] key pressed: down (2, 0)\n"
        "DEBUG: [14] key pressed: left (3, 0)\n"
        "DEBUG: [15] key pressed: right (4, 0)\n"
        "DEBUG: [16] Key unknown: idle (duration:0) (ff) timeout:50ms (rel:0,rep:0,prs:0,rel:0)\n"
        "DEBUG: [17] key pressed: select (0, 0)\n"
    )

    adapter._read_output(output, process, adapter.generation)

    assert [event.action for event in sink.events] == [
        InputAction.UP, InputAction.UP, InputAction.ACTIVATE, InputAction.ACTIVATE,
        InputAction.MENU, InputAction.MENU, InputAction.BACK, InputAction.DOWN,
        InputAction.LEFT, InputAction.RIGHT, InputAction.ACTIVATE,
    ]
    assert all(event.source == "cec" for event in sink.events)


def test_cec_translates_media_keys_without_repeating_held_presses() -> None:
    """Transport keys map to media actions; a held key is delivered once."""
    adapter = CecClientAdapter()
    sink = RecordingSink()
    process = MagicMock(spec=subprocess.Popen)
    adapter._sink = sink
    adapter._suspended = False
    adapter._process = process
    output = io.StringIO(
        "DEBUG: [1] key pressed: play (44, 0)\n"
        "DEBUG: [2] key pressed: play (44, 0)\n"
        "DEBUG: [3] key released: play (44) D:90ms\n"
        "DEBUG: [4] key pressed: pause (46, 0)\n"
        "DEBUG: [5] key pressed: stop (45, 0)\n"
        "DEBUG: [6] key pressed: rewind (48, 0)\n"
        "DEBUG: [7] key pressed: Fast forward (49, 0)\n"
        "DEBUG: [8] key pressed: pause-play function (61, 0)\n"
        "DEBUG: [9] key pressed: play function (60, 0)\n"
        "DEBUG: [10] key pressed: stop function (64, 0)\n"
    )

    adapter._read_output(output, process, adapter.generation)

    assert [event.action for event in sink.events] == [
        InputAction.PLAY, InputAction.PAUSE, InputAction.STOP, InputAction.REWIND,
        InputAction.FAST_FORWARD, InputAction.PLAY_PAUSE, InputAction.PLAY,
        InputAction.STOP,
    ]

def test_cec_rearms_back_without_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Samsung Return often omits key-released; Back must still work more than once."""
    adapter = CecClientAdapter()
    sink = RecordingSink()
    process = MagicMock(spec=subprocess.Popen)
    adapter._sink = sink
    adapter._suspended = False
    adapter._process = process
    clock = {"now": 100.0}
    monkeypatch.setattr("pideck.infrastructure.input_cec.time.monotonic", lambda: clock["now"])
    lines = [
        "DEBUG: [1] key pressed: return (91, 0)\n",
        "DEBUG: [2] key pressed: return (91, 0)\n",
        "DEBUG: [3] key pressed: return (91, 0)\n",
    ]

    def timed_lines():
        for index, line in enumerate(lines):
            if index == 1:
                clock["now"] = 100.1  # within debounce → ignored
            elif index == 2:
                clock["now"] = 100.4  # after re-arm → delivered
            yield line

    adapter._read_output(timed_lines(), process, adapter.generation)  # type: ignore[arg-type]
    assert [event.action for event in sink.events] == [InputAction.BACK, InputAction.BACK]


def test_cec_rearms_stop_without_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Samsung Stop often omits key-released; Stop must still work more than once."""
    adapter = CecClientAdapter()
    sink = RecordingSink()
    process = MagicMock(spec=subprocess.Popen)
    adapter._sink = sink
    adapter._suspended = False
    adapter._process = process
    clock = {"now": 100.0}
    monkeypatch.setattr("pideck.infrastructure.input_cec.time.monotonic", lambda: clock["now"])
    lines = [
        "DEBUG: [1] key pressed: stop function (64, 0)\n",
        "DEBUG: [2] key pressed: stop function (64, 0)\n",
        "DEBUG: [3] key pressed: stop function (64, 0)\n",
    ]

    def timed_lines():
        for index, line in enumerate(lines):
            if index == 1:
                clock["now"] = 100.1  # within debounce → ignored
            elif index == 2:
                clock["now"] = 100.4  # after re-arm → delivered
            yield line

    adapter._read_output(timed_lines(), process, adapter.generation)  # type: ignore[arg-type]
    assert [event.action for event in sink.events] == [InputAction.STOP, InputAction.STOP]


def test_cec_logs_unmapped_key_codes_once(caplog: pytest.LogCaptureFixture) -> None:
    """Unknown remote codes are logged once so Samsung Return can be diagnosed."""
    adapter = CecClientAdapter()
    sink = RecordingSink()
    process = MagicMock(spec=subprocess.Popen)
    adapter._sink = sink
    adapter._suspended = False
    adapter._process = process
    output = io.StringIO(
        "DEBUG: [1] key pressed: unknown (a5, 0)\n"
        "DEBUG: [2] key pressed: unknown (a5, 0)\n"
        "DEBUG: [3] key pressed: return (91, 0)\n"
    )
    with caplog.at_level("WARNING", logger="pideck.infrastructure.input_cec"):
        adapter._read_output(output, process, adapter.generation)
    assert [event.action for event in sink.events] == [InputAction.BACK]
    messages = [record.getMessage() for record in caplog.records]
    assert sum("Unmapped CEC key code 0xa5" in message for message in messages) == 1


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


@pytest.mark.parametrize("switch_input", [False, True])
def test_kernel_client_applies_address_before_connect_and_switches_only_when_requested(
    monkeypatch: pytest.MonkeyPatch, switch_input: bool,
) -> None:
    """The selected address precedes process startup and source activation is opt-in."""
    real_popen = subprocess.Popen
    calls: list[object] = []
    connected = threading.Event()
    received = threading.Event()
    children: list[subprocess.Popen[str]] = []

    def prepare(settings: CecSettings) -> str:
        calls.append(settings.hdmi_port)
        return "Linux"

    def start_client(command: list[str], **kwargs: object) -> subprocess.Popen[str]:
        calls.append(command)
        child = real_popen(
            [sys.executable, "-u", "-c",
             "import sys; print('waiting for input', flush=True); "
             "line = sys.stdin.readline(); "
             "print('DEBUG: [1] key pressed: up (1, 0)', flush=True) if line == 'as\\n' else None; "
             "sys.stdin.read()"],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr("pideck.infrastructure.input_cec.prepare_kernel_cec", prepare)
    monkeypatch.setattr("pideck.infrastructure.input_cec.subprocess.Popen", start_client)

    def status(value: str) -> None:
        if value == "Connected":
            connected.set()

    sink = RecordingSink()
    adapter = CecClientAdapter(
        settings=CecSettings(hdmi_port=2, switch_input=switch_input), status_changed=status,
    )
    adapter.start(sink)
    try:
        assert connected.wait(3)
        assert calls[0] == 2
        assert calls[1][-1] == "Linux"
        assert sink.received.wait(0.3) is switch_input
    finally:
        adapter.close()
    assert all(child.poll() is not None for child in children)


def test_manual_connection_survives_handoff_but_does_not_retry_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual mode starts disconnected and restores its connection after an app."""
    real_popen = subprocess.Popen
    started = threading.Event()
    children: list[subprocess.Popen[str]] = []

    monkeypatch.setattr("pideck.infrastructure.input_cec.prepare_kernel_cec", lambda settings: "Linux")

    def start_client(command: list[str], **kwargs: object) -> subprocess.Popen[str]:
        child = real_popen(
            [sys.executable, "-u", "-c",
             "import time; print('waiting for input', flush=True); time.sleep(60)"],
            **kwargs,
        )
        children.append(child)
        started.set()
        return child

    monkeypatch.setattr("pideck.infrastructure.input_cec.subprocess.Popen", start_client)
    adapter = CecClientAdapter(settings=CecSettings(auto_connect=False), retry_interval=0.01)
    adapter.start(RecordingSink())
    try:
        assert not started.wait(0.1)
        adapter.connect()
        assert started.wait(3)
        adapter.suspend()
        assert children[0].poll() is not None
        started.clear()
        adapter.resume()
        assert started.wait(3)
        adapter.disconnect()
        assert len(children) == 2
    finally:
        adapter.close()


def test_manual_connection_reports_error_without_automatic_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed manual connection remains disconnected until another request."""
    failed = threading.Event()
    attempts: list[bool] = []

    def prepare(settings: CecSettings) -> str:
        attempts.append(True)
        raise CecKernelError("HDMI address unavailable")

    def status(value: str) -> None:
        if "HDMI address unavailable" in value:
            failed.set()

    monkeypatch.setattr("pideck.infrastructure.input_cec.prepare_kernel_cec", prepare)
    adapter = CecClientAdapter(
        settings=CecSettings(auto_connect=False), retry_interval=0.01, status_changed=status,
    )
    adapter.start(RecordingSink())
    try:
        adapter.connect()
        assert failed.wait(3)
        assert not threading.Event().wait(0.1)
        assert len(attempts) == 1
        assert "HDMI address unavailable" in adapter.status
    finally:
        adapter.close()


def test_suspension_waits_for_inflight_preparation_and_fences_old_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old preparation cannot start a client after the handoff completes."""
    preparing = threading.Event()
    release = threading.Event()
    suspended = threading.Event()
    launches: list[bool] = []

    def prepare(settings: CecSettings) -> str:
        preparing.set()
        assert release.wait(3)
        return "Linux"

    def launch(*args: object, **kwargs: object) -> None:
        launches.append(True)
        raise FileNotFoundError("unexpected client")

    monkeypatch.setattr("pideck.infrastructure.input_cec.prepare_kernel_cec", prepare)
    monkeypatch.setattr("pideck.infrastructure.input_cec.subprocess.Popen", launch)
    adapter = CecClientAdapter(settings=CecSettings())
    adapter.start(RecordingSink())
    try:
        assert preparing.wait(3)
        adapter.configure(CecSettings(enabled=False))
        thread = threading.Thread(target=lambda: (adapter.suspend(), suspended.set()))
        thread.start()
        assert not suspended.wait(0.1)
        release.set()
        assert suspended.wait(3)
        thread.join(timeout=3)
        assert not launches
    finally:
        release.set()
        adapter.close()
