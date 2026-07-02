"""Retry policy: what to retry, and how long to wait.

Defaults (SDK contract §1.2):

- up to 3 retries with exponential backoff + full jitter (base 0.5 s, cap 30 s)
- retryable statuses: 429, 502, 503, 504 — plus connect-phase transport
  errors (the request never reached the server, so any method is safe)
- non-idempotent methods (POST/PUT/PATCH/DELETE) retry **only** on 429
  (and connect-phase errors); GET retries on every retryable status
- server-directed wait wins over computed backoff. Resolution order:

  1. ``Retry-After`` response header (seconds or HTTP-date),
  2. ``RateLimit-Reset`` / ``X-RateLimit-Reset`` header (delta-seconds),
  3. the JSON ``retryAfter`` body field — **last resort only**: a known
     server bug emitted epoch seconds instead of delta seconds here, so
     epoch-looking values are converted to a delta defensively,
  4. computed exponential backoff.
"""

from __future__ import annotations

import email.utils
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

#: JSON ``retryAfter`` values above this are assumed to be (buggy) epoch
#: seconds rather than delta seconds and are converted to a delta.
_EPOCH_THRESHOLD = 10_000_000

_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class RetryConfig:
    """Retry policy knobs. ``max_retries=0`` disables retries entirely."""

    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_cap: float = 30.0
    retry_statuses: frozenset = field(default_factory=lambda: frozenset({429, 502, 503, 504}))
    #: Statuses retried even for non-idempotent methods.
    retry_statuses_non_idempotent: frozenset = field(default_factory=lambda: frozenset({429}))


DEFAULT_RETRY_CONFIG = RetryConfig()


def compute_backoff(attempt: int, config: RetryConfig) -> float:
    """Exponential backoff with full jitter: ``U(0, min(cap, base * 2**attempt))``."""
    ceiling = min(config.backoff_cap, config.backoff_base * (2**attempt))
    return random.uniform(0, ceiling)


def parse_retry_after(value: str) -> float | None:
    """Parse a ``Retry-After`` header: delta-seconds or HTTP-date."""
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        return max(0.0, when.timestamp() - time.time())
    return seconds if seconds >= 0 else None


def _body_retry_after(body: Any) -> float | None:
    """JSON ``retryAfter`` field, defensively handling the epoch-seconds bug."""
    if not isinstance(body, dict):
        return None
    raw = body.get("retryAfter")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value < 0:
        return None
    if value > _EPOCH_THRESHOLD:  # buggy epoch-seconds shape → convert to delta
        return max(0.0, value - time.time())
    return value


def server_wait_hint(headers: httpx.Headers, body: Any) -> float | None:
    """Resolve the server-directed wait for a 429/5xx response, if any.

    Header sources are preferred over the JSON ``retryAfter`` body field
    (which was buggy — epoch seconds — until a pending server fix).
    """
    header = headers.get("retry-after")
    if header is not None:
        parsed = parse_retry_after(header)
        if parsed is not None:
            return parsed
    for name in ("ratelimit-reset", "x-ratelimit-reset"):
        reset = headers.get(name)
        if reset is not None:
            try:
                seconds = float(reset)
            except ValueError:
                continue
            if seconds > _EPOCH_THRESHOLD:  # some servers emit epoch here too
                seconds = seconds - time.time()
            if seconds >= 0:
                return seconds
    return _body_retry_after(body)


def is_retryable_exception(exc: httpx.HTTPError) -> bool:
    """Connect-phase failures are safe to retry for any method.

    The request never reached the server (connection or connect-timeout
    failure), so retrying cannot duplicate a side effect.
    """
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def is_retryable_status(method: str, status: int, config: RetryConfig) -> bool:
    if method.upper() in _IDEMPOTENT_METHODS:
        return status in config.retry_statuses
    return status in config.retry_statuses_non_idempotent


def retry_wait(
    method: str,
    response: httpx.Response,
    body: Any,
    attempt: int,
    config: RetryConfig,
) -> float | None:
    """Seconds to sleep before retry *attempt*, or ``None`` for "don't retry".

    Server-directed waits win over computed backoff but are clamped to
    ``backoff_cap`` so a pathological ``Retry-After`` can't stall the caller.
    """
    if attempt >= config.max_retries:
        return None
    if not is_retryable_status(method, response.status_code, config):
        return None
    hint = server_wait_hint(response.headers, body)
    if hint is not None:
        return min(hint, config.backoff_cap)
    return compute_backoff(attempt, config)
