"""Tests for transient GitHub HTTP retry behaviour."""

from email.message import EmailMessage
from io import BytesIO

import pytest
from urllib.error import HTTPError, URLError

from pideck.infrastructure.http_retry import call_with_retries, is_transient_error, retry_after_seconds


def _http_error(code: int, *, retry_after: str | None = None) -> HTTPError:
    headers = EmailMessage()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError("https://example.test", code, "error", headers, BytesIO())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(504), True),
        (_http_error(503), True),
        (_http_error(429), True),
        (_http_error(408), True),
        (_http_error(500), True),
        (_http_error(502), True),
        (_http_error(404), False),
        (_http_error(403), False),
        (TimeoutError("timed out"), True),
        (URLError("timed out"), True),
        (OSError("Connection reset by peer"), True),
        (RuntimeError("HTTP Error 504: Gateway Timeout"), True),
        (RuntimeError("disk full"), False),
        (ValueError("invalid"), False),
    ],
)
def test_transient_error_classification(error: BaseException, expected: bool) -> None:
    """Only temporary network and gateway failures are retried."""
    assert is_transient_error(error) is expected


def test_retry_after_header_is_honoured() -> None:
    """GitHub rate-limit delays come from Retry-After when present."""
    assert retry_after_seconds(_http_error(429, retry_after="2.5")) == 2.5
    assert retry_after_seconds(_http_error(504)) is None
    assert retry_after_seconds(TimeoutError()) is None


def test_call_with_retries_recovers_from_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single 504 does not fail the operation when a later attempt succeeds."""
    delays: list[float] = []
    monkeypatch.setattr("pideck.infrastructure.http_retry.random.uniform", lambda _a, _b: 0.0)
    attempts = {"count": 0}

    def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _http_error(504)
        return "ok"

    assert call_with_retries(operation, sleep=delays.append, label="test") == "ok"
    assert attempts["count"] == 2
    assert delays == [1.0]


def test_call_with_retries_stops_on_permanent_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permanent HTTP errors are not retried."""
    delays: list[float] = []
    attempts = {"count": 0}

    def operation() -> None:
        attempts["count"] += 1
        raise _http_error(404)

    with pytest.raises(HTTPError) as raised:
        call_with_retries(operation, sleep=delays.append)
    assert raised.value.code == 404
    assert attempts["count"] == 1
    assert delays == []


def test_call_with_retries_exhausts_transient_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated gateway timeouts eventually surface to the caller."""
    monkeypatch.setattr("pideck.infrastructure.http_retry.random.uniform", lambda _a, _b: 0.0)
    delays: list[float] = []

    def operation() -> None:
        raise _http_error(504)

    with pytest.raises(HTTPError) as raised:
        call_with_retries(operation, attempts=3, sleep=delays.append)
    assert raised.value.code == 504
    assert delays == [1.0, 2.0]


def test_call_with_retries_uses_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry-After overrides the exponential backoff when GitHub asks for a wait."""
    monkeypatch.setattr("pideck.infrastructure.http_retry.random.uniform", lambda _a, _b: 0.0)
    delays: list[float] = []
    attempts = {"count": 0}

    def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _http_error(429, retry_after="7")
        return "ok"

    assert call_with_retries(operation, sleep=delays.append) == "ok"
    assert delays == [7.0]
