"""Top-level ``code`` on legacy-shape bodies and ``QUOTA_EXHAUSTED``
(PLAN.md §1.2 / §1.3; lex-api PR #304).

From #304 on, legacy-shape error bodies carry ``success: false`` and a
top-level ``code`` next to the human-text ``error``; a legacy quota 429 says
``"code": "QUOTA_EXHAUSTED"`` with a top-level ``resetsAt``. The code takes
precedence over the status-derived one, a quota 429 is never retried and
exposes no retry-after, and the absence-based detection stays as the
fallback for older servers.
"""

import httpx
import pytest

from conftest import FAST_RETRY
from lexapi import InvalidParamsError, LexAPI, LexAPIError, RateLimitedError
from lexapi._retry import is_quota_exhausted
from lexapi.errors import error_from_response

QUOTA_BODY = {
    "success": False,
    "code": "QUOTA_EXHAUSTED",
    "error": "Daily quota exceeded",
    "message": "You've reached your daily limit of 1000 API calls. Resets at midnight UTC.",
    "resetsAt": "2026-10-27T00:00:00.000Z",
    "usage": {
        "current": 1000,
        "limit": 1000,
        "resetsAt": "2026-10-27T00:00:00.000Z",
        "unit": "calls",
    },
}


def client_for(status, body, headers=None):
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(status, json=body, headers=headers or {})

    client = LexAPI(
        api_key="lex_test_key", transport=httpx.MockTransport(handler), retry_config=FAST_RETRY
    )
    return client, calls


def test_quota_exhausted_code_is_not_retried_and_has_no_retry_after():
    # Even with a Retry-After / RateLimit-Reset on the response: the code decides.
    client, calls = client_for(429, QUOTA_BODY, {"Retry-After": "0", "RateLimit-Reset": "0"})
    with client, pytest.raises(RateLimitedError) as info:
        client.get_info()
    err = info.value
    assert calls["count"] == 1
    assert err.code == "QUOTA_EXHAUSTED"
    assert err.quota_exhausted is True
    assert err.retry_after is None
    assert err.resets_at == "2026-10-27T00:00:00.000Z"
    assert err.message.startswith("Daily quota exceeded")


def test_top_level_resets_at_wins_over_usage():
    body = {**QUOTA_BODY, "resetsAt": "2026-11-01T00:00:00.000Z"}
    err = error_from_response(429, body)
    assert err.resets_at == "2026-11-01T00:00:00.000Z"


def test_rate_limited_code_is_retried():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                429,
                json={
                    "success": False,
                    "code": "RATE_LIMITED",
                    "error": "Rate limit exceeded",
                    "retryAfter": 0,
                },
                headers={"Retry-After": "0"},
            )
        return httpx.Response(200, json={"success": True})

    with LexAPI(
        api_key="lex_test_key", transport=httpx.MockTransport(handler), retry_config=FAST_RETRY
    ) as client:
        client.get_info()
    assert calls["count"] == 2


def test_top_level_code_takes_precedence_over_the_status():
    err = error_from_response(
        400, {"success": False, "code": "INVALID_CELEX", "error": "Bad CELEX"}
    )
    assert err.code == "INVALID_CELEX"
    assert err.message == "Bad CELEX"
    err = error_from_response(400, {"error": "Bad request"})
    assert isinstance(err, InvalidParamsError)
    assert err.code == "INVALID_PARAMS"
    # The legacy string `error` is never a code, even when it looks like one.
    assert error_from_response(429, {"error": "QUOTA_EXHAUSTED"}).code == "RATE_LIMITED"


def test_unknown_top_level_code_is_kept_raw():
    err = error_from_response(418, {"code": "SOMETHING_NEW", "error": "x"})
    assert type(err) is LexAPIError
    assert err.code == "SOMETHING_NEW"


def test_absence_based_fallback_still_applies_to_older_servers():
    body = {
        "error": "Daily quota exceeded",
        "usage": {"current": 1000, "limit": 1000, "resetsAt": "2026-10-27T00:00:00.000Z"},
    }
    err = error_from_response(429, body)
    assert err.code == "RATE_LIMITED"
    assert err.quota_exhausted is True
    assert is_quota_exhausted(429, httpx.Headers(), body)


def test_is_quota_exhausted_reads_the_code():
    assert is_quota_exhausted(429, httpx.Headers({"Retry-After": "30"}), QUOTA_BODY)
    assert not is_quota_exhausted(503, httpx.Headers(), QUOTA_BODY)
