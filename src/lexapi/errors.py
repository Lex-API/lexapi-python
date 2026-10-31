"""Typed errors mirroring the LexAPI error envelope.

The API's canonical envelope is::

    {"success": false, "error": {"code": "NOT_FOUND", "message": "...", "details": {...}}}

Some endpoint classes still answer in the legacy bare shape
``{"error": "<text>", "message": "...", ...}`` (API-key auth 401s, every 429,
search/batch/webhook validation 400s, the ``/export`` and legacy semantic
tier gates, semantic upstream 502/504s). The parser tolerates both shapes; on
the legacy shape the code is the body's top-level string ``code`` when present
(lex-api #304: ``RATE_LIMITED``, ``QUOTA_EXHAUSTED``, ...), else derived from
the HTTP status, and a top-level ``details`` (validation errors) is preserved.
Unknown ``error.code`` values never crash the parser — the raw code string
is preserved on the generic :class:`LexAPIError` (forward compatibility).

Two distinct "timeout" errors exist by design:

- :class:`TimeoutError` — the *server's* typed 504 ``TIMEOUT`` envelope
  (upstream EUR-Lex fetch timed out; the spec marks it retry-safe).
- :class:`RequestTimeoutError` — the *SDK's* local httpx timeout (the
  response never arrived).
"""

from __future__ import annotations

from typing import Any

#: Fallback mapping used when the body carries no typed ``error.code``
#: (legacy bare ``{"error": "..."}`` shape, or non-JSON bodies). 520-524 are
#: Cloudflare's origin-error statuses (lex-api.com sits behind Cloudflare).
#: ``UNAUTHORIZED`` and ``SERVICE_UNAVAILABLE`` are SDK-side codes: the API
#: never sends them in ``error.code`` (its 401s and its deploy-drain 503 are
#: not typed envelopes).
STATUS_TO_CODE = {
    400: "INVALID_PARAMS",
    401: "UNAUTHORIZED",
    402: "CREDITS_EXHAUSTED",
    403: "TIER_FORBIDDEN",
    404: "NOT_FOUND",
    429: "RATE_LIMITED",
    500: "INTERNAL",
    502: "UPSTREAM_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "TIMEOUT",
    520: "UPSTREAM_ERROR",
    521: "UPSTREAM_ERROR",
    522: "UPSTREAM_ERROR",
    523: "UPSTREAM_ERROR",
    524: "TIMEOUT",
}


class LexAPIError(Exception):
    """Base error for any failed LexAPI call.

    Attributes:
        code: stable machine-readable slug (``NOT_FOUND``, ``RATE_LIMITED``, ...).
            Unknown codes from newer API versions are preserved as-is.
        message: human-readable description from the API.
        status: HTTP status code (``None`` for transport-level failures).
        details: ``error.details`` on the typed envelope, or the top-level
            ``details`` of a legacy validation body (e.g. the list of
            rejected webhook criteria), when present.
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
    def from_response(cls, status: int, body: Any, retry_after: float | None = None) -> LexAPIError:
        """Build the most specific typed error for a non-2xx response."""
        return error_from_response(status, body, retry_after=retry_after)

    @property
    def upgrade_url(self) -> str | None:
        """``upgradeUrl`` from a legacy tier-gate body (e.g. ``/pricing``)."""
        if isinstance(self.body, dict) and isinstance(self.body.get("upgradeUrl"), str):
            return str(self.body["upgradeUrl"])
        return None


class AuthenticationError(LexAPIError):
    """401 — missing, invalid, deactivated, or revoked API key."""

    default_code = "UNAUTHORIZED"


class InvalidParamsError(LexAPIError):
    """400 ``INVALID_PARAMS`` — missing or malformed parameters.

    Legacy validation bodies put their reasons in ``details`` (a list of
    strings, e.g. every rejected webhook criterion).
    """

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


def _usage_resets_at(body: Any) -> str | None:
    if isinstance(body, dict):
        if isinstance(body.get("resetsAt"), str):
            return str(body["resetsAt"])
        usage = body.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("resetsAt"), str):
            return str(usage["resetsAt"])
    return None


class RateLimitedError(LexAPIError):
    """429 ``RATE_LIMITED`` — per-minute rate limit — or ``QUOTA_EXHAUSTED``,
    a legacy quota.

    ``retry_after`` carries the server-directed wait in seconds when the
    server provided one (``Retry-After`` header preferred; see
    :mod:`lexapi._retry` for the resolution order).

    Legacy daily-call plans (and the legacy monthly semantic quota) also
    answer 429 when the *quota* is used up: ``code`` is ``QUOTA_EXHAUSTED``
    (servers before lex-api #304 sent no code; the quota is then recognised
    by its shape), :attr:`quota_exhausted` is True, :attr:`resets_at` says
    when it refills, and ``retry_after`` is ``None``. Those are not retried
    automatically.
    """

    default_code = "RATE_LIMITED"

    @property
    def resets_at(self) -> str | None:
        """When a legacy quota refills (ISO-8601): the body's ``resetsAt``,
        else ``usage.resetsAt``, if present."""
        return _usage_resets_at(self.body)

    @property
    def quota_exhausted(self) -> bool:
        """True when the 429 reports an exhausted quota, not a rate limit."""
        if self.code == "QUOTA_EXHAUSTED":
            return True
        # Fallback for servers before lex-api #304 (no top-level code).
        return (
            isinstance(self.body, dict)
            and "retryAfter" not in self.body
            and self.resets_at is not None
        )


class TierForbiddenError(LexAPIError):
    """403 ``TIER_FORBIDDEN`` — no active subscription, or the plan lacks
    the endpoint (``/export`` below BUSINESS, legacy FREE semantic access).

    Legacy tier gates carry an ``upgrade_url`` (``upgradeUrl``).
    """

    default_code = "TIER_FORBIDDEN"


class CreditsExhaustedError(LexAPIError):
    """402 ``CREDITS_EXHAUSTED`` — the monthly credit pool (incl. top-ups)
    cannot cover the operation's weight (pricing v2).

    ``details`` carries ``operation``, ``weight``, ``available``,
    ``included``, ``topup``, ``used`` and ``resetsAt``; each is exposed as a
    typed attribute. Variable-priced operations are admitted against their
    *upfront* weight (a document read needs 5 available even when it would
    settle at 1), so ``weight - available`` is the exact shortfall. Retrying
    cannot help until a top-up or the next monthly reset.
    """

    default_code = "CREDITS_EXHAUSTED"

    def __init__(self, message: str, *, resets_at: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.resets_at = resets_at

    def _detail_int(self, key: str) -> int | None:
        if not isinstance(self.details, dict):
            return None
        value = self.details.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    @property
    def operation(self) -> str | None:
        """The operation that was refused (e.g. ``semantic_case_law``)."""
        if isinstance(self.details, dict) and isinstance(self.details.get("operation"), str):
            return str(self.details["operation"])
        return None

    @property
    def weight(self) -> int | None:
        """Credits the operation needed upfront."""
        return self._detail_int("weight")

    @property
    def available(self) -> int | None:
        """Credits left in the pool when the request was refused."""
        return self._detail_int("available")

    @property
    def included(self) -> int | None:
        """The tier's monthly allowance."""
        return self._detail_int("included")

    @property
    def topup(self) -> int | None:
        """Purchased top-up credits."""
        return self._detail_int("topup")

    @property
    def used(self) -> int | None:
        """Credits used in the current window."""
        return self._detail_int("used")


class NationalAllowanceExhaustedError(LexAPIError):
    """402 ``NATIONAL_ALLOWANCE_EXHAUSTED`` — the monthly national-law call
    allowance of a plan below BUSINESS (FREE 30, STARTER 500, PROFESSIONAL
    2,000 calls per UTC month) is used up.

    Raised only by the national documents and national semantic-search
    endpoints (``client.national.*``); BUSINESS and above have no separate
    limit and never see this error. ``details`` carries ``jurisdiction``,
    ``limit``, ``remaining`` (always 0 here), ``resetsAt`` and
    ``upgradeUrl`` — each exposed as a typed attribute. Not retried: wait
    for ``resets_at`` or upgrade.
    """

    default_code = "NATIONAL_ALLOWANCE_EXHAUSTED"

    def __init__(self, message: str, *, resets_at: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.resets_at = resets_at

    def _detail_str(self, key: str) -> str | None:
        if not isinstance(self.details, dict):
            return None
        value = self.details.get(key)
        return value if isinstance(value, str) else None

    def _detail_int(self, key: str) -> int | None:
        if not isinstance(self.details, dict):
            return None
        value = self.details.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    @property
    def jurisdiction(self) -> str | None:
        """The jurisdiction code (e.g. ``AT``) the refused call targeted."""
        return self._detail_str("jurisdiction")

    @property
    def limit(self) -> int | None:
        """The plan's monthly allowance."""
        return self._detail_int("limit")

    @property
    def remaining(self) -> int | None:
        """Calls left this month (always 0 on this error)."""
        return self._detail_int("remaining")

    @property
    def upgrade_url(self) -> str | None:
        """``details.upgradeUrl`` (e.g. ``/pricing``) — overrides the base
        class's property, which reads the top-level body instead of
        ``details``, where this endpoint puts it."""
        return self._detail_str("upgradeUrl")


def _body_jurisdiction(err: LexAPIError) -> str | None:
    if isinstance(err.body, dict) and isinstance(err.body.get("jurisdiction"), str):
        return str(err.body["jurisdiction"])
    return None


def _field(err: LexAPIError, key: str) -> Any:
    """``error.details[key]`` (typed envelope) falling back to the raw
    ``body[key]`` (legacy envelope, where the same context is a top-level
    sibling of ``code``/``error`` instead of nested) — correct for either
    shape without the caller needing to know which one answered."""
    if isinstance(err.details, dict) and key in err.details:
        return err.details[key]
    if isinstance(err.body, dict):
        return err.body.get(key)
    return None


class NationalSemanticNotEnabledError(LexAPIError):
    """409 ``NATIONAL_SEMANTIC_NOT_ENABLED`` — the national semantic index
    does not search this jurisdiction (yet). Legacy-shape body with a
    top-level ``jurisdiction`` (not nested in ``details``). Not retryable;
    nothing was charged.
    """

    default_code = "NATIONAL_SEMANTIC_NOT_ENABLED"

    @property
    def jurisdiction(self) -> str | None:
        """The jurisdiction code (e.g. ``AT``) from the top-level body."""
        return _body_jurisdiction(self)


class UpstreamError(LexAPIError):
    """502 ``UPSTREAM_ERROR`` — EUR-Lex (or the semantic service) returned an error."""

    default_code = "UPSTREAM_ERROR"


class ServiceUnavailableError(UpstreamError):
    """503 — the API is briefly unavailable (e.g. a deploy draining
    connections). Retryable. SDK-side code ``SERVICE_UNAVAILABLE``; a
    subclass of :class:`UpstreamError` so code written against 0.1.0
    (where 503 mapped to ``UPSTREAM_ERROR``) keeps catching it."""

    default_code = "SERVICE_UNAVAILABLE"


class NationalSemanticUnavailableError(ServiceUnavailableError):
    """503 ``NATIONAL_SEMANTIC_UNAVAILABLE`` — the national semantic index
    for this jurisdiction is temporarily down. A subclass of
    :class:`ServiceUnavailableError` (itself a :class:`UpstreamError`), so
    ``except ServiceUnavailableError`` still catches it. Legacy-shape body
    with a top-level ``jurisdiction``. Retryable later; nothing was charged.
    """

    default_code = "NATIONAL_SEMANTIC_UNAVAILABLE"

    @property
    def jurisdiction(self) -> str | None:
        """The jurisdiction code (e.g. ``AT``) from the top-level body."""
        return _body_jurisdiction(self)


class DocumentWithdrawnError(LexAPIError):
    """410 ``DOCUMENT_WITHDRAWN`` — a national document its
    source withdrew (a takedown, a pseudonymisation fix, a robots.txt
    listing). LexAPI serves nothing of it: no title, no metadata, no text.

    Two envelope shapes carry this code: the TYPED envelope on a direct
    document read (``get_document``, ``error.details: {id, jurisdiction,
    withdrawnAt}``), and the LEGACY shape with those same three fields as
    top-level siblings of ``code``/``error`` on the citation methods (a
    withdrawn id queried directly, or reached via ``include_national``).
    :attr:`id` / :attr:`jurisdiction` / :attr:`withdrawn_at` read ``details``
    first, then fall back to the raw ``body`` — correct for either shape.

    Not retryable (the document is gone, not temporarily failing); nothing
    is ever charged for it. If you hold a cached copy, drop it — the
    withdrawal may be a legal takedown or a pseudonymisation fix (Terms §7:
    no re-identification of anonymised or pseudonymised people).
    """

    default_code = "DOCUMENT_WITHDRAWN"

    @property
    def id(self) -> str | None:  # noqa: A003 - mirrors the wire field name
        value = _field(self, "id")
        return value if isinstance(value, str) else None

    @property
    def jurisdiction(self) -> str | None:
        value = _field(self, "jurisdiction")
        return value if isinstance(value, str) else None

    @property
    def withdrawn_at(self) -> str | None:
        """ISO date-time the source withdrew the document; ``None`` when unknown."""
        value = _field(self, "withdrawnAt")
        return value if isinstance(value, str) else None


class TimeoutError(LexAPIError):  # noqa: A001 - mirrors the API's TIMEOUT code
    """504 ``TIMEOUT`` — the upstream fetch timed out (retry-safe)."""

    default_code = "TIMEOUT"


class InternalServerError(LexAPIError):
    """500 ``INTERNAL`` — unexpected server failure."""

    default_code = "INTERNAL"


class InvalidResponseError(LexAPIError):
    """A 2xx response whose body is not JSON (e.g. an HTML interstitial from
    a proxy). Raised instead of returning an empty, "successful" result."""

    default_code = "INVALID_RESPONSE"


class IncompleteExportError(LexAPIError):
    """An export stream is not the whole result: it ended without its
    ``_done`` trailer, its ``_done`` said ``"complete": false``, or the
    server sent an ``_error`` line (then on ``details``)."""

    default_code = "INCOMPLETE_EXPORT"


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
    "QUOTA_EXHAUSTED": RateLimitedError,
    "TIER_FORBIDDEN": TierForbiddenError,
    "CREDITS_EXHAUSTED": CreditsExhaustedError,
    "NATIONAL_ALLOWANCE_EXHAUSTED": NationalAllowanceExhaustedError,
    "NATIONAL_SEMANTIC_NOT_ENABLED": NationalSemanticNotEnabledError,
    "UPSTREAM_ERROR": UpstreamError,
    "SERVICE_UNAVAILABLE": ServiceUnavailableError,
    "NATIONAL_SEMANTIC_UNAVAILABLE": NationalSemanticUnavailableError,
    "DOCUMENT_WITHDRAWN": DocumentWithdrawnError,
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
                return str(block["resetsAt"])
    return None


def error_from_response(status: int, body: Any, retry_after: float | None = None) -> LexAPIError:
    """Map a non-2xx response to the most specific typed error.

    Tolerates both envelope shapes (PLAN.md §1.3):

    - typed: ``{"success": false, "error": {"code", "message", "details"}}``
      — ``code`` is authoritative;
    - legacy: ``{"error": "<text>", "message": "...", "details": [...]}`` —
      ``error`` is human text, never parsed as a code; the code is the
      top-level string ``code`` when present (lex-api #304), else derived
      from the HTTP status. ``message`` / ``details`` at the top level are
      kept (also on bodies that carry no ``error`` at all).

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
            if isinstance(body.get("code"), str) and body["code"]:
                code = body["code"]
            legacy_message = body.get("message")
            message = err
            if isinstance(legacy_message, str) and legacy_message != err:
                message = f"{err}: {legacy_message}"
            details = body.get("details")
        else:
            if isinstance(body.get("code"), str) and body["code"]:
                code = body["code"]
            if isinstance(body.get("message"), str):
                message = body["message"]
            details = body.get("details")

    error_cls = CODE_TO_ERROR.get(code, LexAPIError)
    kwargs: dict[str, Any] = {
        "code": code,
        "status": status,
        "details": details,
        "body": body,
        "retry_after": retry_after,
    }
    if error_cls is CreditsExhaustedError or error_cls is NationalAllowanceExhaustedError:
        kwargs["resets_at"] = _extract_resets_at(details, body)
    return error_cls(message, **kwargs)
