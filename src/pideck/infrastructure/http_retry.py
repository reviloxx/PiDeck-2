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
_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_TRANSIENT_TEXT = re.compile(
    r"(?i)(?:timed?\s*out|timeout|temporarily\s+unavailable|connection\s+reset|"
    r"connection\s+aborted|broken\s+pipe|network\s+is\s+unreachable|"
    r"name\s+or\s+service\s+not\s+known|temporary\s+failure|"
    r"HTTP\s+Error\s+(?:408|429|500|502|503|504)|Gateway\s+Time-?out|"
    r"Service\s+Unavailable|Bad\s+Gateway|Too\s+Many\s+Requests)"
)
_T = TypeVar("_T")


def is_transient_error(error: BaseException) -> bool:
    """Return whether ``error`` is worth retrying against GitHub."""
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
    raw = error.headers.get("Retry-After") if error.headers is not None else None
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
