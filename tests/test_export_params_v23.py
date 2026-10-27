"""Export parameters for API 2.3.0 (lex-api #303): ``document_type_code``,
``changed_since``, and client-side checks that mirror the server's 400s
exactly (timezone-less datetime, non-positive / non-integer limit)."""

import datetime as dt

import httpx
import pytest

from lexapi import AsyncLexAPI, LexAPI
from lexapi.export import export_params

BODY = b'{"_meta":{}}\n{"_done":{"written":0}}\n'


def capture():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=BODY, headers={"Content-Type": "application/x-ndjson"})

    return seen, LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))


def test_new_params_are_sent():
    seen, client = capture()
    utc = dt.timezone.utc
    with client:
        stream = client.export(
            document_type_code=["regulation", "directive"],
            changed_since=dt.datetime(2026, 5, 11, 10, 30, tzinfo=utc),
            fetched_since="2026-05-11",
            date_from=dt.date(2020, 1, 1),
            limit=10,
        )
        list(stream)
    assert seen["params"] == {
        "documentTypeCode": "regulation,directive",
        "changedSince": "2026-05-11T10:30:00+00:00",
        "fetchedSince": "2026-05-11",
        "dateFrom": "2020-01-01",
        "limit": "10",
    }


def test_async_client_sends_the_new_params():
    import asyncio

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=BODY, headers={"Content-Type": "application/x-ndjson"})

    async def run():
        async with AsyncLexAPI(api_key="k", transport=httpx.MockTransport(handler)) as client:
            stream = await client.export(
                document_type_code="regulation", changed_since="2026-05-11"
            )
            async for _ in stream:
                pass

    asyncio.run(run())
    assert seen["params"] == {"documentTypeCode": "regulation", "changedSince": "2026-05-11"}


@pytest.mark.parametrize("name", ["fetched_since", "changed_since"])
def test_naive_datetime_is_refused_before_any_request(name):
    with pytest.raises(ValueError, match="timezone"):
        export_params(**{name: dt.datetime(2026, 5, 11, 10, 30)})


@pytest.mark.parametrize("bad", [0, -5, 2.5, True, "10"])
def test_limit_must_be_a_positive_integer(bad):
    with pytest.raises(ValueError, match="positive integer"):
        export_params(limit=bad)


def test_large_limit_is_left_for_the_server_to_clamp():
    assert export_params(limit=999_999)["limit"] == 999_999


def test_strings_pass_through_for_the_server_to_validate():
    # Mirroring the server's string grammar client-side would drift; the
    # server answers 400 INVALID_PARAMS (no charge).
    assert export_params(fetched_since="yesterday")["fetchedSince"] == "yesterday"
