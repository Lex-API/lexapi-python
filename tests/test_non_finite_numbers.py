"""Numbers too large for a float (``1e999``) in the credit, usage and error
figures read as ``None`` instead of raising; finite values read as before."""

import asyncio

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    CreditsExhaustedError,
    LexAPI,
    NationalAllowanceExhaustedError,
    TimeoutError,
)

LOOPBACK = "http://127.0.0.1:9/api/v1"
HUGE = "1e999"  # parsed as float('inf')


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def _transport(status, raw):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(
            status, content=raw.encode(), headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


def run(mode, status, raw, call):
    transport = _transport(status, raw)
    if mode == "sync":
        with LexAPI("lex_test_key", base_url=LOOPBACK, max_retries=0, transport=transport) as c:
            return call(c)

    async def go():
        async with AsyncLexAPI(
            "lex_test_key", base_url=LOOPBACK, max_retries=0, transport=transport
        ) as c:
            return await call(c)

    return asyncio.run(go())


INFO_RAW = (
    '{"service": "LexAPI", "version": "2.29.0",'
    ' "subscription": {"tier": "FREE", "maxResults": @, "monthlyCredits": 2.9},'
    ' "usage": {"current": @, "limit": 500, "remaining": -@, "unit": "credits"},'
    ' "credits": {"operation_weight": @, "current": 1, "limit": @, "remaining": 499.7,'
    ' "included": 500, "topup": 0}}'
).replace("@", HUGE)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_2xx_credit_and_usage_figures(mode):
    resp = run(mode, 200, INFO_RAW, lambda c: c.get_info())
    credits = resp.credits
    assert credits is not None
    assert (credits.operation_weight, credits.limit) == (None, None)
    assert credits.remaining == 499  # a finite float is truncated, as released
    assert (credits.current, credits.included) == (1, 500)
    usage = resp.usage
    assert usage is not None
    assert (usage.current, usage.remaining, usage.limit) == (None, None, 500)
    sub = resp.subscription_info
    assert sub is not None
    assert (sub.max_results, sub.monthly_credits) == (None, 2)
    assert resp.units_charged == 0


CREDITS_402 = (
    '{"success": false, "error": {"code": "CREDITS_EXHAUSTED", "message": "Out of credits",'
    ' "details": {"operation": "semantic", "weight": @, "available": -@,'
    ' "included": 2.9, "topup": @, "used": 3}}}'
).replace("@", HUGE)

NATIONAL_402 = (
    '{"success": false, "error": {"code": "NATIONAL_ALLOWANCE_EXHAUSTED",'
    ' "message": "Allowance used up", "details": {"jurisdiction": "AT", "limit": @,'
    ' "remaining": 0, "resetsAt": "2026-12-01T00:00:00.000Z"}}}'
).replace("@", HUGE)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_402_credit_figures(mode):
    with pytest.raises(CreditsExhaustedError) as info:
        run(mode, 402, CREDITS_402, lambda c: c.semantic_search("credit scoring"))
    err = info.value
    assert (err.weight, err.available, err.topup) == (None, None, None)
    assert (err.included, err.used, err.operation) == (2, 3, "semantic")


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_402_national_allowance_figures(mode):
    with pytest.raises(NationalAllowanceExhaustedError) as info:
        run(mode, 402, NATIONAL_402, lambda c: c.national.semantic_search("AT", "Datenschutz"))
    err = info.value
    assert err.limit is None
    assert err.jurisdiction == "AT"


def test_timeout_budget():
    for raw, expected in [(float("inf"), None), (float("nan"), None), (2.9, 2), (5000, 5000)]:
        err = TimeoutError("x", details={"timeoutMs": raw})
        assert err.timeout_ms == expected
