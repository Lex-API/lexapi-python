"""Unit tests for the LexAPI client scaffold (mocked HTTP, no live sockets)."""

import httpx
import pytest

from lexapi import LexAPI, LexAPIError

INFO_BODY = {
    "service": "LexAPI",
    "version": "2.0.0",
    "subscription": {"tier": "FREE", "pricingMode": "credits"},
    "usage": {"current": 0, "limit": 100, "remaining": 100, "unit": "credits"},
    "credits": {"operation_weight": 0, "remaining": 100},
}


def make_client(handler) -> LexAPI:
    return LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))


def test_get_info_sends_auth_and_user_agent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["x-api-key"] = request.headers.get("x-api-key")
        seen["user-agent"] = request.headers.get("user-agent")
        return httpx.Response(200, json=INFO_BODY)

    with make_client(handler) as client:
        info = client.get_info()

    assert info["service"] == "LexAPI"
    assert seen["url"] == "https://lex-api.com/api/v1/info"
    assert seen["x-api-key"] == "lex_test_key"
    assert seen["user-agent"].startswith("lexapi-python/")


def test_typed_error_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "success": False,
                "error": {"code": "NOT_FOUND", "message": "Document missing"},
            },
        )

    with make_client(handler) as client:
        with pytest.raises(LexAPIError) as excinfo:
            client.get_info()

    assert excinfo.value.code == "NOT_FOUND"
    assert excinfo.value.message == "Document missing"
    assert excinfo.value.status == 404


def test_legacy_bare_error_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "Rate limit hit"}, headers={"Retry-After": "7"})

    with make_client(handler) as client:
        with pytest.raises(LexAPIError) as excinfo:
            client.get_info()

    assert excinfo.value.code == "RATE_LIMITED"
    assert excinfo.value.message == "Rate limit hit"
    assert excinfo.value.retry_after == 7.0


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("LEXAPI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        LexAPI(api_key=None)
