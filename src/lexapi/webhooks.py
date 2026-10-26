"""Webhooks resource namespace: ``client.webhooks.*``, plus signature
verification for the deliveries LexAPI POSTs to your endpoint.

A webhook needs ``search_criteria`` (``documents.new`` for each new
document matching the /search-vocabulary filters), ``watch_celex``
(``watchlist.changes`` when a watched act is amended, corrected, repealed or
gets a new consolidated version), or both. Matching runs hourly; deliveries
cost no credits.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from lexapi._payloads import drop_none, seg, webhook_create_body, webhook_update_body
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


class WebhookSignatureError(ValueError):
    """A delivery failed verification (bad signature, or a stale timestamp)."""


def _as_bytes(value: bytes | bytearray | memoryview | str) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


def compute_signature(payload: bytes | bytearray | memoryview | str, secret: str | bytes) -> str:
    """The ``X-Webhook-Signature`` LexAPI sends: hex HMAC-SHA256 of the raw
    request body, keyed with the webhook ``secret``."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(key, _as_bytes(payload), hashlib.sha256).hexdigest()


def verify_signature(
    payload: bytes | bytearray | memoryview | str,
    signature: str | None,
    secret: str | bytes,
) -> bool:
    """Timing-safe check of an ``X-Webhook-Signature`` header.

    Pass the **raw request body bytes exactly as received** — parsing and
    re-serialising the JSON changes the bytes and breaks the digest.
    """
    if not signature:
        return False
    candidate = signature.strip().lower()
    if not candidate.isascii():  # never a hex digest; compare_digest(str) would raise
        return False
    expected = compute_signature(payload, secret)
    return hmac.compare_digest(expected.encode("ascii"), candidate.encode("ascii"))


def verify_webhook(
    payload: bytes | bytearray | memoryview | str,
    signature: str | None,
    secret: str | bytes,
    *,
    tolerance: float | None = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    """Verify a delivery and return the parsed event.

    Checks the signature (raises :class:`WebhookSignatureError` on mismatch),
    then — unless ``tolerance`` is ``None`` — rejects an event whose signed
    ``timestamp`` is more than ``tolerance`` seconds from ``now``: the
    signature covers only the body and there is no nonce, so a replayed
    delivery would otherwise verify again. Retries are re-signed with a fresh
    ``timestamp``; dedupe side effects on ``event["delivery"]["id"]`` (stable
    across retries, also sent as ``X-Webhook-Delivery``).

    Events: ``documents.new``, ``watchlist.changes`` and ``webhook.test``.
    """
    if not verify_signature(payload, signature, secret):
        raise WebhookSignatureError("invalid webhook signature")
    try:
        event = json.loads(_as_bytes(payload))
    except ValueError as exc:
        raise WebhookSignatureError("webhook body is not JSON") from exc
    if not isinstance(event, dict):
        raise WebhookSignatureError("webhook body is not a JSON object")
    if tolerance is not None:
        stamp = event.get("timestamp")
        if not isinstance(stamp, str):
            raise WebhookSignatureError("webhook event carries no timestamp")
        try:
            sent = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError as exc:
            raise WebhookSignatureError(f"unparseable webhook timestamp {stamp!r}") from exc
        current = time.time() if now is None else now
        if abs(current - sent) > tolerance:
            raise WebhookSignatureError(
                f"webhook timestamp {stamp} is outside the {tolerance:g}s tolerance"
            )
    return event


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
        self._client._request("DELETE", f"/webhooks/{seg(webhook_id)}")

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
        past a page boundary. Webhook reads cost no credits.
        """
        offset = 0
        seen: set[str] = set()
        while True:
            page = self.deliveries(webhook_id, limit=page_size, offset=offset)
            deliveries = page.deliveries
            for delivery in deliveries:
                key = delivery.id
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                yield delivery
            offset += len(deliveries)
            total = page.total
            if not deliveries or (total is not None and offset >= total):
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
        await self._client._request("DELETE", f"/webhooks/{seg(webhook_id)}")

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
        offset = 0
        seen: set[str] = set()
        while True:
            page = await self.deliveries(webhook_id, limit=page_size, offset=offset)
            deliveries = page.deliveries
            for delivery in deliveries:
                key = delivery.id
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                yield delivery
            offset += len(deliveries)
            total = page.total
            if not deliveries or (total is not None and offset >= total):
                return
