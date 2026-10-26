"""Async client parity: same core behavior through httpx.AsyncClient."""

import asyncio

import httpx
import pytest

from conftest import FAST_RETRY, INFO_BODY
from lexapi import AsyncLexAPI, NotFoundError


def make_client(handler, **kwargs) -> AsyncLexAPI:
    kwargs.setdefault("retry_config", FAST_RETRY)
    return AsyncLexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler), **kwargs)


def test_async_get_info():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["x-api-key"] = request.headers.get("x-api-key")
        seen["user-agent"] = request.headers.get("user-agent")
        return httpx.Response(200, json=INFO_BODY)

    async def run():
        async with make_client(handler) as client:
            return await client.get_info()

    info = asyncio.run(run())
    assert info["service"] == "LexAPI"
    assert seen["x-api-key"] == "lex_test_key"
    assert seen["user-agent"].startswith("lexapi-python/")


def test_async_error_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"success": False, "error": {"code": "NOT_FOUND", "message": "gone"}},
        )

    async def run():
        async with make_client(handler) as client:
            await client.get_info()

    with pytest.raises(NotFoundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code == "NOT_FOUND"


def test_async_retries_429():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] <= 2:
            return httpx.Response(429, json={"error": "slow"}, headers={"Retry-After": "0"})
        return httpx.Response(200, json=INFO_BODY)

    async def run():
        async with make_client(handler) as client:
            return await client.get_info()

    info = asyncio.run(run())
    assert info["service"] == "LexAPI"
    assert calls["count"] == 3


def test_async_credits_helpers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=INFO_BODY)

    async def run():
        async with make_client(handler) as client:
            return await client.get_info()

    info = asyncio.run(run())
    assert info.units_charged == 0
    assert info.credits_remaining == 100
