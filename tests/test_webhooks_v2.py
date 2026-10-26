"""Webhook watch lists, criteria errors, retry policy, re-queue count, the
real DELETE response, and delivery signature verification."""

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    LexAPI,
    WebhookSignatureError,
    compute_signature,
    verify_signature,
    verify_webhook,
)

DETAIL = {
    "id": "cm5abc123",
    "name": "Chemicals compliance watch",
    "url": "https://example.com/webhooks/lexapi",
    "status": "ACTIVE",
    "searchCriteria": None,
    "criteriaErrors": [],
    "watchCelex": ["32006R1907", "32008R1272"],
    "lastChecked": "2026-10-26T07:00:00.000Z",
    "lastTriggered": None,
    "consecutiveFailures": 0,
    "retryPolicy": {
        "maxAttempts": 7,
        "windowHours": 24,
        "delaysMinutes": [1, 5, 30, 120, 360, 900],
    },
    "createdAt": "2026-10-01T00:00:00.000Z",
    "updatedAt": "2026-10-02T00:00:00.000Z",
    "recentDeliveries": [
        {
            "id": "dlv_1",
            "webhookId": "cm5abc123",
            "status": "SUCCESS",
            "payload": {"event": "watchlist.changes"},
            "responseStatus": 200,
            "attemptCount": 1,
            "createdAt": "2026-10-05T11:35:50.493Z",
        }
    ],
}


def capture_client(response_json, status=200, client_cls=LexAPI):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json)

    client = client_cls(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


def test_create_watch_list_only():
    created = {
        "success": True,
        "message": "Webhook created successfully",
        "webhook": {
            "id": "cm5abc123",
            "name": "Chemicals compliance watch",
            "url": "https://example.com/webhooks/lexapi",
            "secret": "7f9a",
            "status": "ACTIVE",
            "searchCriteria": None,
            "watchCelex": ["32006R1907", "32008R1272"],
            "createdAt": "2026-10-01T00:00:00.000Z",
        },
    }
    client, seen = capture_client(created, status=201)
    with client:
        resp = client.webhooks.create(
            "Chemicals compliance watch",
            "https://example.com/webhooks/lexapi",
            watch_celex=("32006R1907", "32008R1272"),
        )
    assert seen["body"] == {
        "name": "Chemicals compliance watch",
        "url": "https://example.com/webhooks/lexapi",
        "watchCelex": ["32006R1907", "32008R1272"],
    }
    hook = resp.webhook
    assert hook.secret == "7f9a"
    assert hook.watch_celex == ["32006R1907", "32008R1272"]
    assert hook.search_criteria == {}
    assert hook.has_search_criteria is False


def test_create_criteria_and_watch_list():
    client, seen = capture_client({"success": True, "webhook": {}}, status=201)
    with client:
        client.webhooks.create(
            "both", "https://e.com/h", {"documentType": "regulation"}, watch_celex=["32008R1272"]
        )
    assert seen["body"]["searchCriteria"] == {"documentType": "regulation"}
    assert seen["body"]["watchCelex"] == ["32008R1272"]


def test_create_positional_criteria_still_works():
    client, seen = capture_client({"success": True, "webhook": {}}, status=201)
    with client:
        client.webhooks.create("n", "https://e.com/h", {"documentType": "judgment"}, secret="s")
    assert seen["body"] == {
        "name": "n",
        "url": "https://e.com/h",
        "searchCriteria": {"documentType": "judgment"},
        "secret": "s",
    }


def test_get_detail_fields():
    client, _ = capture_client({"success": True, "webhook": DETAIL})
    with client:
        hook = client.webhooks.get("cm5abc123").webhook
    assert hook.criteria_errors == []
    assert hook.watch_celex == ["32006R1907", "32008R1272"]
    assert hook.retry_policy.max_attempts == 7
    assert hook.retry_policy.window_hours == 24
    assert hook.retry_policy.delays_minutes == [1, 5, 30, 120, 360, 900]
    assert hook.recent_deliveries[0].webhook_id == "cm5abc123"
    assert hook.updated_at == "2026-10-02T00:00:00.000Z"


def test_list_criteria_errors():
    item = {
        "id": "w1",
        "searchCriteria": {"author": ["commission", "ema"]},
        "criteriaErrors": ['author "ema" is ignored'],
        "watchCelex": [],
        "deliveryCount": 3,
    }
    client, _ = capture_client({"success": True, "count": 1, "webhooks": [item]})
    with client:
        hook = client.webhooks.list().webhooks[0]
    assert hook.criteria_errors == ['author "ema" is ignored']
    assert hook.has_search_criteria is True
    assert hook.delivery_count == 3
    assert hook.retry_policy is None


def test_update_watch_list_and_requeue():
    client, seen = capture_client(
        {
            "success": True,
            "message": "Webhook updated successfully",
            "webhook": {
                "id": "w1",
                "status": "ACTIVE",
                "watchCelex": ["32008R1272"],
                "criteriaErrors": [],
            },
            "requeuedDeliveries": 4,
        }
    )
    with client:
        resp = client.webhooks.update("w1", status="ACTIVE", watch_celex=["32008R1272"])
    assert seen["method"] == "PUT"
    assert seen["body"] == {"status": "ACTIVE", "watchCelex": ["32008R1272"]}
    assert resp.requeued_deliveries == 4
    assert resp.webhook.watch_celex == ["32008R1272"]


def test_update_remove_criteria_sends_null():
    client, seen = capture_client({"success": True, "webhook": {}})
    with client:
        client.webhooks.update("w1", remove_search_criteria=True)
    assert seen["body"] == {"searchCriteria": None}


def test_update_empty_watch_list_is_sent():
    client, seen = capture_client({"success": True, "webhook": {}})
    with client:
        client.webhooks.update("w1", watch_celex=[])
    assert seen["body"] == {"watchCelex": []}


def test_update_rejects_conflicting_criteria_args():
    client, _ = capture_client({"success": True, "webhook": {}})
    with client:
        with pytest.raises(ValueError):
            client.webhooks.update("w1", search_criteria={"a": 1}, remove_search_criteria=True)


def test_update_without_requeue_field():
    client, _ = capture_client({"success": True, "webhook": {"name": "renamed"}})
    with client:
        assert client.webhooks.update("w1", name="renamed").requeued_deliveries is None


def test_delete_200_json_body_returns_none():
    # The server answers 200 + {success, message} (the spec says 204).
    client, seen = capture_client({"success": True, "message": "Webhook deleted successfully"})
    with client:
        assert client.webhooks.delete("w1") is None
    assert seen["method"] == "DELETE"


# ── signature verification ─────────────────────────────────────────────

SECRET = "whsec_test"
EVENT = {
    "event": "watchlist.changes",
    "timestamp": "2026-10-05T11:35:50.493Z",
    "webhook": {"id": "cm5abc123", "name": "Chemicals compliance watch"},
    "delivery": {"id": "d1e2", "attempt": 1},
    "data": {"count": 0, "changes": []},
}
RAW = json.dumps(EVENT, separators=(",", ":")).encode()
SENT_AT = 1791200150.493  # 2026-10-05T11:35:50.493Z


def test_compute_signature_matches_spec():
    expected = hmac.new(SECRET.encode(), RAW, hashlib.sha256).hexdigest()
    assert compute_signature(RAW, SECRET) == expected
    assert compute_signature(RAW.decode(), SECRET.encode()) == expected


def test_verify_signature():
    sig = compute_signature(RAW, SECRET)
    assert verify_signature(RAW, sig, SECRET)
    assert verify_signature(RAW, sig.upper(), SECRET)
    assert not verify_signature(RAW, sig, "other")
    assert not verify_signature(RAW + b" ", sig, SECRET)  # re-serialised bodies fail
    assert not verify_signature(RAW, None, SECRET)
    assert not verify_signature(RAW, "", SECRET)


def test_verify_webhook_returns_event_within_tolerance():
    sig = compute_signature(RAW, SECRET)
    event = verify_webhook(RAW, sig, SECRET, now=SENT_AT + 60)
    assert event["delivery"]["id"] == "d1e2"


def test_verify_webhook_rejects_stale_and_bad_signature():
    sig = compute_signature(RAW, SECRET)
    with pytest.raises(WebhookSignatureError):
        verify_webhook(RAW, sig, SECRET, now=SENT_AT + 301)
    with pytest.raises(WebhookSignatureError):
        verify_webhook(RAW, "deadbeef", SECRET, now=SENT_AT)
    # tolerance=None skips the replay window
    assert verify_webhook(RAW, sig, SECRET, tolerance=None)["event"] == "watchlist.changes"


def test_verify_webhook_rejects_missing_timestamp():
    raw = json.dumps({"event": "webhook.test"}).encode()
    with pytest.raises(WebhookSignatureError):
        verify_webhook(raw, compute_signature(raw, SECRET), SECRET)
    assert issubclass(WebhookSignatureError, ValueError)


def test_async_watch_list_parity():
    async def run():
        client, seen = capture_client({"success": True, "webhook": DETAIL}, client_cls=AsyncLexAPI)
        async with client:
            await client.webhooks.create("w", "https://e.com/h", watch_celex=["32008R1272"])
            created = seen["body"]
            await client.webhooks.update("w1", remove_search_criteria=True, watch_celex=["X"])
            updated = seen["body"]
            hook = (await client.webhooks.get("w1")).webhook
        return created, updated, hook

    created, updated, hook = asyncio.run(run())
    assert created == {"name": "w", "url": "https://e.com/h", "watchCelex": ["32008R1272"]}
    assert updated == {"watchCelex": ["X"], "searchCriteria": None}
    assert hook.retry_policy.max_attempts == 7
