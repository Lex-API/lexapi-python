"""NDJSON export streaming: meta/rows/done parsing, headers, errors."""

import asyncio
import json

import httpx
import pytest

from lexapi import AsyncLexAPI, CreditsExhaustedError, LexAPI

ROWS = [
    {"celex": "32016R0679", "language": "en", "title": "GDPR", "version": 3},
    {"celex": "62018CJ0311", "language": "en", "title": "Schrems II", "version": 1},
]


def ndjson_body(meta=None, rows=ROWS, done=None):
    lines = []
    if meta is not None:
        lines.append(json.dumps(meta))
    lines.extend(json.dumps(row) for row in rows)
    if done is not None:
        lines.append(json.dumps(done))
    return ("\n".join(lines) + "\n").encode()


META_LINE = {"_meta": {"filters": {"documentType": "regulation"}, "total": 2}}
DONE_LINE = {"_done": {"count": 2, "truncated": False}}

EXPORT_HEADERS = {
    "Content-Type": "application/x-ndjson",
    "X-Export-Total": "2",
    "X-Export-Streaming": "2",
    "X-Export-Truncated": "0",
}


def make_client(handler):
    return LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))


def test_export_streams_rows_and_captures_envelopes():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200, content=ndjson_body(META_LINE, ROWS, DONE_LINE), headers=EXPORT_HEADERS
        )

    with make_client(handler) as client:
        stream = client.export(document_type="regulation", limit=1000)
        # header-borne attributes available immediately
        assert stream.total == 2
        assert stream.streaming == 2
        assert stream.truncated is False

        rows = list(stream)

    assert seen["path"] == "/api/v1/export"
    assert seen["params"] == {"documentType": "regulation", "limit": "1000"}
    assert [row["celex"] for row in rows] == ["32016R0679", "62018CJ0311"]
    assert stream.meta == {"filters": {"documentType": "regulation"}, "total": 2}
    assert stream.done == {"count": 2, "truncated": False}


def test_export_read_meta_before_iterating():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson_body(META_LINE, ROWS, DONE_LINE))

    with make_client(handler) as client:
        stream = client.export()
        meta = stream.read_meta()
        assert meta == {"filters": {"documentType": "regulation"}, "total": 2}
        # no rows were lost
        assert [row["celex"] for row in stream] == ["32016R0679", "62018CJ0311"]


def test_export_flat_envelope_shape_tolerated():
    # Alternative flat line shapes: {"_meta": true, ...} / {"_done": true, ...}
    meta = {"_meta": True, "total": 2}
    done = {"_done": True, "truncated": True, "count": 2}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson_body(meta, ROWS, done))

    with make_client(handler) as client:
        stream = client.export()
        rows = list(stream)

    assert len(rows) == 2
    assert stream.meta["total"] == 2
    assert stream.done["truncated"] is True
    assert stream.truncated is True  # from the _done trailer


def test_export_truncated_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson_body(META_LINE, ROWS, {"_done": {"truncated": True}}),
            headers={"X-Export-Truncated": "1"},
        )

    with make_client(handler) as client:
        stream = client.export()
        assert stream.truncated is True  # header, before reading anything
        list(stream)
        assert stream.truncated is True


def test_export_param_serialization():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=ndjson_body(META_LINE, [], DONE_LINE))

    with make_client(handler) as client:
        list(
            client.export(
                document_type=["regulation", "directive"],
                author="commission",
                subdomain="case-law",
                language="en",
                date_from="2024-01-01",
                date_to="2024-12-31",
                fetched_since="2026-06-01T00:00:00Z",
                include_html=False,
                include_content=True,
                limit=500,
            )
        )

    assert seen["params"] == {
        "documentType": "regulation,directive",
        "author": "commission",
        "subdomain": "case-law",
        "language": "en",
        "dateFrom": "2024-01-01",
        "dateTo": "2024-12-31",
        "fetchedSince": "2026-06-01T00:00:00Z",
        "includeHtml": "false",
        "includeContent": "true",
        "limit": "500",
    }


def test_export_credit_pool_402_raises_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={
                "success": False,
                "error": {
                    "code": "CREDITS_EXHAUSTED",
                    "message": "Credit pool exhausted",
                },
            },
        )

    with make_client(handler) as client:
        with pytest.raises(CreditsExhaustedError):
            client.export()


def test_export_context_manager_closes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson_body(META_LINE, ROWS, DONE_LINE))

    with make_client(handler) as client:
        with client.export() as stream:
            first = next(iter(stream))
            assert first["celex"] == "32016R0679"
        # closed on exit without consuming the rest
        assert stream._finished is True


def test_export_blank_lines_skipped():
    content = b'\n{"_meta": {"total": 1}}\n\n{"celex": "X"}\n\n{"_done": {"count": 1}}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    with make_client(handler) as client:
        rows = list(client.export())
    assert rows == [{"celex": "X"}]


def test_async_export_stream():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=ndjson_body(META_LINE, ROWS, DONE_LINE), headers=EXPORT_HEADERS
        )

    async def run():
        async with AsyncLexAPI(
            api_key="lex_test_key", transport=httpx.MockTransport(handler)
        ) as client:
            stream = await client.export(document_type="regulation")
            meta = await stream.read_meta()
            rows = [row async for row in stream]
            return meta, rows, stream

    meta, rows, stream = asyncio.run(run())
    assert meta["total"] == 2
    assert [row["celex"] for row in rows] == ["32016R0679", "62018CJ0311"]
    assert stream.done == {"count": 2, "truncated": False}
    assert stream.total == 2
