"""Webhooks resource namespace: ``client.webhooks.*``."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

from lexapi._payloads import drop_none
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


def _update_body(
    name: str | None, url: str | None, search_criteria: dict | None, status: str | None
) -> dict:
    return drop_none(
        {"name": name, "url": url, "searchCriteria": search_criteria, "status": status}
    )


class WebhooksResource:
    """Synchronous ``/webhooks`` operations (``client.webhooks``)."""

    def __init__(self, client: LexAPI) -> None:
        self._client = client

    def list(self) -> WebhookListResponse:
        """GET /webhooks — every webhook owned by the caller, newest first.

        Secrets are never returned here — only on :meth:`create`.
        """
        return self._client._request("GET", "/webhooks", cast=WebhookListResponse)

    def create(
        self,
        name: str,
        url: str,
        search_criteria: dict,
        *,
        secret: str | None = None,
    ) -> WebhookResponse:
        """POST /webhooks — subscribe ``url`` to documents matching
        ``search_criteria`` (same shape as a /search request body).

        If ``secret`` is omitted the server generates one; **store
        ``response.webhook.secret`` now** — this is the only response that
        ever contains it. Creation fails with 400 at the tier's webhook cap.
        """
        body = drop_none(
            {"name": name, "url": url, "searchCriteria": search_criteria, "secret": secret}
        )
        return self._client._request("POST", "/webhooks", json=body, cast=WebhookResponse)

    def get(self, webhook_id: str) -> WebhookResponse:
        """GET /webhooks/{id} — full details incl. up to 20 recent deliveries."""
        return self._client._request(
            "GET", f"/webhooks/{webhook_id}", cast=WebhookResponse
        )

    def update(
        self,
        webhook_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        search_criteria: dict | None = None,
        status: str | None = None,
    ) -> WebhookResponse:
        """PUT /webhooks/{id} — partial update; only passed fields change.

        ``status="ACTIVE"`` also resets ``consecutiveFailures`` to zero —
        that's how you re-enable a webhook the system auto-paused.
        """
        return self._client._request(
            "PUT",
            f"/webhooks/{webhook_id}",
            json=_update_body(name, url, search_criteria, status),
            cast=WebhookResponse,
        )

    def delete(self, webhook_id: str) -> None:
        """DELETE /webhooks/{id} — permanent, cascades to delivery history.

        Returns ``None`` on the 204 success; re-deletes raise
        :class:`~lexapi.NotFoundError`.
        """
        self._client._request("DELETE", f"/webhooks/{webhook_id}")

    def test(self, webhook_id: str) -> WebhookTestResponse:
        """POST /webhooks/{id}/test — synchronously deliver a ``webhook.test``
        payload (HMAC-SHA256 signed via ``X-Webhook-Signature``).

        A failed upstream delivery is still HTTP 200 — check
        ``response.ok`` and ``response.delivery.error_message``.
        """
        return self._client._request(
            "POST", f"/webhooks/{webhook_id}/test", cast=WebhookTestResponse
        )

    def deliveries(
        self, webhook_id: str, *, limit: int | None = None, offset: int | None = None
    ) -> WebhookDeliveriesResponse:
        """GET /webhooks/{id}/deliveries — one page of delivery history."""
        return self._client._request(
            "GET",
            f"/webhooks/{webhook_id}/deliveries",
            params=drop_none({"limit": limit, "offset": offset}),
            cast=WebhookDeliveriesResponse,
        )

    def iter_deliveries(
        self, webhook_id: str, *, page_size: int = _DELIVERIES_PAGE_SIZE
    ) -> Iterator[WebhookDelivery]:
        """Iterate the full delivery history, walking offset pages transparently."""
        offset = 0
        while True:
            page = self.deliveries(webhook_id, limit=page_size, offset=offset)
            deliveries = page.deliveries
            yield from deliveries
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
        return await self._client._request("GET", "/webhooks", cast=WebhookListResponse)

    async def create(
        self,
        name: str,
        url: str,
        search_criteria: dict,
        *,
        secret: str | None = None,
    ) -> WebhookResponse:
        """POST /webhooks — see :meth:`WebhooksResource.create`."""
        body = drop_none(
            {"name": name, "url": url, "searchCriteria": search_criteria, "secret": secret}
        )
        return await self._client._request(
            "POST", "/webhooks", json=body, cast=WebhookResponse
        )

    async def get(self, webhook_id: str) -> WebhookResponse:
        """GET /webhooks/{id} — see :meth:`WebhooksResource.get`."""
        return await self._client._request(
            "GET", f"/webhooks/{webhook_id}", cast=WebhookResponse
        )

    async def update(
        self,
        webhook_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        search_criteria: dict | None = None,
        status: str | None = None,
    ) -> WebhookResponse:
        """PUT /webhooks/{id} — see :meth:`WebhooksResource.update`."""
        return await self._client._request(
            "PUT",
            f"/webhooks/{webhook_id}",
            json=_update_body(name, url, search_criteria, status),
            cast=WebhookResponse,
        )

    async def delete(self, webhook_id: str) -> None:
        """DELETE /webhooks/{id} — see :meth:`WebhooksResource.delete`."""
        await self._client._request("DELETE", f"/webhooks/{webhook_id}")

    async def test(self, webhook_id: str) -> WebhookTestResponse:
        """POST /webhooks/{id}/test — see :meth:`WebhooksResource.test`."""
        return await self._client._request(
            "POST", f"/webhooks/{webhook_id}/test", cast=WebhookTestResponse
        )

    async def deliveries(
        self, webhook_id: str, *, limit: int | None = None, offset: int | None = None
    ) -> WebhookDeliveriesResponse:
        """GET /webhooks/{id}/deliveries — see :meth:`WebhooksResource.deliveries`."""
        return await self._client._request(
            "GET",
            f"/webhooks/{webhook_id}/deliveries",
            params=drop_none({"limit": limit, "offset": offset}),
            cast=WebhookDeliveriesResponse,
        )

    async def iter_deliveries(
        self, webhook_id: str, *, page_size: int = _DELIVERIES_PAGE_SIZE
    ) -> AsyncIterator[WebhookDelivery]:
        """Async-iterate the full delivery history across offset pages."""
        offset = 0
        while True:
            page = await self.deliveries(webhook_id, limit=page_size, offset=offset)
            deliveries = page.deliveries
            for delivery in deliveries:
                yield delivery
            offset += len(deliveries)
            total = page.total
            if not deliveries or (total is not None and offset >= total):
                return
