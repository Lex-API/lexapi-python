"""Regression tests for the PR #11 review findings (R*, E*, W*, P1, M*, D1, K*)
and the PLAN.md §1 contract decisions. Each test fails on the pre-fix code."""

import asyncio
import json
import time
import warnings
from pathlib import Path

import httpx
import pytest

from conftest import FAST_RETRY, INFO_BODY
from lexapi import (
    AsyncLexAPI,
    IncompleteExportError,
    InvalidParamsError,
    InvalidResponseError,
    LexAPI,
    LexAPIConnectionError,
    RateLimitedError,
    RequestTimeoutError,
    RetryConfig,
    ServiceUnavailableError,
    TimeoutError,
    UpstreamError,
    WebhookSignatureError,
    verify_signature,
    verify_webhook,
)
from lexapi._payloads import seg
from lexapi._retry import is_app_response, parse_retry_after, server_wait_hint
from lexapi.errors import error_from_response

ROOT = Path(__file__).resolve().parent.parent


def counting(responder, **kwargs):
    calls = {"count": 0, "requests": []}

    def handler(request):
        calls["count"] += 1
        calls["requests"].append(request)
        return responder(request, calls["count"])

    kwargs.setdefault("retry_config", FAST_RETRY)
    client = LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler), **kwargs)
    return client, calls


def fail_then_ok(first, ok_body=None):
    ok_body = ok_body if ok_body is not None else INFO_BODY

    def responder(request, n):
        if n == 1:
            result = first(request)
            if isinstance(result, Exception):
                raise result
            return result
        return httpx.Response(200, json=ok_body)

    return responder


# ── R1: non-JSON 2xx ───────────────────────────────────────────────────


def test_r1_non_json_success_raises_instead_of_empty_result():
    client, _ = counting(lambda r, n: httpx.Response(200, html="<html>Just a moment...</html>"))
    with client:
        with pytest.raises(InvalidResponseError) as info:
            client.search("gdpr")
    assert info.value.code == "INVALID_RESPONSE"
    assert info.value.status == 200
    assert "Just a moment" in info.value.message


def test_r1_empty_and_204_bodies_still_succeed():
    client, _ = counting(lambda r, n: httpx.Response(204))
    with client:
        assert client.webhooks.delete("w1") is None
    client, _ = counting(lambda r, n: httpx.Response(200, content=b""))
    with client:
        assert dict(client.get_info()) == {}


# ── R2 + billing-safe transport retries (PLAN §1.2) ────────────────────


def _disconnect(request):
    return httpx.RemoteProtocolError(
        "Server disconnected without sending a response.", request=request
    )


def test_r2_stale_keepalive_disconnect_retried_for_billed_get():
    client, calls = counting(fail_then_ok(_disconnect, {"success": True, "versions": []}))
    with client:
        client.list_document_versions("32016R0679")
    assert calls["count"] == 2


def test_r2_stale_keepalive_disconnect_retried_even_for_post():
    client, calls = counting(fail_then_ok(_disconnect, {"results": []}))
    with client:
        client.search("gdpr")
    assert calls["count"] == 2


def test_post_send_read_error_not_retried_on_billed_get():
    def boom(request):
        return httpx.ReadError("connection reset mid-response", request=request)

    client, calls = counting(fail_then_ok(boom))
    with client:
        with pytest.raises(LexAPIConnectionError):
            client.citations.network("32016R0679")  # 20 credits; may have been charged
    assert calls["count"] == 1


def test_post_send_read_error_retried_on_free_get():
    def boom(request):
        return httpx.ReadError("connection reset mid-response", request=request)

    client, calls = counting(fail_then_ok(boom))
    with client:
        client.get_info()  # 0 credits
    assert calls["count"] == 2
    client, calls = counting(fail_then_ok(boom, {"success": True, "webhooks": []}))
    with client:
        client.webhooks.list()
    assert calls["count"] == 2


def test_pool_timeout_is_never_sent_so_retried():
    client, calls = counting(
        fail_then_ok(lambda r: httpx.PoolTimeout("no free connection", request=r))
    )
    with client:
        client.resolve("32016R0679")
    assert calls["count"] == 2


# ── 5xx origin rule ────────────────────────────────────────────────────


def test_proxy_502_not_retried_on_billed_get():
    client, calls = counting(lambda r, n: httpx.Response(502, text="Bad Gateway"))
    with client:
        with pytest.raises(UpstreamError):
            client.list_document_versions("32016R0679")
    assert calls["count"] == 1


def test_app_502_retried_on_billed_get_via_body_or_header():
    client, calls = counting(
        fail_then_ok(lambda r: httpx.Response(502, json={"error": "x"}), {"versions": []})
    )
    with client:
        client.list_document_versions("32016R0679")
    assert calls["count"] == 2
    client, calls = counting(
        fail_then_ok(
            lambda r: httpx.Response(502, text="x", headers={"X-LexAPI-Version": "2.1.0"}),
            {"versions": []},
        )
    )
    with client:
        client.list_document_versions("32016R0679")
    assert calls["count"] == 2


def test_proxy_524_retried_only_for_free_get():
    client, calls = counting(fail_then_ok(lambda r: httpx.Response(524, text="timeout")))
    with client:
        client.get_info()
    assert calls["count"] == 2
    client, calls = counting(lambda r, n: httpx.Response(524, text="timeout"))
    with client:
        with pytest.raises(TimeoutError):
            client.citations.cites("32016R0679")
    assert calls["count"] == 1


def test_cloudflare_522_never_reached_retried_for_any_method():
    client, calls = counting(fail_then_ok(lambda r: httpx.Response(522, text="cf"), {}))
    with client:
        client.webhooks.create("w", "https://e.com/h", watch_celex=["32008R1272"])
    assert calls["count"] == 2


def test_is_app_response():
    assert is_app_response(httpx.Headers({"X-LexAPI-Version": "2.1.0"}), None)
    assert is_app_response(httpx.Headers(), {"error": "x"})
    assert is_app_response(httpx.Headers(), {"success": False, "error": {"code": "INTERNAL"}})
    assert not is_app_response(httpx.Headers(), None)
    assert not is_app_response(httpx.Headers(), {"status": "shutting_down"})


# ── R7: retry_read_only_posts ──────────────────────────────────────────


APP_502 = lambda r: httpx.Response(502, json={"error": "Upstream service error"})  # noqa: E731


def test_r7_read_only_posts_not_retried_by_default():
    client, calls = counting(fail_then_ok(APP_502, {"results": []}))
    with client:
        with pytest.raises(UpstreamError):
            client.search("gdpr")
    assert calls["count"] == 1


def test_r7_opt_in_retries_app_origin_5xx_on_read_only_posts():
    config = RetryConfig(
        max_retries=3, backoff_base=0.0001, backoff_cap=0.001, retry_read_only_posts=True
    )
    for call in (
        lambda c: c.search("gdpr"),
        lambda c: c.get_document("32016R0679"),
        lambda c: c.get_documents_batch(["32016R0679"]),
        lambda c: c.get_document_metadata("32016R0679"),
        lambda c: c.get_document_by_url("https://eur-lex.europa.eu/x?uri=CELEX:32016R0679"),
        lambda c: c.resolve("32016R0679"),
        lambda c: c.semantic_search("q"),
        lambda c: c.semantic_legislation_search("q"),
    ):
        client, calls = counting(fail_then_ok(APP_502, {"results": []}), retry_config=config)
        with client:
            call(client)
        assert calls["count"] == 2


def test_r7_opt_in_never_retries_proxy_5xx_or_webhook_writes():
    config = RetryConfig(backoff_base=0.0001, backoff_cap=0.001, retry_read_only_posts=True)
    client, calls = counting(
        lambda r, n: httpx.Response(502, text="Bad Gateway"), retry_config=config
    )
    with client:
        with pytest.raises(UpstreamError):
            client.search("gdpr")  # origin unknown → may have been billed
    assert calls["count"] == 1
    for call in (
        lambda c: c.webhooks.create("w", "https://e.com/h", {"documentType": "judgment"}),
        lambda c: c.webhooks.test("w1"),
        lambda c: c.webhooks.update("w1", name="x"),
    ):
        client, calls = counting(lambda r, n: APP_502(r), retry_config=config)
        with client:
            with pytest.raises(UpstreamError):
                call(client)
        assert calls["count"] == 1


# ── R3: quota 429 retry_after ──────────────────────────────────────────


def test_r3_quota_429_has_no_rate_window_retry_after():
    body = {
        "error": "Daily quota exceeded",
        "message": "Resets at midnight UTC.",
        "usage": {"current": 1000, "limit": 1000, "resetsAt": "2026-10-27T00:00:00.000Z"},
    }
    client, _ = counting(
        lambda r, n: httpx.Response(429, json=body, headers={"RateLimit-Reset": "40"})
    )
    with client:
        with pytest.raises(RateLimitedError) as info:
            client.get_info()
    assert info.value.quota_exhausted is True
    assert info.value.retry_after is None
    assert info.value.resets_at == "2026-10-27T00:00:00.000Z"


# ── PLAN §1.2 wait details ─────────────────────────────────────────────


def test_empty_retry_after_falls_through():
    assert parse_retry_after("") is None
    assert parse_retry_after("   ") is None
    headers = httpx.Headers({"Retry-After": "", "RateLimit-Reset": "7"})
    assert server_wait_hint(headers, {}, 429) == 7.0
    assert server_wait_hint(httpx.Headers({"Retry-After": ""}), {}, 503) is None


def test_epoch_thresholds_seconds_and_millis():
    now = time.time()
    wait_s = server_wait_hint(httpx.Headers(), {"retryAfter": now + 60}, 429)
    wait_ms = server_wait_hint(httpx.Headers(), {"retryAfter": (now + 60) * 1000}, 429)
    assert 50 < wait_s < 70
    assert 50 < wait_ms < 70  # epoch milliseconds, not a 30 s clamp
    # values below 1e9 are delta-seconds, even large ones
    assert server_wait_hint(httpx.Headers(), {"retryAfter": 20_000_000}, 429) == 20_000_000


def test_x_ratelimit_reset_is_ignored():
    assert server_wait_hint(httpx.Headers({"X-RateLimit-Reset": "33"}), {}, 429) is None


# ── PLAN §1.3 error parsing ────────────────────────────────────────────


def test_503_is_service_unavailable():
    err = error_from_response(503, None)
    assert isinstance(err, ServiceUnavailableError)
    assert isinstance(err, UpstreamError)  # 0.1.0 callers keep catching it
    assert err.code == "SERVICE_UNAVAILABLE"


def test_details_kept_on_message_only_body():
    err = error_from_response(400, {"message": "bad", "details": ["a", "b"]})
    assert isinstance(err, InvalidParamsError)
    assert err.message == "bad"
    assert err.details == ["a", "b"]


def test_string_error_equal_to_a_slug_is_not_a_code():
    err = error_from_response(400, {"error": "NOT_FOUND"})
    assert err.code == "INVALID_PARAMS"


# ── E1 / E2: export stream ─────────────────────────────────────────────


class _ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks, exc=None):
        self.chunks, self.exc = chunks, exc

    def __iter__(self):
        yield from self.chunks
        if self.exc is not None:
            raise self.exc


def export_client(stream, headers=None):
    def handler(request):
        return httpx.Response(200, stream=stream, headers=headers or {})

    return LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))


def line(obj):
    return (json.dumps(obj) + "\n").encode()


def test_e1_mid_stream_transport_error_is_typed():
    stream = _ChunkStream([line({"_meta": {}}), line({"celex": "A"})], httpx.ReadError("reset"))
    with export_client(stream) as client:
        rows = []
        with pytest.raises(LexAPIConnectionError):
            for row in client.export():
                rows.append(row)
    assert rows == [{"celex": "A"}]

    stream = _ChunkStream([line({"_meta": {}})], httpx.ReadTimeout("stall"))
    with export_client(stream) as client:
        with pytest.raises(RequestTimeoutError):
            list(client.export())


def test_e1_missing_done_trailer_raises_after_rows():
    stream = _ChunkStream([line({"_meta": {}}), line({"celex": "A"}), b'{"celex": "B", "tit'])
    with export_client(stream) as client:
        export = client.export()
        rows = []
        with pytest.raises(IncompleteExportError) as info:
            for row in export:
                rows.append(row)
    assert rows == [{"celex": "A"}]
    assert export.complete is False
    assert export.invalid_lines == 1
    assert export.rows_read == 1
    assert "1 row" in info.value.message


def test_e1_complete_stream_is_complete():
    stream = _ChunkStream(
        [line({"_meta": {}}), line({"celex": "A"}), line({"_done": {"written": 1}})]
    )
    with export_client(stream) as client:
        export = client.export()
        assert list(export) == [{"celex": "A"}]
    assert export.complete is True
    assert export.invalid_lines == 0


def test_e1_async_missing_trailer():
    class _AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield line({"_meta": {}})
            yield line({"celex": "A"})

    async def run():
        async def handler(request):
            return httpx.Response(200, stream=_AsyncStream())

        async with AsyncLexAPI(api_key="k", transport=httpx.MockTransport(handler)) as client:
            export = await client.export()
            rows = []
            with pytest.raises(IncompleteExportError):
                async for row in export:
                    rows.append(row)
            return rows

    assert asyncio.run(run()) == [{"celex": "A"}]


def test_e2_skipped_stubs_are_not_truncation():
    lines = [
        line({"_meta": {"totalMatching": 3}}),
        line({"celex": "A"}),
        line({"_done": {"written": 1, "truncated": True, "skippedStubs": 2}}),
    ]
    with export_client(_ChunkStream(lines), headers={"X-Export-Total": "3"}) as client:
        export = client.export()
        list(export)
    assert export.truncated is False

    with export_client(_ChunkStream(lines), headers={"X-Export-Truncated": "0"}) as client:
        export = client.export()
        list(export)
    assert export.truncated is False  # the header is cap-only and wins

    capped = [line({"_meta": {"truncated": True}}), line({"_done": {"written": 1}})]
    with export_client(_ChunkStream(capped)) as client:
        export = client.export()
        list(export)
    assert export.truncated is True


# ── W1 / W3 / P1 ───────────────────────────────────────────────────────


def test_w1_non_ascii_signature_is_rejected_not_typeerror():
    raw = b'{"timestamp": "2026-10-05T11:35:50.493Z"}'
    assert verify_signature(raw, "é" * 64, "secret") is False
    with pytest.raises(WebhookSignatureError):
        verify_webhook(raw, "éè", "secret")


def test_w3_iter_deliveries_dedupes_shifted_rows():
    pages = {
        0: [{"id": "d3"}, {"id": "d2"}],
        2: [{"id": "d2"}, {"id": "d1"}],  # a new delivery arrived and shifted d2 down
        4: [],
    }

    def responder(request, n):
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"deliveries": pages[offset], "total": 5})

    client, _ = counting(responder)
    with client:
        ids = [d.id for d in client.webhooks.iter_deliveries("w1", page_size=2)]
    assert ids == ["d3", "d2", "d1"]


@pytest.mark.parametrize("bad", ["", ".", ".."])
def test_p1_dot_segments_rejected(bad):
    with pytest.raises(ValueError):
        seg(bad)
    client, calls = counting(lambda r, n: httpx.Response(200, json={}))
    with client:
        with pytest.raises(ValueError):
            client.webhooks.get(bad)
    assert calls["count"] == 0
    assert seg("...") == "..."  # only the exact dot-segments are special


# ── R5: with_options(connect_timeout=...) ──────────────────────────────


def test_r5_with_options_connect_timeout_alone_applies():
    client, calls = counting(lambda r, n: httpx.Response(200, json={}))
    with client:
        client.with_options(connect_timeout=1.5).get_info()
    timeout = calls["requests"][0].extensions["timeout"]
    assert timeout["connect"] == 1.5
    assert timeout["read"] == 95.0  # the client's current read timeout kept


def test_pool_timeout_defaults_to_connect_budget():
    client, calls = counting(lambda r, n: httpx.Response(200, json={}))
    with client:
        client.get_info()
    assert calls["requests"][0].extensions["timeout"]["pool"] == 10.0


# ── W2: deprecation warning ────────────────────────────────────────────


def test_w2_max_retries_warns():
    from lexapi import Webhook

    hook = Webhook.from_dict({"id": "w"})
    with pytest.warns(DeprecationWarning):
        assert hook.max_retries is None
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        repr(hook)  # repr / eq never touch the deprecated field
        assert hook == Webhook.from_dict({"id": "w"})


# ── D1 / M4 / M5 / K: copy and packaging ───────────────────────────────


def test_d1_no_per_row_export_price_in_docs():
    assert "per row" not in (LexAPI.export.__doc__ or "")
    assert "per row" not in (AsyncLexAPI.export.__doc__ or "")
    assert "per row" not in (ROOT / "README.md").read_text()


def test_m4_m5_docstrings_name_real_limits():
    assert "FREE 10" in (LexAPI.semantic_search.__doc__ or "")
    assert "ceil(limit / 10)" in (LexAPI.get_recent_documents.__doc__ or "")
    assert "1 for a live item" in (LexAPI.get_documents_batch.__doc__ or "")


def test_k_packaging_single_sourced_version_and_pins():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in pyproject
    assert 'path = "src/lexapi/_meta.py"' in pyproject
    assert '"hatchling>=1.27"' in pyproject
    assert "strict = true" in pyproject
    assert "](CHANGELOG.md)" not in (ROOT / "README.md").read_text()
    ci = ROOT / ".github/workflows/ci.yml"
    if ci.exists():  # not shipped in the sdist
        assert "ruff format --check" in ci.read_text()
