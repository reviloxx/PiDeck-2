"""Retry transient network failures when talking to GitHub."""

from __future__ import annotations

from collections.abc import Callable
import logging
import random
import re
import time
from typing import TypeVar
from urllib.error import HTTPError, URLError


_LOGGER = logging.getLogger(__name__)
# Rate limits (429 / primary 403) are never short-retried; see GitHubRateLimitError.
_TRANSIENT_STATUS = frozenset({408, 500, 502, 503, 504})
_TRANSIENT_TEXT = re.compile(
    r"(?i)(?:timed?\s*out|timeout|temporarily\s+unavailable|connection\s+reset|"
    r"connection\s+aborted|broken\s+pipe|network\s+is\s+unreachable|"
    r"name\s+or\s+service\s+not\s+known|temporary\s+failure|"
    r"HTTP\s+Error\s+(?:408|500|502|503|504)|Gateway\s+Time-?out|"
    r"Service\s+Unavailable|Bad\s+Gateway)"
)
_RATE_LIMIT_TEXT = re.compile(
    r"(?i)(?:rate\s*limit|secondary\s+rate\s+limit|too\s+many\s+requests|"
    r"API\s+rate\s+limit\s+exceeded)"
)
_SECONDARY_FALLBACK_SECONDS = 60.0
_T = TypeVar("_T")


class GitHubRateLimitError(Exception):
    """GitHub asked us to stop calling the API until ``not_before`` (UTC epoch seconds)."""

    def __init__(self, not_before: float, message: str | None = None) -> None:
        self.not_before = not_before
        self.message = message or format_rate_limit_message(not_before)
        super().__init__(self.message)


def format_rate_limit_message(not_before: float) -> str:
    """Build a short user-facing wait message from a UTC epoch resume time."""
    resume = time.strftime("%H:%M UTC", time.gmtime(not_before))
    return f"GitHub rate limit; next check after {resume}"


def _header(error: HTTPError, name: str) -> str | None:
    if error.headers is None:
        return None
    value = error.headers.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _error_body(error: HTTPError) -> str:
    try:
        data = error.read()
    except Exception:
        return str(error)
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(error)


def is_github_rate_limit(error: BaseException) -> bool:
    """Return whether ``error`` is a GitHub primary or secondary rate-limit response."""
    if isinstance(error, GitHubRateLimitError):
        return True
    if not isinstance(error, HTTPError):
        return _RATE_LIMIT_TEXT.search(str(error)) is not None
    if error.code == 429:
        return True
    remaining = _header(error, "X-RateLimit-Remaining")
    if error.code in {403, 429} and remaining == "0":
        return True
    if error.code in {403, 429} and _RATE_LIMIT_TEXT.search(_error_body(error) + " " + str(error)):
        return True
    return False


def rate_limit_not_before(error: BaseException, *, now: float | None = None) -> float:
    """Return the earliest UTC epoch when another GitHub API request is allowed."""
    if isinstance(error, GitHubRateLimitError):
        return error.not_before
    clock = time.time() if now is None else now
    if not isinstance(error, HTTPError):
        return clock + _SECONDARY_FALLBACK_SECONDS
    retry_after = retry_after_seconds(error)
    if retry_after is not None:
        return clock + retry_after
    remaining = _header(error, "X-RateLimit-Remaining")
    reset = _header(error, "X-RateLimit-Reset")
    if remaining == "0" and reset is not None:
        try:
            return max(clock, float(reset))
        except ValueError:
            pass
    return clock + _SECONDARY_FALLBACK_SECONDS


def github_rate_limit_error(error: BaseException, *, now: float | None = None) -> GitHubRateLimitError:
    """Convert a rate-limit response into a hard-stop error with a resume time."""
    not_before = rate_limit_not_before(error, now=now)
    return GitHubRateLimitError(not_before)


def is_transient_error(error: BaseException) -> bool:
    """Return whether ``error`` is worth a short retry (never rate limits)."""
    if is_github_rate_limit(error):
        return False
    if isinstance(error, HTTPError):
        return error.code in _TRANSIENT_STATUS
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, (OSError, URLError)):
        reason = getattr(error, "reason", None)
        if isinstance(reason, BaseException) and reason is not error and is_transient_error(reason):
            return True
        return _TRANSIENT_TEXT.search(str(error)) is not None
    return _TRANSIENT_TEXT.search(str(error)) is not None


def retry_after_seconds(error: BaseException) -> float | None:
    """Return a server-requested delay when a Retry-After header is present."""
    if not isinstance(error, HTTPError):
        return None
    raw = _header(error, "Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def call_with_retries(
    operation: Callable[[], _T],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    label: str = "request",
    sleep: Callable[[float], None] = time.sleep,
) -> _T:
    """Run ``operation`` until it succeeds or permanent/transient attempts are exhausted."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if is_github_rate_limit(error):
                raise github_rate_limit_error(error) from error
            if attempt >= attempts or not is_transient_error(error):
                raise
            delay = retry_after_seconds(error)
            if delay is None:
                delay = base_delay * (2 ** (attempt - 1))
                delay += random.uniform(0.0, delay * 0.25)
            _LOGGER.warning(
                "%s failed with transient error (%s); retrying in %.1fs (%s/%s)",
                label, error, delay, attempt, attempts,
            )
            sleep(delay)
    assert last_error is not None
    raise last_error
