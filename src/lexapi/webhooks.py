"""Webhooks resource namespace: ``client.webhooks.*``, plus signature
verification for the deliveries LexAPI POSTs to your endpoint.

A webhook needs ``search_criteria`` (``documents.new`` for each new
document matching the /search-vocabulary filters), ``watch_celex``
(``watchlist.changes`` when a watched act is amended, corrected, repealed or
gets a new consolidated version), or both. Matching runs hourly; deliveries
cost no credits.
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import math
import re
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from lexapi._json import strict_loads, too_deep
from lexapi._paging import DeliveryWalk
from lexapi._payloads import drop_none, seg, webhook_create_body, webhook_update_body
from lexapi._response import APIResponse
from lexapi.models import (
    WebhookDeliveriesResponse,
    WebhookDelivery,
    WebhookListResponse,
    WebhookResponse,
    WebhookTestResponse,
)

if TYPE_CHECKING:  # pragma: no cover
    from lexapi.async_client import AsyncLexAPI
    from lexapi.client import LexAPI

_DELIVERIES_PAGE_SIZE = 50

#: Default replay window for :func:`verify_webhook` (seconds).
DEFAULT_TOLERANCE_SECONDS = 300


#: Why a delivery failed verification (``WebhookSignatureError.reason``).
WebhookFailureReason = Literal[
    "missing_secret",
    "missing_signature",
    "invalid_signature",
    "malformed_body",
    "stale_timestamp",
]


class WebhookSignatureError(ValueError):
    """A delivery failed verification, or a helper was given no secret.

    :attr:`reason` says which check failed, in the order
    :func:`verify_webhook` runs them: ``missing_secret`` (a configuration
    error: the secret is empty), ``missing_signature`` (no header),
    ``invalid_signature``, ``malformed_body`` (not strict UTF-8 JSON, or not
    a JSON object), ``stale_timestamp`` (the signed ``timestamp`` is missing,
    malformed or outside the replay window).
    """

    def __init__(self, message: str, reason: str = "invalid_signature") -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (str(self), self.reason))


def _as_bytes(value: bytes | bytearray | memoryview | str) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


def _secret_bytes(secret: str | bytes) -> bytes:
    """The HMAC key. An empty secret, or a ``str`` secret that cannot be
    encoded as UTF-8 (a lone surrogate), is a configuration error
    (``missing_secret``); the error never holds the secret. A secret that
    is neither ``str`` nor bytes-like raises ``TypeError``."""
    if isinstance(secret, str):
        try:
            key = secret.encode("utf-8")
        except UnicodeEncodeError:
            key = None  # raised below, outside the except: no chained error
        if key is None:
            raise WebhookSignatureError(
                "the webhook secret cannot be encoded as UTF-8", "missing_secret"
            )
    elif isinstance(secret, (bytes, bytearray, memoryview)):
        key = bytes(secret)
    else:
        raise TypeError(f"secret must be str or bytes, not {type(secret).__name__}")
    if not key:
        raise WebhookSignatureError("the webhook secret is empty", "missing_secret")
    return key


#: Exactly 64 hexadecimal digits, after trimming.
_HEX_DIGEST = re.compile(r"[0-9a-fA-F]{64}")


def compute_signature(payload: bytes | bytearray | memoryview | str, secret: str | bytes) -> str:
    """The ``X-Webhook-Signature`` LexAPI sends: hex HMAC-SHA256 of the raw
    request body, keyed with the webhook ``secret``.

    An empty secret raises :class:`WebhookSignatureError`
    (``reason="missing_secret"``). A ``str`` payload is signed as its UTF-8
    bytes; one that has none (a lone surrogate) raises ``UnicodeEncodeError``
    and no signature is returned."""
    key = _secret_bytes(secret)
    return hmac.new(key, _as_bytes(payload), hashlib.sha256).hexdigest()


def verify_signature(
    payload: bytes | bytearray | memoryview | str,
    signature: str | None,
    secret: str | bytes,
) -> bool:
    """Timing-safe check of an ``X-Webhook-Signature`` header.

    Pass the **raw request body bytes exactly as received** — parsing and
    re-serialising the JSON changes the bytes and breaks the digest. Leading
    and trailing spaces, tabs, carriage returns and line feeds are removed
    from the header, and nothing else; what remains must be exactly 64
    hexadecimal digits (either case). A ``str`` payload that cannot be
    encoded as UTF-8 has no bytes to verify: ``False``. An empty secret
    raises :class:`WebhookSignatureError` (``reason="missing_secret"``).
    """
    key = _secret_bytes(secret)
    if not signature:
        return False
    candidate = signature.strip(" \t\r\n")
    if not _HEX_DIGEST.fullmatch(candidate):
        return False
    try:
        body = _as_bytes(payload)
    except UnicodeEncodeError:  # a str payload with a lone surrogate: no bytes
        return False
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), candidate.lower().encode("ascii"))


#: ``YYYY-MM-DDTHH:MM:SS[.fraction]`` then ``Z`` or ``±HH:MM`` (ASCII only).
_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]+))?(?:(Z)|([+-])([0-9]{2}):([0-9]{2}))"
)


def _signed_time(stamp: Any) -> int | None:
    """Whole microseconds since the epoch of a signed ``timestamp`` (fraction
    digits beyond the sixth are ignored: truncated, not rounded), or ``None``
    when it is not a string in the required form or names a time that does
    not exist (years 0001–9999)."""
    if not isinstance(stamp, str):
        return None
    match = _TIMESTAMP.fullmatch(stamp)
    if match is None:
        return None
    year, month, day, hh, mm, ss, fraction, zulu, sign, oh, om = match.groups()
    offset_minutes = 0
    if not zulu:
        if int(oh) > 23 or int(om) > 59:
            return None
        offset_minutes = (int(oh) * 60 + int(om)) * (1 if sign == "+" else -1)
    try:
        when = datetime(int(year), int(month), int(day), int(hh), int(mm), int(ss))
    except ValueError:
        return None
    seconds = calendar.timegm(when.timetuple()) - offset_minutes * 60
    micros = int((fraction or "")[:6].ljust(6, "0"))
    return seconds * 1_000_000 + micros


def _micros(seconds: float) -> int:
    """Seconds to whole microseconds, rounded to the nearest (halves up)."""
    return math.floor(seconds * 1_000_000 + 0.5)


def _within(distance_us: int, tolerance: float) -> bool:
    """Whether a distance in microseconds is inside the window (the edge
    included); the window is rounded to the nearest microsecond. An
    infinite window holds everything, a negative one nothing."""
    if tolerance < 0:
        return False
    if math.isinf(tolerance):
        return True
    return distance_us <= _micros(tolerance)


def verify_webhook(
    payload: bytes | bytearray | memoryview | str,
    signature: str | None,
    secret: str | bytes,
    *,
    tolerance: float | None = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    """Verify a delivery and return the parsed event.

    Checks, in this order, each failing with :class:`WebhookSignatureError`
    and its :attr:`~WebhookSignatureError.reason`: the secret
    (``missing_secret``), a missing or empty header (``missing_signature``),
    the signature (``invalid_signature``), the body — strict UTF-8 JSON
    whose value is an object (``malformed_body``) — and, unless
    ``tolerance`` is ``None``, the signed ``timestamp``
    (``stale_timestamp``): it must be ``YYYY-MM-DDTHH:MM:SS[.fraction]``
    followed by ``Z`` or ``±HH:MM``, at most ``tolerance`` seconds from
    ``now``. The signature covers only the body and there is no nonce, so a
    replayed delivery would otherwise verify again. Retries are re-signed
    with a fresh ``timestamp``; dedupe side effects on
    ``event["delivery"]["id"]`` (stable across retries, also sent as
    ``X-Webhook-Delivery``). ``tolerance`` must not be NaN (``ValueError``).

    Events: ``documents.new``, ``watchlist.changes`` and ``webhook.test``.
    """
    if tolerance is not None and math.isnan(tolerance):
        raise ValueError("tolerance must be a number of seconds, not NaN")
    _secret_bytes(secret)
    if not signature:
        raise WebhookSignatureError("missing webhook signature", "missing_signature")
    if not verify_signature(payload, signature, secret):
        raise WebhookSignatureError("invalid webhook signature", "invalid_signature")
    body = _as_bytes(payload)
    try:
        # Strict UTF-8 (one leading byte order mark allowed), as for every
        # response body: the parser never picks an encoding itself.
        text = body.decode("utf-8-sig")
        if too_deep(text):
            raise ValueError("nested too deeply")
        event = strict_loads(text)
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError is a ValueError
        raise WebhookSignatureError("webhook body is not JSON", "malformed_body") from exc
    if not isinstance(event, dict):
        raise WebhookSignatureError("webhook body is not a JSON object", "malformed_body")
    if tolerance is not None:
        stamp = event.get("timestamp")
        if not isinstance(stamp, str):
            raise WebhookSignatureError("webhook event carries no timestamp", "stale_timestamp")
        sent = _signed_time(stamp)
        if sent is None:
            raise WebhookSignatureError(
                f"unparseable webhook timestamp {stamp!r}", "stale_timestamp"
            )
        current = _micros(time.time() if now is None else now)
        if not _within(abs(current - sent), tolerance):
            raise WebhookSignatureError(
                f"webhook timestamp {stamp} is outside the {tolerance:g}s tolerance",
                "stale_timestamp",
            )
    return event


class _DeleteResponse(APIResponse):
    """``DELETE /webhooks/{id}``: a 204 or an empty body is a result."""

    _allow_empty = True


class WebhooksResource:
    """Synchronous ``/webhooks`` operations (``client.webhooks``)."""

    def __init__(self, client: LexAPI) -> None:
        self._client = client

    def list(self) -> WebhookListResponse:
        """GET /webhooks — every webhook owned by the caller, newest first.

        Secrets are never returned here — only on :meth:`create`.
        """
        return self._client._request("GET", "/webhooks", cast=WebhookListResponse, free=True)

    def create(
        self,
        name: str,
        url: str,
        search_criteria: dict[str, Any] | None = None,
        *,
        watch_celex: Sequence[str] | None = None,
        secret: str | None = None,
    ) -> WebhookResponse:
        """POST /webhooks — subscribe ``url`` to new documents matching
        ``search_criteria`` and/or to changes of the acts in ``watch_celex``.

        Args:
            name: label shown in the dashboard and in payloads.
            url: HTTPS endpoint LexAPI POSTs deliveries to.
            search_criteria: ``documents.new`` filters in the /search
                vocabulary (``query`` matches title words, ``documentType``,
                ``author``, ``subdomain``, ``dateFrom``/``dateTo``/``year``).
                ``{}`` matches every new EU-law document. Unsupported filters
                (``procedure``, boolean operators, ...) are refused with 400.
            watch_celex: base-act CELEX numbers (``32008R1272``) whose
                amendments, corrigenda, repeals and new consolidated versions
                fire ``watchlist.changes``. Plan limit per webhook: STARTER
                100, PROFESSIONAL 500, BUSINESS 1000, SCALE 2000, ENTERPRISE 5000.
            secret: HMAC signing secret; generated (32-byte hex) when omitted.

        At least one of ``search_criteria`` / ``watch_celex`` is required.
        **Store ``response.webhook.secret`` now** — this is the only response
        that ever contains it. Rejected with 400 (reasons in ``err.details``)
        for invalid criteria, an over-limit watch list, or at the tier's
        webhook cap (FREE 0, STARTER 2, PROFESSIONAL 10, BUSINESS 50,
        SCALE/ENTERPRISE 100).
        """
        body = webhook_create_body(
            name, url, search_criteria, watch_celex=watch_celex, secret=secret
        )
        return self._client._request("POST", "/webhooks", json=body, cast=WebhookResponse)

    def get(self, webhook_id: str) -> WebhookResponse:
        """GET /webhooks/{id} — full details incl. up to 20 recent deliveries."""
        return self._client._request(
            "GET", f"/webhooks/{seg(webhook_id)}", cast=WebhookResponse, free=True
        )

    def update(
        self,
        webhook_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        search_criteria: dict[str, Any] | None = None,
        watch_celex: Sequence[str] | None = None,
        status: str | None = None,
        remove_search_criteria: bool = False,
    ) -> WebhookResponse:
        """PUT /webhooks/{id} — partial update; only passed fields change.

        ``watch_celex`` replaces the whole watch list. ``remove_search_criteria
        =True`` drops the criteria (the webhook must keep a watch list).
        ``status`` is ``ACTIVE`` / ``PAUSED`` / ``FAILED``; ``"ACTIVE"`` also
        resets ``consecutiveFailures`` — that's how you re-enable a webhook
        the system set to ``FAILED`` — and re-queues its deliveries from the
        last 7 days that ran out of retries (``response.requeued_deliveries``).
        The response carries summary fields only.
        """
        return self._client._request(
            "PUT",
            f"/webhooks/{seg(webhook_id)}",
            json=webhook_update_body(
                name=name,
                url=url,
                search_criteria=search_criteria,
                watch_celex=watch_celex,
                status=status,
                remove_search_criteria=remove_search_criteria,
            ),
            cast=WebhookResponse,
        )

    def delete(self, webhook_id: str) -> None:
        """DELETE /webhooks/{id} — permanent, cascades to delivery history.

        Returns ``None`` on success; re-deletes raise
        :class:`~lexapi.NotFoundError`.
        """
        self._client._request("DELETE", f"/webhooks/{seg(webhook_id)}", cast=_DeleteResponse)

    def test(self, webhook_id: str) -> WebhookTestResponse:
        """POST /webhooks/{id}/test — synchronously deliver a ``webhook.test``
        payload (HMAC-SHA256 signed via ``X-Webhook-Signature``; 10 s timeout,
        redirects not followed).

        A failed upstream delivery is still HTTP 200 — check
        ``response.ok`` and ``response.delivery.error_message``. Test
        deliveries are not recorded in the delivery history.
        """
        return self._client._request(
            "POST", f"/webhooks/{seg(webhook_id)}/test", cast=WebhookTestResponse
        )

    def deliveries(
        self, webhook_id: str, *, limit: int | None = None, offset: int | None = None
    ) -> WebhookDeliveriesResponse:
        """GET /webhooks/{id}/deliveries — one page of delivery history."""
        return self._client._request(
            "GET",
            f"/webhooks/{seg(webhook_id)}/deliveries",
            params=drop_none({"limit": limit, "offset": offset}),
            cast=WebhookDeliveriesResponse,
            free=True,
        )

    def iter_deliveries(
        self, webhook_id: str, *, page_size: int = _DELIVERIES_PAGE_SIZE
    ) -> Iterator[WebhookDelivery]:
        """Iterate the full delivery history, newest first, walking offset pages.

        The API pages by ``limit``/``offset`` over ``createdAt desc`` and has
        no stable cursor, so a delivery created while you iterate shifts
        later pages by one. Each delivery is therefore yielded at most once
        (deduplicated on ``id``); deliveries created after the walk began may
        be missed, and pruned ones (history is kept 30 days) can shift a row
        past a page boundary. Without a ``total``, the walk raises
        :class:`~lexapi.InvalidResponseError` on the third page in a row that
        brings no unseen delivery id: the server is not advancing.
        Deliveries already yielded stay valid; the page that showed it is on
        the error's ``body``. Webhook reads cost no credits.
        """
        walk = DeliveryWalk(page_size)
        while True:
            page = self.deliveries(webhook_id, limit=walk.page_size, offset=walk.offset)
            deliveries = page.deliveries
            if not deliveries:
                return
            walk.check(page._map, [delivery.id for delivery in deliveries])
            for delivery in deliveries:
                key = delivery.id
                if key is not None:
                    if key in walk.seen:
                        continue
                    walk.seen.add(key)
                yield delivery
            if walk.advance(page._map, len(deliveries)):
                return


class AsyncWebhooksResource:
    """Asynchronous ``/webhooks`` operations (``client.webhooks``)."""

    def __init__(self, client: AsyncLexAPI) -> None:
        self._client = client

    async def list(self) -> WebhookListResponse:
        """GET /webhooks — see :meth:`WebhooksResource.list`."""
        return await self._client._request("GET", "/webhooks", cast=WebhookListResponse, free=True)

    async def create(
        self,
        name: str,
        url: str,
        search_criteria: dict[str, Any] | None = None,
        *,
        watch_celex: Sequence[str] | None = None,
        secret: str | None = None,
    ) -> WebhookResponse:
        """POST /webhooks — see :meth:`WebhooksResource.create`."""
        body = webhook_create_body(
            name, url, search_criteria, watch_celex=watch_celex, secret=secret
        )
        return await self._client._request("POST", "/webhooks", json=body, cast=WebhookResponse)

    async def get(self, webhook_id: str) -> WebhookResponse:
        """GET /webhooks/{id} — see :meth:`WebhooksResource.get`."""
        return await self._client._request(
            "GET", f"/webhooks/{seg(webhook_id)}", cast=WebhookResponse, free=True
        )

    async def update(
        self,
        webhook_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        search_criteria: dict[str, Any] | None = None,
        watch_celex: Sequence[str] | None = None,
        status: str | None = None,
        remove_search_criteria: bool = False,
    ) -> WebhookResponse:
        """PUT /webhooks/{id} — see :meth:`WebhooksResource.update`."""
        return await self._client._request(
            "PUT",
            f"/webhooks/{seg(webhook_id)}",
            json=webhook_update_body(
                name=name,
                url=url,
                search_criteria=search_criteria,
                watch_celex=watch_celex,
                status=status,
                remove_search_criteria=remove_search_criteria,
            ),
            cast=WebhookResponse,
        )

    async def delete(self, webhook_id: str) -> None:
        """DELETE /webhooks/{id} — see :meth:`WebhooksResource.delete`."""
        await self._client._request("DELETE", f"/webhooks/{seg(webhook_id)}", cast=_DeleteResponse)

    async def test(self, webhook_id: str) -> WebhookTestResponse:
        """POST /webhooks/{id}/test — see :meth:`WebhooksResource.test`."""
        return await self._client._request(
            "POST", f"/webhooks/{seg(webhook_id)}/test", cast=WebhookTestResponse
        )

    async def deliveries(
        self, webhook_id: str, *, limit: int | None = None, offset: int | None = None
    ) -> WebhookDeliveriesResponse:
        """GET /webhooks/{id}/deliveries — see :meth:`WebhooksResource.deliveries`."""
        return await self._client._request(
            "GET",
            f"/webhooks/{seg(webhook_id)}/deliveries",
            params=drop_none({"limit": limit, "offset": offset}),
            cast=WebhookDeliveriesResponse,
            free=True,
        )

    async def iter_deliveries(
        self, webhook_id: str, *, page_size: int = _DELIVERIES_PAGE_SIZE
    ) -> AsyncIterator[WebhookDelivery]:
        """Async twin of :meth:`WebhooksResource.iter_deliveries` (same
        deduplication and paging caveats)."""
        walk = DeliveryWalk(page_size)
        while True:
            page = await self.deliveries(webhook_id, limit=walk.page_size, offset=walk.offset)
            deliveries = page.deliveries
            if not deliveries:
                return
            walk.check(page._map, [delivery.id for delivery in deliveries])
            for delivery in deliveries:
                key = delivery.id
                if key is not None:
                    if key in walk.seen:
                        continue
                    walk.seen.add(key)
                yield delivery
            if walk.advance(page._map, len(deliveries)):
                return
