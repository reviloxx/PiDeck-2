"""Tests for transient GitHub HTTP retry behaviour."""

from email.message import EmailMessage
from io import BytesIO
import socket

import pytest
from urllib.error import HTTPError, URLError

from pideck.infrastructure.http_retry import (
    GitHubRateLimitError,
    call_with_retries,
    github_rate_limit_error,
    is_github_rate_limit,
    is_transient_error,
    rate_limit_not_before,
    retry_after_seconds,
)


def _german_dns_error() -> OSError:
    """Build a German-locale temporary DNS failure (EAI_AGAIN)."""
    return OSError(socket.EAI_AGAIN, "Temporärer Fehler bei der Namensauflösung")


def _http_error(
    code: int,
    *,
    retry_after: str | None = None,
    remaining: str | None = None,
    reset: str | None = None,
    body: bytes = b"",
) -> HTTPError:
    headers = EmailMessage()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    if remaining is not None:
        headers["X-RateLimit-Remaining"] = remaining
    if reset is not None:
        headers["X-RateLimit-Reset"] = reset
    return HTTPError("https://api.github.com/", code, "error", headers, BytesIO(body))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(504), True),
        (_http_error(503), True),
        (_http_error(408), True),
        (_http_error(500), True),
        (_http_error(502), True),
        (_http_error(429), False),
        (_http_error(404), False),
        (_http_error(403), False),
        (_http_error(403, remaining="0", reset="1700000000"), False),
        (TimeoutError("timed out"), True),
        (URLError("timed out"), True),
        (OSError("Connection reset by peer"), True),
        (OSError(socket.EAI_AGAIN, "Temporary failure in name resolution"), True),
        (_german_dns_error(), True),
        (URLError(_german_dns_error()), True),
        (OSError(socket.EAI_NONAME, "Name oder Dienst nicht bekannt"), True),
        (RuntimeError("HTTP Error 504: Gateway Timeout"), True),
        (RuntimeError("HTTP Error 429: Too Many Requests"), False),
        (RuntimeError("disk full"), False),
        (ValueError("invalid"), False),
    ],
)
def test_transient_error_classification(error: BaseException, expected: bool) -> None:
    """Only temporary network and gateway failures are short-retried."""
    assert is_transient_error(error) is expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(429), True),
        (_http_error(403, remaining="0", reset="1700000000"), True),
        (_http_error(403, body=b"API rate limit exceeded"), True),
        (_http_error(403, body=b"secondary rate limit"), True),
        (_http_error(403, body=b"Repository access blocked"), False),
        (_http_error(404), False),
        (_http_error(504), False),
        (GitHubRateLimitError(1700000000.0), True),
    ],
)
def test_rate_limit_detection(error: BaseException, expected: bool) -> None:
    """Primary and secondary GitHub rate limits are recognized explicitly."""
    assert is_github_rate_limit(error) is expected


def test_rate_limit_not_before_uses_retry_after() -> None:
    """Retry-After is the first authority for when the next API call is allowed."""
    error = _http_error(429, retry_after="30")
    assert rate_limit_not_before(error, now=1000.0) == 1030.0


def test_rate_limit_not_before_uses_reset_header() -> None:
    """X-RateLimit-Reset is used when remaining is zero and Retry-After is absent."""
    error = _http_error(403, remaining="0", reset="2000")
    assert rate_limit_not_before(error, now=1000.0) == 2000.0


def test_rate_limit_not_before_secondary_fallback() -> None:
    """Secondary limits without timing headers wait at least one minute."""
    error = _http_error(403, body=b"You have exceeded a secondary rate limit")
    assert rate_limit_not_before(error, now=1000.0) == 1060.0


def test_retry_after_header_is_parsed() -> None:
    """Retry-After values are parsed as second delays."""
    assert retry_after_seconds(_http_error(503, retry_after="2.5")) == 2.5
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


def test_call_with_retries_recovers_from_localized_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """German EAI_AGAIN messages are retried by errno, not English text."""
    delays: list[float] = []
    monkeypatch.setattr("pideck.infrastructure.http_retry.random.uniform", lambda _a, _b: 0.0)
    attempts = {"count": 0}

    def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise URLError(_german_dns_error())
        return "ok"

    assert call_with_retries(operation, sleep=delays.append, label="dns") == "ok"
    assert attempts["count"] == 2
    assert delays == [1.0]


def test_call_with_retries_never_retries_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate-limited responses must not be short-retried inside the helper."""
    delays: list[float] = []
    attempts = {"count": 0}

    def operation() -> None:
        attempts["count"] += 1
        raise _http_error(429, retry_after="3600", remaining="0", reset="9999999999")

    with pytest.raises(GitHubRateLimitError) as raised:
        call_with_retries(operation, sleep=delays.append)
    assert attempts["count"] == 1
    assert delays == []
    assert raised.value.not_before > 0


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


def test_call_with_retries_uses_retry_after_for_gateway_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry-After still overrides backoff for retriable gateway responses."""
    monkeypatch.setattr("pideck.infrastructure.http_retry.random.uniform", lambda _a, _b: 0.0)
    delays: list[float] = []
    attempts = {"count": 0}

    def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _http_error(503, retry_after="7")
        return "ok"

    assert call_with_retries(operation, sleep=delays.append) == "ok"
    assert delays == [7.0]


def test_github_rate_limit_error_message_includes_resume_time() -> None:
    """Users see when the next check is allowed."""
    error = github_rate_limit_error(_http_error(403, remaining="0", reset="1700003600"), now=1700000000.0)
    assert "GitHub rate limit" in error.message
    assert "UTC" in error.message
