"""Redirects are never followed: the API key stays on the configured origin."""

import asyncio

import httpx
import pytest

from lexapi import AsyncLexAPI, LexAPI, LexAPIError

API_KEY = "lex_test_secret_key"
BASE_URL = "https://api.example/api/v1"

LOCATIONS = [
    "https://other.example/api/v1/info",  # another host
    "https://api.example:8443/api/v1/info",  # another port
    "http://api.example/api/v1/info",  # https -> http
    "https://user:hunter2@other.example/api/v1/info",  # credentials in the URL
    "//other.example/api/v1/info",  # protocol-relative
    "ftp://other.example/info",  # not http(s)
    "https://api.example/moved/info",  # same origin, absolute
    "/moved/info",  # same origin, relative
    "/api/v1/info",  # loop
]
STATUSES = [301, 302, 303, 307, 308]


def redirecting(seen: list, status: int, location: str):
    """A handler that records every request and always redirects to ``location``."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, headers={"location": location})

    return handler


CALLS = {
    "get": lambda client: client.get_info(),
    "post": lambda client: client.search("gdpr"),
    "stream": lambda client: client.export(document_type="regulation"),
}


def check(seen: list, error: LexAPIError, status: int) -> None:
    assert error.status == status
    assert len(seen) == 1
    assert seen[0].url.host == "api.example"
    assert seen[0].url.scheme == "https"
    assert seen[0].headers["x-api-key"] == API_KEY
    assert API_KEY not in str(error)
    assert "hunter2" not in str(error)


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("location", LOCATIONS)
@pytest.mark.parametrize("status", STATUSES)
def test_sync_client_does_not_follow_redirects(status, location, call):
    seen: list = []
    transport = httpx.MockTransport(redirecting(seen, status, location))

    with LexAPI(api_key=API_KEY, base_url=BASE_URL, transport=transport) as client:
        with pytest.raises(LexAPIError) as excinfo:
            CALLS[call](client)

    check(seen, excinfo.value, status)


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("location", LOCATIONS)
@pytest.mark.parametrize("status", STATUSES)
def test_async_client_does_not_follow_redirects(status, location, call):
    seen: list = []
    transport = httpx.MockTransport(redirecting(seen, status, location))

    async def run():
        async with AsyncLexAPI(api_key=API_KEY, base_url=BASE_URL, transport=transport) as client:
            await CALLS[call](client)

    with pytest.raises(LexAPIError) as excinfo:
        asyncio.run(run())

    check(seen, excinfo.value, status)


def test_with_options_does_not_follow_redirects():
    seen: list = []
    transport = httpx.MockTransport(redirecting(seen, 307, LOCATIONS[0]))

    with LexAPI(api_key=API_KEY, base_url=BASE_URL, transport=transport) as client:
        with pytest.raises(LexAPIError):
            client.with_options(timeout=5, max_retries=0).get_info()

    assert len(seen) == 1
    assert seen[0].url.host == "api.example"


def test_clients_keep_redirects_off():
    with LexAPI(api_key=API_KEY) as client:
        assert client._http.follow_redirects is False
    async_client = AsyncLexAPI(api_key=API_KEY)
    assert async_client._http.follow_redirects is False
    asyncio.run(async_client.close())
