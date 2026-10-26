"""Core client behavior: config, headers, envelope parsing (mocked HTTP)."""

import httpx
import pytest

from conftest import INFO_BODY
from lexapi import APIResponse, LexAPI, LexAPIError, RateLimitedError


def make_client(handler, **kwargs) -> LexAPI:
    kwargs.setdefault("api_key", "lex_test_key")
    return LexAPI(transport=httpx.MockTransport(handler), **kwargs)


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


def test_user_agent_suffix():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user-agent"] = request.headers.get("user-agent")
        return httpx.Response(200, json=INFO_BODY)

    with make_client(handler, user_agent_suffix="myapp/1.2") as client:
        client.get_info()

    assert seen["user-agent"].endswith(" myapp/1.2")
    assert seen["user-agent"].startswith("lexapi-python/")


def test_base_url_override_and_trailing_slash():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=INFO_BODY)

    with make_client(handler, base_url="http://localhost:3000/api/v1/") as client:
        client.get_info()

    assert seen["url"] == "http://localhost:3000/api/v1/info"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("LEXAPI_API_KEY", "lex_env_key")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["x-api-key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=INFO_BODY)

    with LexAPI(transport=httpx.MockTransport(handler)) as client:
        client.get_info()

    assert seen["x-api-key"] == "lex_env_key"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("LEXAPI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        LexAPI(api_key=None)


def test_response_is_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=INFO_BODY)

    with make_client(handler) as client:
        info = client.get_info()

    assert isinstance(info, APIResponse)
    assert "service" in info
    assert info.get("missing") is None
    assert len(info) == len(INFO_BODY)
    assert set(info) == set(INFO_BODY)
    assert info.data == INFO_BODY


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
        return httpx.Response(500, json={"error": "Something broke"})

    with make_client(handler) as client:
        with pytest.raises(LexAPIError) as excinfo:
            client.get_info()

    assert excinfo.value.code == "INTERNAL"
    assert excinfo.value.message == "Something broke"


def test_rate_limit_error_carries_retry_after_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "Rate limit hit"}, headers={"Retry-After": "7"})

    with make_client(handler, max_retries=0) as client:
        with pytest.raises(RateLimitedError) as excinfo:
            client.get_info()

    assert excinfo.value.code == "RATE_LIMITED"
    assert excinfo.value.message == "Rate limit hit"
    assert excinfo.value.retry_after == 7.0


def test_no_content_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with make_client(handler) as client:
        resp = client._request("DELETE", "/webhooks/whk_1")

    assert isinstance(resp, APIResponse)
    assert len(resp) == 0
