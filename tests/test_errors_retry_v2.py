"""Error-envelope and retry fixes in 0.2.0, modelled on the bodies the API
actually sends (server/middleware/*, server/controllers/*)."""

import httpx
import pytest

from conftest import FAST_RETRY, INFO_BODY
from lexapi import (
    AuthenticationError,
    CreditsExhaustedError,
    InternalServerError,
    InvalidParamsError,
    LexAPI,
    NotFoundError,
    RateLimitedError,
    TierForbiddenError,
    TimeoutError,
    UpstreamError,
)
from lexapi._retry import RetryConfig, is_quota_exhausted, retry_wait, server_wait_hint


def make_client(handler, **kwargs) -> LexAPI:
    kwargs.setdefault("retry_config", FAST_RETRY)
    return LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler), **kwargs)


def fixed(status, body, headers=None):
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(status, json=body, headers=headers or {})

    return handler, calls


# ── Legacy-shape bodies ────────────────────────────────────────────────


def test_legacy_validation_details_preserved():
    handler, _ = fixed(
        400,
        {
            "error": "Invalid webhook matching rules",
            "details": ['author "ema" never matches', "procedure is not supported"],
        },
    )
    with make_client(handler) as client:
        with pytest.raises(InvalidParamsError) as info:
            client.webhooks.create("x", "https://e.com/h", {"author": "ema"})
    assert info.value.details == ['author "ema" never matches', "procedure is not supported"]
    assert info.value.message == "Invalid webhook matching rules"


def test_legacy_401_shapes():
    for body in (
        {
            "error": "API key required",
            "message": "Please provide your API key in the x-api-key header",
        },
        {"error": "Invalid API key"},
        {"error": "API key has been deactivated"},
    ):
        handler, _ = fixed(401, body)
        with make_client(handler) as client:
            with pytest.raises(AuthenticationError) as info:
                client.get_info()
        assert info.value.code == "UNAUTHORIZED"
        assert info.value.status == 401


def test_legacy_tier_gate_upgrade_url():
    handler, _ = fixed(
        403,
        {
            "error": "Bulk export is a BUSINESS-tier feature",
            "message": "Upgrade your plan to export the LexAPI corpus in bulk.",
            "upgradeUrl": "/pricing",
        },
    )
    with make_client(handler) as client:
        with pytest.raises(TierForbiddenError) as info:
            client.export()
    assert info.value.upgrade_url == "/pricing"
    assert info.value.code == "TIER_FORBIDDEN"


def test_legacy_webhook_404():
    handler, _ = fixed(404, {"error": "Webhook not found"})
    with make_client(handler) as client:
        with pytest.raises(NotFoundError):
            client.webhooks.get("nope")


def test_semantic_upstream_legacy_502_and_504():
    handler, _ = fixed(502, {"error": "Upstream service error", "message": "try later"})
    with make_client(handler, max_retries=0) as client:
        with pytest.raises(UpstreamError):
            client.semantic_search("q")
    handler, _ = fixed(504, {"error": "Request timeout", "message": "too slow"})
    with make_client(handler, max_retries=0) as client:
        with pytest.raises(TimeoutError):
            client.semantic_search("q")


def test_cloudflare_524_maps_to_timeout_and_is_retried_for_get():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(524, text="<html>A timeout occurred</html>")
        return httpx.Response(200, json=INFO_BODY)

    with make_client(handler) as client:
        assert client.get_info()["service"] == "LexAPI"
    assert calls["count"] == 2

    handler, _ = fixed(524, {})
    with make_client(handler, max_retries=0) as client:
        with pytest.raises(TimeoutError) as info:
            client.get_info()
    assert info.value.status == 524


# ── 402 details ────────────────────────────────────────────────────────


def test_credits_exhausted_typed_details():
    handler, calls = fixed(
        402,
        {
            "success": False,
            "error": {
                "code": "CREDITS_EXHAUSTED",
                "message": "Credit pool exhausted",
                "details": {
                    "operation": "semantic_case_law",
                    "weight": 5,
                    "available": 2,
                    "included": 30000,
                    "topup": 0,
                    "used": 29998,
                    "resetsAt": "2026-07-01T00:00:00.000Z",
                },
            },
        },
    )
    with make_client(handler) as client:
        with pytest.raises(CreditsExhaustedError) as info:
            client.semantic_search("q")
    err = info.value
    assert calls["count"] == 1  # never retried
    assert (err.operation, err.weight, err.available) == ("semantic_case_law", 5, 2)
    assert (err.included, err.topup, err.used) == (30000, 0, 29998)
    assert err.resets_at == "2026-07-01T00:00:00.000Z"


def test_credits_exhausted_without_details():
    handler, _ = fixed(
        402, {"success": False, "error": {"code": "CREDITS_EXHAUSTED", "message": "x"}}
    )
    with make_client(handler) as client:
        with pytest.raises(CreditsExhaustedError) as info:
            client.get_info()
    assert info.value.operation is None and info.value.weight is None


# ── 429: rate limit vs quota ───────────────────────────────────────────


def test_rate_limit_429_is_retried_with_retry_after():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                429,
                json={"error": "Rate limit exceeded", "message": "120/min", "retryAfter": 0},
                headers={"Retry-After": "0", "RateLimit-Reset": "0"},
            )
        return httpx.Response(200, json=INFO_BODY)

    with make_client(handler) as client:
        client.get_info()
    assert calls["count"] == 2


def test_legacy_daily_quota_429_not_retried():
    body = {
        "error": "Daily quota exceeded",
        "message": "You've reached your daily limit of 1000 API calls. Resets at midnight UTC.",
        "usage": {"current": 1000, "limit": 1000, "resetsAt": "2026-10-27T00:00:00.000Z"},
    }
    handler, calls = fixed(429, body, headers={"RateLimit-Reset": "30"})
    with make_client(handler) as client:
        with pytest.raises(RateLimitedError) as info:
            client.get_info()
    assert calls["count"] == 1
    assert info.value.quota_exhausted is True
    assert info.value.resets_at == "2026-10-27T00:00:00.000Z"


def test_rate_limit_error_is_not_quota():
    handler, _ = fixed(429, {"error": "Rate limit exceeded", "retryAfter": 3})
    with make_client(handler, max_retries=0) as client:
        with pytest.raises(RateLimitedError) as info:
            client.get_info()
    assert info.value.quota_exhausted is False
    assert info.value.resets_at is None
    assert info.value.retry_after == 3


def test_is_quota_exhausted_helper():
    headers = httpx.Headers()
    assert is_quota_exhausted(429, headers, {"usage": {"resetsAt": "x"}})
    assert not is_quota_exhausted(
        429, httpx.Headers({"Retry-After": "5"}), {"usage": {"resetsAt": "x"}}
    )
    assert not is_quota_exhausted(429, headers, {"retryAfter": 5, "usage": {"resetsAt": "x"}})
    assert not is_quota_exhausted(503, headers, {"usage": {"resetsAt": "x"}})


# ── RateLimit-Reset only governs 429s ──────────────────────────────────


def test_ratelimit_reset_ignored_outside_429():
    headers = httpx.Headers({"RateLimit-Reset": "55"})
    assert server_wait_hint(headers, {"retryAfter": 40}, 502) is None
    assert server_wait_hint(headers, {}, 429) == 55
    assert server_wait_hint(headers, {}) == 55  # unknown status: legacy behaviour
    # Retry-After applies to any status
    assert server_wait_hint(httpx.Headers({"Retry-After": "2"}), {}, 503) == 2


def test_502_retry_uses_backoff_not_rate_window():
    # Real backoff numbers (T1): attempt 0 backoff is U(0, 0.5); the old
    # behaviour would have waited min(55, 30) = 30 s.
    config = RetryConfig(backoff_base=0.5, backoff_cap=30.0)
    response = httpx.Response(502, json={"error": "x"}, headers={"RateLimit-Reset": "55"})
    wait = retry_wait("GET", response, {"error": "x"}, 0, config)
    assert wait is not None and wait < 1


def test_error_retry_after_not_set_from_rate_window_on_5xx():
    handler, _ = fixed(
        500,
        {"success": False, "error": {"code": "INTERNAL", "message": "x"}},
        headers={"RateLimit-Reset": "55"},
    )
    with make_client(handler) as client:
        with pytest.raises(InternalServerError) as info:
            client.get_info()
    assert info.value.retry_after is None
