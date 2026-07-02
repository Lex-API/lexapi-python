"""Webhooks CRUD + test + deliveries (mocked HTTP)."""

import asyncio
import json

import httpx
import pytest

from lexapi import AsyncLexAPI, LexAPI, NotFoundError

WEBHOOK = {
    "id": "cm5abc123",
    "name": "New CJEU judgments",
    "url": "https://example.com/webhooks/lexapi",
    "status": "ACTIVE",
    "searchCriteria": {"documentType": "judgment", "author": ["court-of-justice"]},
    "lastChecked": None,
    "lastTriggered": None,
    "consecutiveFailures": 0,
    "deliveryCount": 7,
    "createdAt": "2026-06-01T00:00:00.000Z",
}

DELIVERY = {
    "id": "dlv_1",
    "status": "SUCCESS",
    "payload": {"event": "webhook.test"},
    "responseStatus": 200,
    "responseBody": "ok",
    "errorMessage": None,
    "attemptCount": 1,
    "nextRetryAt": None,
    "deliveredAt": "2026-06-02T00:00:00.000Z",
    "createdAt": "2026-06-02T00:00:00.000Z",
}


def capture_client(response_json, status=200):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content) if request.content else None
        if response_json is None:
            return httpx.Response(status)
        return httpx.Response(status, json=response_json)

    client = LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


def test_list_webhooks():
    client, seen = capture_client({"success": True, "count": 1, "webhooks": [WEBHOOK]})
    with client:
        resp = client.webhooks.list()

    assert seen["method"] == "GET"
    assert seen["path"] == "/api/v1/webhooks"
    assert resp.count == 1
    hook = resp.webhooks[0]
    assert hook.id == "cm5abc123"
    assert hook.status == "ACTIVE"
    assert hook.delivery_count == 7
    assert hook.secret is None  # never returned by list
    assert hook.search_criteria["documentType"] == "judgment"


def test_create_webhook_returns_secret_once():
    created = dict(WEBHOOK)
    created["secret"] = "a" * 64
    client, seen = capture_client(
        {"success": True, "message": "created", "webhook": created}, status=201
    )
    with client:
        resp = client.webhooks.create(
            "New CJEU judgments",
            "https://example.com/webhooks/lexapi",
            {"documentType": "judgment", "author": ["court-of-justice"]},
        )

    assert seen["method"] == "POST"
    assert seen["body"] == {
        "name": "New CJEU judgments",
        "url": "https://example.com/webhooks/lexapi",
        "searchCriteria": {"documentType": "judgment", "author": ["court-of-justice"]},
    }
    assert resp.webhook.secret == "a" * 64


def test_create_webhook_with_own_secret():
    client, seen = capture_client({"success": True, "webhook": WEBHOOK}, status=201)
    with client:
        client.webhooks.create("n", "https://e.com", {"query": "x"}, secret="s3cret")
    assert seen["body"]["secret"] == "s3cret"


def test_get_webhook_with_recent_deliveries():
    detail = dict(WEBHOOK)
    detail["maxRetries"] = 3
    detail["recentDeliveries"] = [DELIVERY]
    client, seen = capture_client({"success": True, "webhook": detail})
    with client:
        resp = client.webhooks.get("cm5abc123")

    assert seen["path"] == "/api/v1/webhooks/cm5abc123"
    assert resp.webhook.max_retries == 3
    assert resp.webhook.recent_deliveries[0].response_status == 200


def test_update_webhook_partial_body():
    client, seen = capture_client({"success": True, "webhook": WEBHOOK})
    with client:
        client.webhooks.update("cm5abc123", status="ACTIVE")

    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/v1/webhooks/cm5abc123"
    assert seen["body"] == {"status": "ACTIVE"}


def test_delete_webhook_204_returns_none():
    client, seen = capture_client(None, status=204)
    with client:
        assert client.webhooks.delete("cm5abc123") is None
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/api/v1/webhooks/cm5abc123"


def test_delete_webhook_repeat_raises_not_found():
    client, _ = capture_client(
        {"success": False, "error": {"code": "NOT_FOUND", "message": "gone"}}, status=404
    )
    with client:
        with pytest.raises(NotFoundError):
            client.webhooks.delete("cm5abc123")


def test_test_webhook_failure_is_not_an_exception():
    body = {
        "success": False,
        "message": "Delivery failed",
        "delivery": {
            "status": "FAILED",
            "responseStatus": 500,
            "responseBody": "oops",
            "errorMessage": "upstream returned 500",
        },
    }
    client, seen = capture_client(body)  # HTTP 200 per spec
    with client:
        resp = client.webhooks.test("cm5abc123")

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/webhooks/cm5abc123/test"
    assert resp.ok is False
    assert resp.delivery.status == "FAILED"
    assert resp.delivery.error_message == "upstream returned 500"


def test_deliveries_page():
    body = {
        "success": True,
        "total": 120,
        "limit": 2,
        "offset": 4,
        "deliveries": [DELIVERY, dict(DELIVERY, id="dlv_2", status="FAILED")],
    }
    client, seen = capture_client(body)
    with client:
        resp = client.webhooks.deliveries("cm5abc123", limit=2, offset=4)

    assert seen["path"] == "/api/v1/webhooks/cm5abc123/deliveries"
    assert seen["params"] == {"limit": "2", "offset": "4"}
    assert resp.total == 120
    assert [d.id for d in resp.deliveries] == ["dlv_1", "dlv_2"]


def test_iter_deliveries_walks_pages():
    all_deliveries = [dict(DELIVERY, id=f"dlv_{i}") for i in range(5)]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 50))
        calls.append((offset, limit))
        page = all_deliveries[offset : offset + limit]
        return httpx.Response(
            200,
            json={
                "success": True,
                "total": len(all_deliveries),
                "limit": limit,
                "offset": offset,
                "deliveries": page,
            },
        )

    with LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler)) as client:
        ids = [d.id for d in client.webhooks.iter_deliveries("cm5abc123", page_size=2)]

    assert ids == ["dlv_0", "dlv_1", "dlv_2", "dlv_3", "dlv_4"]
    assert calls == [(0, 2), (2, 2), (4, 2)]


def test_async_webhooks_parity():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"success": True, "count": 1, "webhooks": [WEBHOOK]})

    async def run():
        async with AsyncLexAPI(
            api_key="lex_test_key", transport=httpx.MockTransport(handler)
        ) as client:
            listed = await client.webhooks.list()
            deleted = await client.webhooks.delete("cm5abc123")
            return listed, deleted

    listed, deleted = asyncio.run(run())
    assert listed.webhooks[0].id == "cm5abc123"
    assert deleted is None


def test_async_iter_deliveries():
    all_deliveries = [dict(DELIVERY, id=f"dlv_{i}") for i in range(3)]

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 50))
        return httpx.Response(
            200,
            json={
                "success": True,
                "total": len(all_deliveries),
                "deliveries": all_deliveries[offset : offset + limit],
            },
        )

    async def run():
        async with AsyncLexAPI(
            api_key="lex_test_key", transport=httpx.MockTransport(handler)
        ) as client:
            return [
                d.id async for d in client.webhooks.iter_deliveries("cm5abc123", page_size=2)
            ]

    assert asyncio.run(run()) == ["dlv_0", "dlv_1", "dlv_2"]
