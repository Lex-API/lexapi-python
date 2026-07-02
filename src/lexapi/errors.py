"""Typed errors mirroring the LexAPI error envelope.

The API's canonical envelope is::

    {"success": false, "error": {"code": "NOT_FOUND", "message": "...", "details": {...}}}

Some legacy endpoints return a bare ``{"error": "..."}`` string body; the
parser tolerates both shapes.
"""

from __future__ import annotations

from typing import Any

_STATUS_TO_CODE = {
    400: "INVALID_PARAMS",
    401: "UNAUTHORIZED",
    402: "CREDITS_EXHAUSTED",
    403: "TIER_FORBIDDEN",
    404: "NOT_FOUND",
    429: "RATE_LIMITED",
    502: "UPSTREAM_ERROR",
    504: "TIMEOUT",
}


class LexAPIError(Exception):
    """Error raised for any non-2xx LexAPI response."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        details: Any | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message} (HTTP {status})")
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        self.retry_after = retry_after

    @classmethod
    def from_response(
        cls, status: int, body: Any, retry_after: float | None = None
    ) -> LexAPIError:
        code = _STATUS_TO_CODE.get(status, "INTERNAL")
        message = f"HTTP {status}"
        details: Any | None = None
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):  # typed envelope
                code = err.get("code") or code
                message = err.get("message") or message
                details = err.get("details")
            elif isinstance(err, str):  # legacy bare {"error": "..."}
                message = err
            if retry_after is None and isinstance(body.get("retryAfter"), (int, float)):
                retry_after = float(body["retryAfter"])
        return cls(code, message, status, details=details, retry_after=retry_after)
