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
- ``warning`` — the ``X-Warning`` response header, when present
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
    raw: dict = field(default_factory=dict)

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
    raw: dict = field(default_factory=dict)

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


class APIResponse(Mapping):
    """Read-only mapping over the raw JSON body, plus typed helpers.

    The raw body stays fully accessible (``response["usage"]``,
    ``response.data``) so no information is lost by the typed layer.
    """

    def __init__(self, data: Any, headers: httpx.Headers | None = None) -> None:
        self.data: Any = data
        self._map: dict = data if isinstance(data, dict) else {}
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
    def subscription(self) -> dict | None:
        """The raw ``subscription`` block, when present."""
        value = self._map.get("subscription")
        return value if isinstance(value, dict) else None

    # -- header-borne signals ---------------------------------------------
    @property
    def warning(self) -> str | None:
        """The ``X-Warning`` header (tier clamping and similar), if any."""
        return self.headers.get("x-warning")
