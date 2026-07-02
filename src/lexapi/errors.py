"""Typed errors mirroring the LexAPI error envelope.

The API's canonical envelope is::

    {"success": false, "error": {"code": "NOT_FOUND", "message": "...", "details": {...}}}

Some legacy endpoints return a bare ``{"error": "...", "message": "..."}`` body
with string values; the parser tolerates both shapes. Unknown ``error.code``
values never crash the parser — the raw code string is preserved on the
generic :class:`LexAPIError` (forward compatibility).

Two distinct "timeout" errors exist by design:

- :class:`TimeoutError` — the *server's* typed 504 ``TIMEOUT`` envelope
  (upstream EUR-Lex fetch timed out; the spec marks it retry-safe).
- :class:`RequestTimeoutError` — the *SDK's* local httpx timeout (the
  response never arrived).
"""

from __future__ import annotations

from typing import Any

#: Fallback mapping used when the body carries no typed ``error.code``
#: (legacy bare ``{"error": "..."}`` shape, or non-JSON bodies).
STATUS_TO_CODE = {
    400: "INVALID_PARAMS",
    401: "UNAUTHORIZED",
    402: "CREDITS_EXHAUSTED",
    403: "TIER_FORBIDDEN",
    404: "NOT_FOUND",
    429: "RATE_LIMITED",
    500: "INTERNAL",
    502: "UPSTREAM_ERROR",
    503: "UPSTREAM_ERROR",
    504: "TIMEOUT",
}


class LexAPIError(Exception):
    """Base error for any failed LexAPI call.

    Attributes:
        code: stable machine-readable slug (``NOT_FOUND``, ``RATE_LIMITED``, ...).
            Unknown codes from newer API versions are preserved as-is.
        message: human-readable description from the API.
        status: HTTP status code (``None`` for transport-level failures).
        details: the ``error.details`` object when present.
        body: the raw decoded response body (``None`` if not JSON).
        retry_after: seconds to wait before retrying, when the server said so.
    """

    default_code = "UNKNOWN"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        details: Any | None = None,
        body: Any | None = None,
        retry_after: float | None = None,
    ) -> None:
        code = code or self.default_code
        suffix = f" (HTTP {status})" if status is not None else ""
        super().__init__(f"[{code}] {message}{suffix}")
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        self.body = body
        self.retry_after = retry_after

    @classmethod
    def from_response(
        cls, status: int, body: Any, retry_after: float | None = None
    ) -> LexAPIError:
        """Build the most specific typed error for a non-2xx response."""
        return error_from_response(status, body, retry_after=retry_after)


class AuthenticationError(LexAPIError):
    """401 — missing, invalid, or revoked API key."""

    default_code = "UNAUTHORIZED"


class InvalidParamsError(LexAPIError):
    """400 ``INVALID_PARAMS`` — missing or malformed parameters."""

    default_code = "INVALID_PARAMS"


class InvalidCelexError(LexAPIError):
    """400 ``INVALID_CELEX`` — CELEX identifier failed validation."""

    default_code = "INVALID_CELEX"


class InvalidURLError(LexAPIError):
    """400 ``INVALID_URL`` — URL had no resolvable CELEX."""

    default_code = "INVALID_URL"


class NotFoundError(LexAPIError):
    """404 ``NOT_FOUND`` — document or resource missing."""

    default_code = "NOT_FOUND"


class RateLimitedError(LexAPIError):
    """429 ``RATE_LIMITED`` — quota or rate ceiling hit.

    ``retry_after`` carries the server-directed wait in seconds when the
    server provided one (``Retry-After`` header preferred; see
    :mod:`lexapi._retry` for the resolution order).
    """

    default_code = "RATE_LIMITED"


class TierForbiddenError(LexAPIError):
    """403 ``TIER_FORBIDDEN`` — endpoint not included in the caller's tier."""

    default_code = "TIER_FORBIDDEN"


class CreditsExhaustedError(LexAPIError):
    """402 ``CREDITS_EXHAUSTED`` — monthly credit pool exhausted (pricing v2).

    ``resets_at`` is the ISO-8601 timestamp when the monthly allowance
    resets, when the server reported one. Retrying cannot help until
    top-up or the next cycle anchor.
    """

    default_code = "CREDITS_EXHAUSTED"

    def __init__(self, message: str, *, resets_at: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.resets_at = resets_at


class UpstreamError(LexAPIError):
    """502 ``UPSTREAM_ERROR`` — EUR-Lex returned an error."""

    default_code = "UPSTREAM_ERROR"


class TimeoutError(LexAPIError):  # noqa: A001 - mirrors the API's TIMEOUT code
    """504 ``TIMEOUT`` — the upstream EUR-Lex fetch timed out (retry-safe)."""

    default_code = "TIMEOUT"


class InternalServerError(LexAPIError):
    """500 ``INTERNAL`` — unexpected server failure."""

    default_code = "INTERNAL"


class LexAPIConnectionError(LexAPIError):
    """Transport-level failure — the HTTP exchange never completed."""

    default_code = "CONNECTION_ERROR"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("status", None)
        super().__init__(message, **kwargs)


class RequestTimeoutError(LexAPIConnectionError):
    """The SDK's local timeout elapsed before a response arrived.

    Distinct from :class:`TimeoutError`, which is the server's typed 504.
    """

    default_code = "REQUEST_TIMEOUT"


CODE_TO_ERROR: dict[str, type[LexAPIError]] = {
    "UNAUTHORIZED": AuthenticationError,
    "INVALID_PARAMS": InvalidParamsError,
    "INVALID_CELEX": InvalidCelexError,
    "INVALID_URL": InvalidURLError,
    "NOT_FOUND": NotFoundError,
    "RATE_LIMITED": RateLimitedError,
    "TIER_FORBIDDEN": TierForbiddenError,
    "CREDITS_EXHAUSTED": CreditsExhaustedError,
    "UPSTREAM_ERROR": UpstreamError,
    "TIMEOUT": TimeoutError,
    "INTERNAL": InternalServerError,
}


def _extract_resets_at(details: Any, body: Any) -> str | None:
    """Best-effort ``resetsAt`` for CREDITS_EXHAUSTED errors.

    The spec says the 402 ``details`` includes the pool state and
    ``resetsAt``; some responses also carry top-level ``usage`` / ``credits``
    blocks. Checked in that order.
    """
    for container in (details, body):
        if not isinstance(container, dict):
            continue
        value = container.get("resetsAt")
        if isinstance(value, str):
            return value
        for key in ("credits", "usage"):
            block = container.get(key)
            if isinstance(block, dict) and isinstance(block.get("resetsAt"), str):
                return block["resetsAt"]
    return None


def error_from_response(
    status: int, body: Any, retry_after: float | None = None
) -> LexAPIError:
    """Map a non-2xx response to the most specific typed error.

    Tolerates both envelope shapes:

    - typed: ``{"success": false, "error": {"code", "message", "details"}}``
    - legacy: ``{"error": "<slug or text>", "message": "..."}`` (both strings)

    Unknown ``error.code`` values fall back to the base :class:`LexAPIError`
    with the raw code preserved.
    """
    code = STATUS_TO_CODE.get(status, "INTERNAL")
    message = f"HTTP {status}"
    details: Any | None = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):  # typed envelope
            code = err.get("code") or code
            message = err.get("message") or message
            details = err.get("details")
        elif isinstance(err, str):  # legacy bare {"error": "...", "message": "..."}
            legacy_message = body.get("message")
            if err in CODE_TO_ERROR:  # some legacy bodies carry the slug in `error`
                code = err
                message = legacy_message if isinstance(legacy_message, str) else message
            else:
                message = err
                if isinstance(legacy_message, str):
                    message = f"{err}: {legacy_message}" if legacy_message != err else err
        elif isinstance(body.get("message"), str):
            message = body["message"]

    error_cls = CODE_TO_ERROR.get(code, LexAPIError)
    kwargs: dict[str, Any] = {
        "code": code,
        "status": status,
        "details": details,
        "body": body,
        "retry_after": retry_after,
    }
    if error_cls is CreditsExhaustedError:
        kwargs["resets_at"] = _extract_resets_at(details, body)
    return error_cls(message, **kwargs)
