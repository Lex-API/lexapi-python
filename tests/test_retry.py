"""Retry behavior: statuses, methods, Retry-After precedence, backoff."""

import time

import httpx
import pytest

from conftest import FAST_RETRY, INFO_BODY
from lexapi import (
    InternalServerError,
    LexAPI,
    LexAPIConnectionError,
    RateLimitedError,
    UpstreamError,
)
from lexapi._retry import (
    RetryConfig,
    compute_backoff,
    parse_retry_after,
    server_wait_hint,
)


def make_client(handler, **kwargs) -> LexAPI:
    kwargs.setdefault("retry_config", FAST_RETRY)
    return LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler), **kwargs)


def flaky_handler(failures, fail_response_factory):
    """Handler that fails `failures` times, then returns 200."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] <= failures:
            return fail_response_factory(request)
        return httpx.Response(200, json=INFO_BODY)

    return handler, calls


def test_get_retries_429_honoring_retry_after_header():
    handler, calls = flaky_handler(
        2, lambda req: httpx.Response(429, json={"error": "slow"}, headers={"Retry-After": "0"})
    )
    with make_client(handler) as client:
        info = client.get_info()
    assert info["service"] == "LexAPI"
    assert calls["count"] == 3


@pytest.mark.parametrize("status", [502, 503, 504])
def test_get_retries_5xx(status):
    handler, calls = flaky_handler(1, lambda req: httpx.Response(status, json={"error": "x"}))
    with make_client(handler) as client:
        client.get_info()
    assert calls["count"] == 2


def test_get_does_not_retry_500():
    handler, calls = flaky_handler(1, lambda req: httpx.Response(500, json={"error": "x"}))
    with make_client(handler) as client:
        with pytest.raises(InternalServerError):
            client.get_info()
    assert calls["count"] == 1


def test_retries_exhausted_raises_typed_error():
    handler, calls = flaky_handler(
        99, lambda req: httpx.Response(429, json={"error": "slow"}, headers={"Retry-After": "0"})
    )
    with make_client(handler) as client:
        with pytest.raises(RateLimitedError):
            client.get_info()
    assert calls["count"] == 4  # 1 initial + 3 retries


def test_max_retries_zero_disables():
    handler, calls = flaky_handler(
        99, lambda req: httpx.Response(429, json={"error": "slow"}, headers={"Retry-After": "0"})
    )
    with make_client(handler, retry_config=RetryConfig(max_retries=0)) as client:
        with pytest.raises(RateLimitedError):
            client.get_info()
    assert calls["count"] == 1


def test_post_retries_429_but_not_502():
    handler_429, calls_429 = flaky_handler(
        1, lambda req: httpx.Response(429, json={"error": "slow"}, headers={"Retry-After": "0"})
    )
    with make_client(handler_429) as client:
        client._request("POST", "/search", json={"query": "gdpr"})
    assert calls_429["count"] == 2

    handler_502, calls_502 = flaky_handler(
        1, lambda req: httpx.Response(502, json={"error": "upstream"})
    )
    with make_client(handler_502) as client:
        with pytest.raises(UpstreamError):
            client._request("POST", "/search", json={"query": "gdpr"})
    assert calls_502["count"] == 1


def test_connect_error_retried_for_post():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json=INFO_BODY)

    with make_client(handler) as client:
        client._request("POST", "/search", json={"query": "gdpr"})
    assert calls["count"] == 2


def test_connect_error_exhaustion_raises_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with make_client(handler) as client:
        with pytest.raises(LexAPIConnectionError):
            client.get_info()


def test_read_timeout_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ReadTimeout("slow read", request=request)

    with make_client(handler) as client:
        with pytest.raises(LexAPIConnectionError):
            client.get_info()
    assert calls["count"] == 1


# ── wait resolution ─────────────────────────────────────────────────────


def test_header_preferred_over_buggy_epoch_body_field():
    # The JSON `retryAfter` field was buggy (epoch seconds) until a pending
    # server fix — the header must win.
    headers = httpx.Headers({"Retry-After": "5"})
    body = {"error": "slow", "retryAfter": time.time() + 9999}
    assert server_wait_hint(headers, body) == 5.0


def test_epoch_body_field_converted_to_delta_when_no_header():
    body = {"error": "slow", "retryAfter": time.time() + 120}
    wait = server_wait_hint(httpx.Headers(), body)
    assert wait is not None
    assert 100 < wait < 140


def test_sane_body_field_used_as_seconds_when_no_header():
    assert server_wait_hint(httpx.Headers(), {"retryAfter": 12}) == 12.0


def test_ratelimit_reset_header_used_when_no_retry_after():
    headers = httpx.Headers({"RateLimit-Reset": "30"})
    assert server_wait_hint(headers, {}) == 30.0


def test_retry_after_http_date():
    wait = parse_retry_after("Wed, 01 Jul 2093 00:00:00 GMT")
    assert wait is not None and wait > 0


def test_retry_after_garbage_returns_none():
    assert parse_retry_after("soon") is None


def test_server_wait_clamped_to_cap():
    handler, calls = flaky_handler(
        1,
        lambda req: httpx.Response(
            429, json={"error": "slow"}, headers={"Retry-After": "9999"}
        ),
    )
    config = RetryConfig(max_retries=1, backoff_cap=0.001)
    start = time.monotonic()
    with make_client(handler, retry_config=config) as client:
        client.get_info()
    assert time.monotonic() - start < 1.0
    assert calls["count"] == 2


def test_compute_backoff_bounds():
    config = RetryConfig(backoff_base=0.5, backoff_cap=30.0)
    for attempt in range(10):
        wait = compute_backoff(attempt, config)
        assert 0 <= wait <= min(30.0, 0.5 * 2**attempt)
