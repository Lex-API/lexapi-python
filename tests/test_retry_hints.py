"""Retry hints are read strictly: a value that is not one of the documented
forms is absent, and no sleep ever receives nan, a negative or an
overflowing value."""

from __future__ import annotations

import asyncio
import math

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    LexAPI,
    NationalSemanticUnavailableError,
    RateLimitedError,
    _retry,
)
from lexapi._retry import (
    MAX_SLEEP_SECONDS,
    RetryConfig,
    compute_backoff,
    is_quota_exhausted,
    parse_http_date,
    parse_retry_after,
    server_wait_hint,
)

NOW = 1_763_510_400.0  # Wed, 19 Nov 2025 00:00:00 GMT
LOOPBACK = "http://127.0.0.1:9/api/v1"


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    monkeypatch.setattr(_retry.time, "time", lambda: NOW)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


YEAR_2074 = 3_309_811_200.0  # Mon, 19 Nov 2074 00:00:00 GMT

# Retry-After value -> wait in seconds (None: absent).
RETRY_AFTER = [
    # delta-seconds
    ("0", 0.0),
    ("5", 5.0),
    ("05", 5.0),
    (" 5", 5.0),
    ("5 ", 5.0),
    ("\t7\t", 7.0),
    (" \t 7 \t ", 7.0),
    ("5.5", 5.5),
    ("0.25", 0.25),
    ("120", 120.0),
    ("999999", 999999.0),
    ("1000000000", 1e9),
    ("+5", None),
    ("-5", None),
    ("-0", None),
    ("5e2", None),
    ("5E2", None),
    ("1_000", None),
    (".5", None),
    ("5.", None),
    ("5..5", None),
    ("5,5", None),
    ("0x10", None),
    ("nan", None),
    ("NaN", None),
    ("inf", None),
    ("-inf", None),
    ("Infinity", None),
    ("1e999", None),
    ("1" * 400, None),
    ("５", None),  # fullwidth 5
    ("٣", None),  # Arabic-Indic 3
    ("", None),
    (" ", None),
    ("\t", None),
    ("abc", None),
    ("5 s", None),
    ("5\n", None),
    ("\n5", None),
    (" 5", None),  # no-break space is not trimmed
    # epoch values
    ("1763510430", 30.0),
    ("1763510430000", 30.0),
    ("1500000000", 0.0),
    ("1500000000000", 0.0),
    ("1000000001", 0.0),
    # IMF-fixdate
    ("Wed, 19 Nov 2025 00:00:30 GMT", 30.0),
    ("wed, 19 nov 2025 00:00:30 gmt", 30.0),
    ("WED, 19 NOV 2025 00:00:30 GMT", 30.0),
    ("Wed, 19 Nov 2025 00:00:30 UTC", 30.0),
    ("Wed, 19 Nov 2025 00:00:30 +0000", 30.0),
    ("Mon, 19 Nov 2025 00:00:30 GMT", 30.0),  # weekday not checked
    ("  Wed, 19 Nov 2025 00:00:30 GMT\t", 30.0),
    ("Wed, 19 Nov 2025 01:00:00 GMT", 3600.0),
    ("Tue, 18 Nov 2025 23:59:59 GMT", 0.0),  # past
    ("Thu, 01 Jan 1970 00:00:00 GMT", 0.0),
    ("Wed, 19 Nov 2025 00:00:30 EST", None),
    ("Wed, 19 Nov 2025 00:00:30 -0000", None),
    ("Wed, 19 Nov 2025 00:00:30 +0100", None),
    ("Wed, 19 Nov 2025 00:00:30", None),
    ("Wed, 9 Nov 2025 00:00:30 GMT", None),
    ("Wed 19 Nov 2025 00:00:30 GMT", None),
    ("Wed,  19 Nov 2025 00:00:30 GMT", None),
    ("Wednesday, 19 Nov 2025 00:00:30 GMT", None),
    ("Wed, 19 November 2025 00:00:30 GMT", None),
    ("Wed, 19 Nov 25 00:00:30 GMT", None),
    ("Wed, 19 Nov 2025 0:00:30 GMT", None),
    ("Wed, 19 Nov 2025 00:00:30.5 GMT", None),
    ("Wed, 19 Nov 2025 00:00:30 GMT x", None),
    ("x Wed, 19 Nov 2025 00:00:30 GMT", None),
    ("Wed, 19 Nov 2025 24:00:00 GMT", None),
    ("Wed, 19 Nov 2025 23:60:00 GMT", None),
    ("Wed, 19 Nov 2025 23:59:60 GMT", None),
    ("Wed, 00 Nov 2025 00:00:00 GMT", None),
    ("Wed, 32 Dec 2025 00:00:00 GMT", None),
    ("Mon, 30 Feb 2026 00:00:00 GMT", None),
    ("Thu, 29 Feb 2027 00:00:00 GMT", None),
    ("Mon, 29 Feb 2100 00:00:00 GMT", None),
    ("Tue, 29 Feb 2028 00:00:00 GMT", 71_884_800.0),
    ("Sat, 01 Jan 0000 00:00:00 GMT", None),
    ("Wed, 19 Nov 2025 00:00:30 GΜT", None),
    ("Wed, 19 Nov ２０２５ 00:00:30 GMT", None),
    # RFC 850
    ("Wednesday, 19-Nov-25 00:00:30 GMT", 30.0),
    ("wednesday, 19-nov-25 00:00:30 utc", 30.0),
    ("Wednesday, 19-Nov-25 00:00:30 +0000", 30.0),
    ("Monday, 19-Nov-25 00:00:30 GMT", 30.0),
    ("Wednesday, 19-Nov-99 00:00:30 GMT", 0.0),  # 1999
    ("Monday, 19-Nov-75 00:00:30 GMT", 0.0),  # more than 50 years ahead: 1975
    ("Monday, 19-Nov-74 00:00:00 GMT", YEAR_2074 - NOW),  # 2074
    ("Wed, 19-Nov-25 00:00:30 GMT", None),
    ("Wednesday, 19-Nov-2025 00:00:30 GMT", None),
    ("Wednesday, 9-Nov-25 00:00:30 GMT", None),
    ("Wednesday, 19 Nov 25 00:00:30 GMT", None),
    ("Wednesday, 19-Nov-25 00:00:30 EST", None),
    ("Wednesday, 30-Feb-26 00:00:30 GMT", None),
    ("Wednesday, 19-Nov-25 24:00:00 GMT", None),
    # asctime (read as UTC)
    ("Wed Nov 19 00:00:30 2025", 30.0),
    ("wed nov 19 00:00:30 2025", 30.0),
    ("Sun Nov  9 00:00:00 2025", 0.0),
    ("Sun Nov 09 00:00:00 2025", 0.0),
    ("Wed Dec  3 00:00:00 2025", 1_209_600.0),
    ("Wed Nov 9 00:00:30 2025", None),
    ("Wed Nov 19 00:00:30 2025 GMT", None),
    ("Wed Nov 19 00:00:30 25", None),
    ("Wed Nov 31 00:00:30 2025", None),
    ("Wed Nov 19 00:00:60 2025", None),
    ("Wednesday Nov 19 00:00:30 2025", None),
]


@pytest.mark.parametrize("value, expected", RETRY_AFTER)
def test_retry_after_table(value, expected):
    got = parse_retry_after(value)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)
        assert math.isfinite(got) and got >= 0


def test_the_table_is_large_enough():
    assert len(RETRY_AFTER) >= 100


def test_a_past_epoch_hint_is_zero_not_negative():
    for value in ("1500000000", "1500000000000", "1000000001"):
        assert parse_retry_after(value) == 0.0


def test_http_date_round_half_up(monkeypatch):
    for now, expected in [(NOW + 0.4, 30.0), (NOW + 0.5, 30.0), (NOW + 0.6, 29.0)]:
        monkeypatch.setattr(_retry.time, "time", lambda now=now: now)
        assert parse_retry_after("Wed, 19 Nov 2025 00:00:30 GMT") == expected


def test_asctime_is_read_as_utc_not_local_time():
    assert parse_http_date("Wed Nov 19 00:00:00 2025") == NOW


RATELIMIT_RESET = [
    ("0", 0.0),
    ("4", 4.0),
    (" 4 ", 4.0),
    ("4.5", 4.5),
    ("1763510430", 30.0),
    ("-4", None),
    ("+4", None),
    ("4e1", None),
    ("nan", None),
    ("inf", None),
    ("junk", None),
    ("", None),
    ("Wed, 19 Nov 2025 00:00:30 GMT", None),  # never a date
]


@pytest.mark.parametrize("value, expected", RATELIMIT_RESET)
def test_ratelimit_reset_table(value, expected):
    got = server_wait_hint(httpx.Headers({"ratelimit-reset": value}), None, 429)
    assert got == (pytest.approx(expected) if expected is not None else None)


BODY = [
    (5, 5.0),
    (5.5, 5.5),
    (0, 0.0),
    (-0.0, 0.0),
    (30.0, 30.0),
    (1_763_510_430, 30.0),
    (-1, None),
    (-1e-9, None),
    (True, None),
    (False, None),
    ("5", None),
    (None, None),
    ([5], None),
    ({"s": 5}, None),
    (10**400, None),
    (-(10**400), None),
    (float("inf"), None),
    (float("nan"), None),
]


@pytest.mark.parametrize("value, expected", BODY)
def test_body_retry_after_table(value, expected):
    for body in (
        {"error": "Too many requests", "retryAfter": value},
        {"success": False, "error": {"code": "RATE_LIMITED", "details": {"retryAfter": value}}},
        {"error": "Too many requests", "details": {"retryAfter": value}},
    ):
        got = server_wait_hint(httpx.Headers(), body, 429)
        assert got == (pytest.approx(expected) if expected is not None else None)
        if expected == 0.0:
            assert math.copysign(1.0, got) == 1.0  # -0 reads as 0


def test_body_sources_in_order():
    top_bad = {
        "retryAfter": "x",
        "error": {"details": {"retryAfter": 7}},
        "details": {"retryAfter": 9},
    }
    assert server_wait_hint(httpx.Headers(), top_bad, 429) == 7.0
    legacy = {"error": "Too many requests", "retryAfter": -1, "details": {"retryAfter": 9}}
    assert server_wait_hint(httpx.Headers(), legacy, 429) == 9.0
    typed_without = {"error": {"code": "RATE_LIMITED"}, "details": {"retryAfter": 9}}
    assert server_wait_hint(httpx.Headers(), typed_without, 429) == 9.0
    top_good = {"retryAfter": 3, "error": {"details": {"retryAfter": 7}}}
    assert server_wait_hint(httpx.Headers(), top_good, 429) == 3.0


def test_quota_429_reads_no_usable_wait():
    body = {"error": "Daily quota exceeded", "usage": {"resetsAt": "2025-11-20T00:00:00.000Z"}}
    assert is_quota_exhausted(429, httpx.Headers({"retry-after": "nan"}), body)
    assert is_quota_exhausted(429, httpx.Headers(), dict(body, retryAfter=-5))
    assert not is_quota_exhausted(429, httpx.Headers({"retry-after": "5"}), body)
    assert not is_quota_exhausted(429, httpx.Headers(), dict(body, retryAfter=5))


@pytest.mark.parametrize("name", ["backoff_base", "backoff_cap"])
@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1, -0.5, True, "1"])
def test_retry_config_refuses_bad_waits(name, bad):
    with pytest.raises(ValueError):
        RetryConfig(**{name: bad})


def test_retry_config_accepts_zero_and_large_finite():
    RetryConfig(backoff_base=0, backoff_cap=0)
    RetryConfig(backoff_cap=1e300)


def test_backoff_never_overflows():
    config = RetryConfig(max_retries=10_000, backoff_base=1e300, backoff_cap=1e300)
    for attempt in (0, 10, 1023, 1024, 5000):
        wait = compute_backoff(attempt, config)
        assert math.isfinite(wait) and 0 <= wait <= MAX_SLEEP_SECONDS


# ── what reaches a sleep call ────────────────────────────────────────────


def _record_sleeps(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr("lexapi.client.time.sleep", lambda s: waits.append(s))

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr("lexapi.async_client.asyncio.sleep", fake_sleep)
    return waits


def _client_call(mode, handler, config, call):
    transport = httpx.MockTransport(handler)
    if mode == "sync":
        with LexAPI("k", base_url=LOOPBACK, transport=transport, retry_config=config) as c:
            return call(c)

    async def go():
        async with AsyncLexAPI(
            "k", base_url=LOOPBACK, transport=transport, retry_config=config
        ) as c:
            return await asyncio.wait_for(call(c), 10)

    return asyncio.run(go())


HOSTILE_HEADERS = [
    {"retry-after": "nan"},
    {"retry-after": "-1"},
    {"retry-after": "1e999"},
    {"retry-after": "99999999999"},
    {"retry-after": "Wed, 19 Nov 9999 00:00:00 GMT"},
    {"ratelimit-reset": "inf"},
    {"ratelimit-reset": "-3"},
    {},
]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("headers", HOSTILE_HEADERS)
@pytest.mark.parametrize("cap", [0.0, 30.0, 1e300])
def test_every_sleep_is_finite_non_negative_and_capped(monkeypatch, mode, headers, cap):
    waits = _record_sleeps(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={"error": "Too many requests"}, headers=headers)

    config = RetryConfig(max_retries=3, backoff_base=1e300, backoff_cap=cap)
    with pytest.raises(RateLimitedError):
        _client_call(mode, handler, config, lambda c: c.get_info())
    assert calls["n"] == 4 and len(waits) == 3
    for wait in waits:
        assert math.isfinite(wait) and 0 <= wait <= min(cap, MAX_SLEEP_SECONDS)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_err_retry_after_is_the_resolved_hint_not_clamped(mode):
    def handler(request):
        return httpx.Response(429, json={"error": "x"}, headers={"retry-after": "999999"})

    with pytest.raises(RateLimitedError) as info:
        _client_call(mode, handler, RetryConfig(max_retries=0), lambda c: c.get_info())
    assert info.value.retry_after == 999999.0


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("hint", [-1, 10**400, "5", True])
def test_incomplete_national_search_with_only_an_unusable_hint_is_not_retried(mode, hint):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            503,
            json={
                "error": "Index unavailable",
                "code": "NATIONAL_SEMANTIC_UNAVAILABLE",
                "jurisdiction": "AT",
                "partialReason": "SEARCH_INCOMPLETE",
                "retryAfter": hint,
            },
        )

    config = RetryConfig(max_retries=3, backoff_base=0.0001, backoff_cap=0.001)
    with pytest.raises(NationalSemanticUnavailableError) as info:
        _client_call(mode, handler, config, lambda c: c.national.semantic_search("AT", "x"))
    assert calls["n"] == 1
    assert info.value.retry_after is None
