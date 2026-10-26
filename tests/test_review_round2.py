"""Regression tests for the PR #11 verification review (N1-N6). Each fails
on 6c011ef."""

import asyncio
import copy
import dataclasses
import json
import pickle
import warnings

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    IncompleteExportError,
    LexAPI,
    LexAPIConnectionError,
    RetryConfig,
    SemanticCaseLawResult,
    UpstreamError,
    Webhook,
)
from lexapi._retry import never_reached_app

FAST = RetryConfig(max_retries=3, backoff_base=0.0001, backoff_cap=0.001)
FAST_RO = RetryConfig(
    max_retries=3, backoff_base=0.0001, backoff_cap=0.001, retry_read_only_posts=True
)

PRE_RESPONSE = "Server disconnected without sending a response."
MID_BODY = "peer closed connection without sending complete message body (incomplete chunked read)"


def make(handler, client_cls=LexAPI, retry=FAST):
    return client_cls(
        api_key="lex_test_key", transport=httpx.MockTransport(handler), retry_config=retry
    )


def first_raises(message, ok_body):
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.RemoteProtocolError(message, request=request)
        return httpx.Response(200, json=ok_body)

    return handler, calls


# ── N1: only the pre-response disconnect is "never reached" ────────────


def test_n1_matcher_distinguishes_pre_response_from_mid_body():
    request = httpx.Request("GET", "https://lex-api.com/api/v1/info")
    assert never_reached_app(httpx.RemoteProtocolError(PRE_RESPONSE, request=request))
    assert not never_reached_app(httpx.RemoteProtocolError(MID_BODY, request=request))
    assert not never_reached_app(httpx.RemoteProtocolError("illegal header", request=request))


@pytest.mark.parametrize(
    "call, body",
    [
        (lambda c: c.list_document_versions("32016R0679"), {"versions": []}),  # billed GET
        (lambda c: c.search("gdpr"), {"results": []}),  # read-only POST
    ],
)
def test_n1_mid_body_close_not_retried_on_billed_calls(call, body):
    handler, calls = first_raises(MID_BODY, body)
    with make(handler, retry=FAST_RO) as client:
        with pytest.raises(LexAPIConnectionError):
            call(client)
    assert calls["count"] == 1


@pytest.mark.parametrize(
    "call, body",
    [
        (lambda c: c.list_document_versions("32016R0679"), {"versions": []}),
        (lambda c: c.search("gdpr"), {"results": []}),
    ],
)
def test_n1_pre_response_close_retried_on_billed_calls(call, body):
    handler, calls = first_raises(PRE_RESPONSE, body)
    with make(handler) as client:
        call(client)
    assert calls["count"] == 2


def test_n1_mid_body_close_still_retried_on_free_get():
    handler, calls = first_raises(MID_BODY, {"service": "LexAPI"})
    with make(handler) as client:
        client.get_info()
    assert calls["count"] == 2


# ── N5: app origin by header only ──────────────────────────────────────


def test_n5_json_error_body_without_app_header_is_proxy_origin():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(502, json={"error": "bad gateway"})  # an intermediary's JSON

    with make(handler, retry=FAST_RO) as client:
        with pytest.raises(UpstreamError):
            client.list_document_versions("32016R0679")
        with pytest.raises(UpstreamError):
            client.search("gdpr")
    assert calls["count"] == 2  # one attempt each, no retry


@pytest.mark.parametrize("header", ["RateLimit-Limit", "X-LexAPI-Version"])
def test_n5_app_headers_mark_app_origin(header):
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(502, text="Bad Gateway", headers={header: "120"})
        return httpx.Response(200, json={"versions": []})

    with make(handler) as client:
        client.list_document_versions("32016R0679")
    assert calls["count"] == 2


# ── N2: async parity of the free / read_only flags ─────────────────────


def _run(coro):
    return asyncio.run(coro)


def test_n2_async_free_flag_on_get_info_and_webhook_reads():
    async def one(call, body):
        calls = {"count": 0}

        def handler(request):
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(524, text="timeout")  # proxy-origin
            return httpx.Response(200, json=body)

        async with make(handler, AsyncLexAPI) as client:
            await call(client)
        return calls["count"]

    assert _run(one(lambda c: c.get_info(), {})) == 2
    assert _run(one(lambda c: c.webhooks.list(), {"webhooks": []})) == 2
    assert _run(one(lambda c: c.webhooks.get("w1"), {"webhook": {}})) == 2
    assert _run(one(lambda c: c.webhooks.deliveries("w1"), {"deliveries": []})) == 2
    # a billed async GET is not retried on a proxy 524
    with pytest.raises(Exception) as info:
        _run(one(lambda c: c.list_document_versions("X"), {}))
    assert type(info.value).__name__ == "TimeoutError"


ASYNC_READ_ONLY = [
    lambda c: c.search("gdpr"),
    lambda c: c.get_document("32016R0679"),
    lambda c: c.get_documents_batch(["32016R0679"]),
    lambda c: c.get_document_metadata("32016R0679"),
    lambda c: c.get_document_by_url("https://eur-lex.europa.eu/x?uri=CELEX:32016R0679"),
    lambda c: c.resolve("32016R0679"),
    lambda c: c.semantic_search("q"),
    lambda c: c.semantic_legislation_search("q"),
]


@pytest.mark.parametrize("call", ASYNC_READ_ONLY)
def test_n2_async_read_only_posts_opt_in(call):
    async def run():
        calls = {"count": 0}

        def handler(request):
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(502, json={"error": "x"}, headers={"RateLimit-Limit": "1"})
            return httpx.Response(200, json={"results": []})

        async with make(handler, AsyncLexAPI, retry=FAST_RO) as client:
            await call(client)
        return calls["count"]

    assert _run(run()) == 2


def test_n2_async_webhook_writes_never_retried_under_opt_in():
    async def run():
        calls = {"count": 0}

        def handler(request):
            calls["count"] += 1
            return httpx.Response(502, json={"error": "x"}, headers={"RateLimit-Limit": "1"})

        async with make(handler, AsyncLexAPI, retry=FAST_RO) as client:
            for call in (
                lambda: client.webhooks.create("w", "https://e.com/h", {"documentType": "j"}),
                lambda: client.webhooks.test("w1"),
                lambda: client.webhooks.update("w1", name="x"),
            ):
                with pytest.raises(UpstreamError):
                    await call()
        return calls["count"]

    assert _run(run()) == 3


# ── N3: deprecation only on explicit user access ───────────────────────


def test_n3_no_warning_from_dataclass_helpers_repr_eq_copy():
    hook = Webhook.from_dict({"id": "w", "maxRetries": 3})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        as_dict = dataclasses.asdict(hook)
        assert as_dict["max_retries"] == 3
        dataclasses.astuple(hook)
        replaced = dataclasses.replace(hook, name="renamed")
        repr(hook)
        assert hook == Webhook.from_dict({"id": "w", "maxRetries": 3})
        copy.copy(hook)
        copy.deepcopy(hook)
        pickle.loads(pickle.dumps(hook))
    assert replaced.name == "renamed"
    with pytest.warns(DeprecationWarning) as record:
        assert hook.max_retries == 3
    assert record[0].filename == __file__  # attributed to the caller


# ── N4: parties_text ───────────────────────────────────────────────────


def test_n4_parties_text_for_three_parties():
    hit = SemanticCaseLawResult.from_dict(
        {"parties": ["Land Hessen", "TR", "European Commission"], "metadata": {}}
    )
    assert hit.parties_text == "Land Hessen; TR; European Commission"
    assert " v " not in hit.parties_text
    assert SemanticCaseLawResult.from_dict({"metadata": {}}).parties_text is None


# ── N6: iterating after a caller close always raises ───────────────────


def _stream_client():
    lines = [{"_meta": {}}, {"celex": "A"}, {"celex": "B"}, {"_done": {"written": 2}}]
    content = "".join(json.dumps(line) + "\n" for line in lines).encode()
    return LexAPI(
        api_key="k",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content)),
    )


def test_n6_close_before_iterating_raises():
    with _stream_client() as client:
        stream = client.export()
        stream.close()
        with pytest.raises(IncompleteExportError):
            list(stream)
        assert stream.complete is False


def test_n6_break_then_iterate_raises():
    with _stream_client() as client:
        stream = client.export()
        for _ in stream:
            break
        with pytest.raises(IncompleteExportError):
            list(stream)


def test_n6_with_block_exit_then_iterate_raises():
    with _stream_client() as client:
        with client.export() as stream:
            pass
        with pytest.raises(IncompleteExportError):
            list(stream)


def test_n6_fully_read_then_iterate_returns_empty():
    with _stream_client() as client:
        stream = client.export()
        assert [r["celex"] for r in stream] == ["A", "B"]
        assert list(stream) == []  # complete: nothing left, no error
        assert stream.complete is True


def test_n6_async_close_before_iterating_raises():
    async def run():
        async def handler(request):
            return httpx.Response(200, content=b'{"_meta": {}}\n{"_done": {}}\n')

        async with AsyncLexAPI(api_key="k", transport=httpx.MockTransport(handler)) as client:
            stream = await client.export()
            await stream.aclose()
            with pytest.raises(IncompleteExportError):
                [row async for row in stream]

    asyncio.run(run())
