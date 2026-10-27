"""Export failure endings (PLAN.md §1.8): a stream is complete only when it
ends with a ``_done`` trailer that does not say ``"complete": false``, and a
line whose only key is ``_error`` is a failure signal, never a row.

lex-api PR #303 briefly emitted ``{"_error": ...}`` then
``{"_done": {"complete": false}}`` on a mid-stream failure; the server no
longer does (it ends the body without a trailer), but a stream like that must
never look complete or yield the error object as a document.
"""

import asyncio
import json

import httpx
import pytest

from lexapi import AsyncLexAPI, IncompleteExportError, LexAPI

HEADERS = {"Content-Type": "application/x-ndjson"}

FAILURE_STREAM = (
    '{"_meta":{}}\n'
    '{"celex":"A"}\n'
    '{"celex":"B"}\n'
    '{"_error":{"code":"INTERNAL","message":"x"}}\n'
    '{"_done":{"written":2,"complete":false}}\n'
)


def client_for(body: str) -> LexAPI:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode(), headers=HEADERS)

    return LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))


def async_client_for(body: str) -> AsyncLexAPI:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode(), headers=HEADERS)

    return AsyncLexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))


def drain(stream):
    rows = []
    with pytest.raises(IncompleteExportError) as info:
        for row in stream:
            rows.append(row)
    return rows, info.value


def test_error_line_then_incomplete_done_is_incomplete():
    with client_for(FAILURE_STREAM) as client:
        stream = client.export()
        rows, err = drain(stream)
    assert rows == [{"celex": "A"}, {"celex": "B"}]
    assert stream.complete is False
    assert err.code == "INCOMPLETE_EXPORT"
    assert err.details == {"code": "INTERNAL", "message": "x"}
    assert stream.error == {"code": "INTERNAL", "message": "x"}


def test_done_with_complete_false_alone_is_incomplete():
    body = '{"_meta":{}}\n{"celex":"A"}\n{"_done":{"written":1,"complete":false}}\n'
    with client_for(body) as client:
        stream = client.export()
        rows, err = drain(stream)
    assert rows == [{"celex": "A"}]
    assert "complete: false" in str(err)


def test_error_line_without_done_is_incomplete_and_not_a_row():
    body = '{"_meta":{}}\n{"celex":"A"}\n{"_error":{"code":"INTERNAL","message":"x"}}\n'
    with client_for(body) as client:
        stream = client.export()
        rows, _ = drain(stream)
    assert rows == [{"celex": "A"}]


def test_missing_done_is_still_incomplete():
    body = '{"_meta":{}}\n{"celex":"A"}\n{"celex":"B"}\n'
    with client_for(body) as client:
        stream = client.export()
        rows, err = drain(stream)
    assert rows == [{"celex": "A"}, {"celex": "B"}]
    assert err.details is None


def test_error_line_is_not_buffered_by_read_meta():
    body = (
        '{"_error":{"code":"INTERNAL","message":"x"}}\n{"_done":{"written":0,"complete":false}}\n'
    )
    with client_for(body) as client:
        stream = client.export()
        assert stream.read_meta() is None
        rows, _ = drain(stream)
    assert rows == []


def test_complete_stream_with_new_done_fields():
    done = {
        "_done": {
            "written": 2,
            "complete": True,
            "truncated": False,
            "truncatedBy": None,
            "skippedStubs": 1,
            "credits": {"reserved": 3, "charged": 2, "refunded": 1, "remaining": 998},
        }
    }
    body = '{"_meta":{}}\n{"celex":"A"}\n{"celex":"B"}\n' + json.dumps(done) + "\n"
    with client_for(body) as client:
        stream = client.export()
        rows = list(stream)
    assert rows == [{"celex": "A"}, {"celex": "B"}]
    assert stream.complete is True
    assert stream.written == 2
    assert stream.skipped_stubs == 1
    assert stream.done["credits"]["charged"] == 2


def test_complete_stream_without_complete_field():
    body = '{"_meta":{}}\n{"celex":"A"}\n{"_done":{"written":1,"truncated":false}}\n'
    with client_for(body) as client:
        stream = client.export()
        assert list(stream) == [{"celex": "A"}]
        assert stream.complete is True


def test_async_failure_stream():
    async def run():
        async with async_client_for(FAILURE_STREAM) as client:
            stream = await client.export()
            rows = []
            with pytest.raises(IncompleteExportError):
                async for row in stream:
                    rows.append(row)
            return rows, stream.complete

    rows, complete = asyncio.run(run())
    assert rows == [{"celex": "A"}, {"celex": "B"}]
    assert complete is False
