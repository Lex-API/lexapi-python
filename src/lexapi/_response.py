"""Response wrapper with credit-visibility helpers (SDK contract §1.5).

Every successful call returns an :class:`APIResponse` (or a typed subclass).
It behaves like a read-only mapping over the raw JSON body — existing
``response["key"]`` code keeps working — and additionally exposes:

- ``credits`` — the pricing-v2 credit-pool block, typed (``None`` on legacy
  daily-call accounts, which omit it)
- ``usage`` — the dual-mode usage block, typed
- ``units_charged`` — credits this request cost (``credits.operation_weight``;
  0 for free operations such as ``/info`` and for legacy accounts)
- ``credits_remaining`` — ``credits.remaining`` falling back to ``usage.remaining``
- ``resets_at`` — when the quota window resets (ISO-8601 string)
- ``subscription_info`` — the ``subscription`` block, typed
- ``warning`` — the ``X-Warning`` response header, when present
- ``rate_limit_limit`` / ``rate_limit_remaining`` / ``rate_limit_reset`` —
  the draft-6 ``RateLimit-*`` headers every ``/api/v1`` response carries

Credit figures are the ones the server knew when it serialised the
response. A settlement the handler applies before answering (a document
read served from the corpus at 1 credit or from Cellar at 4, a HyDE
fallback at 5) is reflected in ``units_charged``; refunds for *failed*
requests are applied just after the response is sent, so the error's own
figures still show the pre-refund pool and the next call reflects it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def header(headers: httpx.Headers, name: str) -> str | None:
    """A response header as ``str | None`` (httpx types ``get`` as ``Any``)."""
    value = headers.get(name)
    return value if isinstance(value, str) else None


def _header_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class SubscriptionInfo:
    """The ``subscription`` block: the caller's tier and its limits.

    Endpoints echo the subset that applies to them (``max_pages`` on search,
    ``max_batch_size`` on batch, ``max_results`` on semantic search), so most
    fields are ``None`` on most responses; ``/info`` carries the full set.
    """

    tier: str | None = None
    pricing_mode: str | None = None
    max_pages: int | None = None
    max_batch_size: int | None = None
    max_webhooks: int | None = None
    max_results: int | None = None
    daily_limit: int | None = None
    monthly_credits: int | None = None
    rate_limit: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> SubscriptionInfo | None:
        if not isinstance(data, dict):
            return None
        return cls(
            tier=_opt_str(data.get("tier")),
            pricing_mode=_opt_str(data.get("pricingMode")),
            max_pages=_opt_int(data.get("maxPages")),
            max_batch_size=_opt_int(data.get("maxBatchSize")),
            max_webhooks=_opt_int(data.get("maxWebhooks")),
            max_results=_opt_int(data.get("maxResults")),
            daily_limit=_opt_int(data.get("dailyLimit")),
            monthly_credits=_opt_int(data.get("monthlyCredits")),
            rate_limit=_opt_str(data.get("rateLimit")),
            raw=data,
        )


@dataclass(frozen=True)
class CreditsInfo:
    """Pricing-v2 credit-pool state (the response ``credits`` block)."""

    operation_weight: int | None = None
    current: int | None = None
    limit: int | None = None
    remaining: int | None = None
    included: int | None = None
    topup: int | None = None
    resets_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> CreditsInfo | None:
        if not isinstance(data, dict):
            return None
        return cls(
            operation_weight=_opt_int(data.get("operation_weight")),
            current=_opt_int(data.get("current")),
            limit=_opt_int(data.get("limit")),
            remaining=_opt_int(data.get("remaining")),
            included=_opt_int(data.get("included")),
            topup=_opt_int(data.get("topup")),
            resets_at=data.get("resetsAt") if isinstance(data.get("resetsAt"), str) else None,
            raw=data,
        )


@dataclass(frozen=True)
class UsageInfo:
    """Dual-mode usage counters (credits for v2 accounts, calls for legacy)."""

    current: int | None = None
    limit: int | None = None
    remaining: int | None = None
    unit: str | None = None
    resets_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> UsageInfo | None:
        if not isinstance(data, dict):
            return None
        return cls(
            current=_opt_int(data.get("current")),
            limit=_opt_int(data.get("limit")),
            remaining=_opt_int(data.get("remaining")),
            unit=data.get("unit") if isinstance(data.get("unit"), str) else None,
            resets_at=data.get("resetsAt") if isinstance(data.get("resetsAt"), str) else None,
            raw=data,
        )


class APIResponse(Mapping[str, Any]):
    """Read-only mapping over the raw JSON body, plus typed helpers.

    The raw body stays fully accessible (``response["usage"]``,
    ``response.data``) so no information is lost by the typed layer.
    """

    def __init__(self, data: Any, headers: httpx.Headers | None = None) -> None:
        self.data: Any = data
        self._map: dict[str, Any] = data if isinstance(data, dict) else {}
        self.headers: httpx.Headers = headers if headers is not None else httpx.Headers()

    # -- Mapping protocol -------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._map[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._map)

    def __len__(self) -> int:
        return len(self._map)

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"{type(self).__name__}({self._map!r})"

    # -- credit visibility (SDK contract §1.5) ----------------------------
    @property
    def credits(self) -> CreditsInfo | None:
        """The pricing-v2 ``credits`` block; ``None`` on legacy accounts."""
        return CreditsInfo.from_dict(self._map.get("credits"))

    @property
    def usage(self) -> UsageInfo | None:
        """The dual-mode ``usage`` block; ``None`` when absent."""
        return UsageInfo.from_dict(self._map.get("usage"))

    @property
    def units_charged(self) -> int:
        """Credits this request cost (0 for free ops and legacy accounts)."""
        credits = self.credits
        if credits is not None and credits.operation_weight is not None:
            return credits.operation_weight
        return 0

    @property
    def credits_remaining(self) -> int | None:
        """``credits.remaining``, falling back to ``usage.remaining``."""
        credits = self.credits
        if credits is not None and credits.remaining is not None:
            return credits.remaining
        usage = self.usage
        return usage.remaining if usage is not None else None

    @property
    def resets_at(self) -> str | None:
        """When the quota window resets (ISO-8601), from credits or usage."""
        credits = self.credits
        if credits is not None and credits.resets_at is not None:
            return credits.resets_at
        usage = self.usage
        return usage.resets_at if usage is not None else None

    @property
    def subscription(self) -> dict[str, Any] | None:
        """The raw ``subscription`` block, when present."""
        value = self._map.get("subscription")
        return value if isinstance(value, dict) else None

    @property
    def subscription_info(self) -> SubscriptionInfo | None:
        """The ``subscription`` block, typed (``None`` when absent)."""
        return SubscriptionInfo.from_dict(self._map.get("subscription"))

    # -- header-borne signals ---------------------------------------------
    @property
    def warning(self) -> str | None:
        """The ``X-Warning`` header (tier clamping and similar), if any."""
        return header(self.headers, "x-warning")

    @property
    def rate_limit_limit(self) -> int | None:
        """Requests allowed per window for this key (``RateLimit-Limit``)."""
        return _header_int(header(self.headers, "ratelimit-limit"))

    @property
    def rate_limit_remaining(self) -> int | None:
        """Requests left in the current window (``RateLimit-Remaining``)."""
        return _header_int(header(self.headers, "ratelimit-remaining"))

    @property
    def rate_limit_reset(self) -> int | None:
        """Seconds until the rate-limit window resets (``RateLimit-Reset``)."""
        return _header_int(header(self.headers, "ratelimit-reset"))

    @property
    def national_allowance_limit(self) -> int | None:
        """Monthly national-law call allowance for FREE/STARTER/PROFESSIONAL
        accounts (``X-National-Limit``). Absent for BUSINESS and above, for
        preview accounts, and on every non-national response."""
        return _header_int(header(self.headers, "x-national-limit"))

    @property
    def national_allowance_remaining(self) -> int | None:
        """National calls left this UTC month (``X-National-Remaining``)."""
        return _header_int(header(self.headers, "x-national-remaining"))

    @property
    def national_allowance_resets_at(self) -> str | None:
        """When the national allowance resets — start of next UTC month
        (``X-National-Reset``, ISO-8601)."""
        return header(self.headers, "x-national-reset")
