"""Integer headers read strictly, and deeply nested JSON never crashes the
interpreter (on interpreters older than 3.12 it is refused before parsing)."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import httpx
import pytest

from lexapi import (
    DocumentResponse,
    SemanticSearchResponse,
    WebhookSignatureError,
    compute_signature,
    verify_webhook,
)
from lexapi._json import GUARDED, MAX_DEPTH, too_deep
from lexapi._response import parse_header_int
from lexapi.models import parse_post_filtered_kept

INT_HEADERS = [
    ("0", 0),
    ("5", 5),
    ("005", 5),
    (" 5 ", 5),
    ("\t5\t", 5),
    ("9007199254740991", 9007199254740991),
    ("9007199254740992", None),
    ("1" * 17, None),
    ("1" * 400, None),
    ("0" * 20 + "5", 5),  # leading zeros, any length
    ("0" * 5000 + "9007199254740991", 9007199254740991),
    ("0" * 5000, 0),
    ("+5", None),
    ("-5", None),
    ("1_000", None),
    ("1e3", None),
    ("5.0", None),
    ("0x10", None),
    ("", None),
    (" ", None),
    ("５", None),
    ("5\n", None),
    (None, None),
]


@pytest.mark.parametrize("value, expected", INT_HEADERS)
def test_integer_header_table(value, expected):
    assert parse_header_int(value) == expected


@pytest.mark.parametrize("value, expected", [c for c in INT_HEADERS[:-1] if c[0].isascii()])
def test_integer_headers_on_responses(value, expected):
    headers = httpx.Headers(
        {"ratelimit-limit": value, "x-corpus-version": value, "x-national-limit": value}
    )
    resp = DocumentResponse({"success": True, "document": {}}, headers)
    assert resp.rate_limit_limit == expected
    assert resp.corpus_version == expected
    assert resp.national_allowance_limit == expected


def test_post_filter_counts_read_strictly():
    parsed = parse_post_filtered_kept("author: 3/10;date:+2/3;type:1e3/4;x:2/5")
    assert {k: (v.kept, v.before) for k, v in parsed.items()} == {"author": (3, 10), "x": (2, 5)}


def test_bool_case_law_score_reads_as_none():
    resp = SemanticSearchResponse({"success": True, "results": [{"score": True}]})
    assert resp.results[0].score is None


# ── depth guard ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, deep",
    [
        (b"[" * (MAX_DEPTH + 1) + b"]" * (MAX_DEPTH + 1), True),
        (b"[" * MAX_DEPTH + b"]" * MAX_DEPTH, False),
        (b'{"a":' * (MAX_DEPTH + 1) + b"1" + b"}" * (MAX_DEPTH + 1), True),
        (b'"' + b"[" * 2000 + b'"', False),  # brackets inside a string
        (b'["\\"' + b"[" * 2000 + b'"]', False),  # escaped quote stays in the string
        (b'["x\\\\"' + b"[" * 600, True),  # an escaped backslash ends the string
        ("[" * 600, True),
        (b"[]" * 5000, False),  # many brackets, shallow
    ],
)
def test_depth_guard(text, deep):
    assert too_deep(text) is deep  # the 512 cap applies on every Python version


DEEP_SCRIPT = textwrap.dedent(
    """
    import asyncio, sys
    import httpx
    from lexapi import (AsyncLexAPI, InternalServerError, InvalidResponseError, LexAPI,
                        RateLimitedError, compute_signature, verify_webhook,
                        WebhookSignatureError)

    LIMIT = int(sys.argv[1])
    if LIMIT:
        sys.setrecursionlimit(LIMIT)
    DEEP = b"[" * 100_000 + b"]" * 100_000
    EXPECTED = {200: InvalidResponseError, 500: InternalServerError, 429: RateLimitedError}

    def handler(request):
        status = int(request.url.params["s"])
        return httpx.Response(status, content=DEEP, headers={"content-type": "application/json"})

    def at_depth(n, fn):
        return fn() if n == 0 else at_depth(n - 1, fn)

    def sync_call(status):
        with LexAPI("k", base_url="http://127.0.0.1:9/api/v1",
                    transport=httpx.MockTransport(handler), max_retries=0) as c:
            try:
                c._request("GET", "/info", params={"s": status})
            except EXPECTED[status] as err:
                assert err.body is None, type(err.body)  # refused before parsing
                return "ok"

    async def async_call(status):
        async with AsyncLexAPI("k", base_url="http://127.0.0.1:9/api/v1",
                               transport=httpx.MockTransport(handler), max_retries=0) as c:
            try:
                await c._request("GET", "/info", params={"s": status})
            except EXPECTED[status] as err:
                assert err.body is None, type(err.body)  # refused before parsing
                return "ok"

    depth = min(950, sys.getrecursionlimit() - 50)
    for status in (200, 500, 429):
        assert at_depth(depth, lambda: sync_call(status)) == "ok"
        assert at_depth(depth, lambda: asyncio.run(async_call(status))) == "ok"

    def export_handler(request):
        tail = b'\\n{"celex": "X"}\\n{"_done": {"count": 1}}\\n'
        lines = b'{"_meta": {"total": 1}}\\n' + DEEP + tail
        return httpx.Response(200, content=lines, headers={"content-type": "application/x-ndjson"})

    def export_rows():
        with LexAPI("k", base_url="http://127.0.0.1:9/api/v1",
                    transport=httpx.MockTransport(export_handler), max_retries=0) as c:
            with c.export(limit=1) as stream:
                rows = list(stream)
                assert rows == [{"celex": "X"}] and stream.invalid_lines == 1
                return "ok"

    assert at_depth(depth, export_rows) == "ok"
    signature = compute_signature(DEEP, "s")
    try:
        at_depth(depth, lambda: verify_webhook(DEEP, signature, "s", tolerance=None))
    except WebhookSignatureError:
        pass
    print("survived")
    """
)


@pytest.mark.parametrize("recursion_limit", [0, 200_000])
def test_a_100k_deep_body_never_crashes_the_interpreter(recursion_limit):
    run = subprocess.run(
        [sys.executable, "-c", DEEP_SCRIPT, str(recursion_limit)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, (run.returncode, run.stderr[-2000:])
    assert run.stdout.strip() == "survived"


def test_a_deep_webhook_body_is_a_signature_error():
    body = b'{"a":' * 100_000 + b"1" + b"}" * 100_000
    with pytest.raises(WebhookSignatureError, match="not JSON"):
        verify_webhook(body, compute_signature(body, "s"), "s", tolerance=None)


# ── typed integers above 2**53 - 1 ───────────────────────────────────────


def test_integers_above_the_safe_range_read_as_absent():
    from lexapi import CitedByResponse, CreditsExhaustedError

    huge = 2**53
    resp = DocumentResponse(
        {
            "success": True,
            "document": {"version": huge},
            "credits": {"remaining": huge, "limit": 2.9, "operation_weight": -(2**60)},
            "usage": {"remaining": 9007199254740991},
        }
    )
    assert resp.document.version is None
    assert resp.credits is not None
    assert (resp.credits.remaining, resp.credits.limit, resp.credits.operation_weight) == (
        None,
        2,  # finite floats still truncate, as released
        None,
    )
    assert resp.usage is not None and resp.usage.remaining == 9007199254740991
    assert CitedByResponse({"success": True, "totalEdges": huge}).total_edges is None
    err = CreditsExhaustedError("x", details={"weight": huge, "available": 3})
    assert (err.weight, err.available) == (None, 3)


# ── timeouts ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [1e12, float("inf"), 10**400])
def test_timeouts_beyond_the_timer_are_clamped(value):
    from lexapi import AsyncLexAPI, LexAPI
    from lexapi._base import MAX_TIMEOUT_SECONDS

    for cls in (LexAPI, AsyncLexAPI):
        client = cls(
            "k", base_url="http://127.0.0.1:9/api/v1", timeout=value, connect_timeout=value
        )
        assert client._http.timeout.read == MAX_TIMEOUT_SECONDS
        assert client._http.timeout.connect == MAX_TIMEOUT_SECONDS
        view = client.with_options(timeout=value)
        assert view._timeout.read == MAX_TIMEOUT_SECONDS


@pytest.mark.parametrize("value", [float("nan"), -1, -0.5, True, "5"])
def test_unusable_timeouts_are_refused_at_construction(value):
    from lexapi import AsyncLexAPI, LexAPI

    for cls in (LexAPI, AsyncLexAPI):
        with pytest.raises(ValueError):
            cls("k", base_url="http://127.0.0.1:9/api/v1", timeout=value)
        with pytest.raises(ValueError):
            cls("k", base_url="http://127.0.0.1:9/api/v1", connect_timeout=value)


def test_a_huge_timeout_does_not_break_a_request():
    from lexapi import LexAPI

    def handler(request):
        return httpx.Response(200, json={"success": True, "service": "LexAPI"})

    with LexAPI(
        "k",
        base_url="http://127.0.0.1:9/api/v1",
        timeout=float("inf"),
        connect_timeout=1e12,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.get_info().service == "LexAPI"


# ── linear depth check ───────────────────────────────────────────────────

TRUNCATED_ENDINGS = {
    "escaped quotes": lambda n: b"[" * 600 + b'["' + b'\\"x' * (n // 3),
    "lone backslash": lambda n: b"[" * 600 + b'["' + b'\\"x' * (n // 3) + b"\\",
    "mid escape": lambda n: b"[" * 600 + b'["' + b"x\\" * (n // 2) + b"\\u12",
}


@pytest.mark.parametrize("size", [50_000, 1_000_000, 10_000_000])
@pytest.mark.parametrize("ending", sorted(TRUNCATED_ENDINGS))
def test_the_depth_check_is_linear_on_truncated_text(size, ending):
    import time

    from lexapi._json import nesting_depth

    data = TRUNCATED_ENDINGS[ending](size)
    start = time.perf_counter()
    nesting_depth(data)
    too_deep(data)
    assert time.perf_counter() - start < 2.0


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("size", [50_000, 1_000_000, 10_000_000])
def test_truncated_bodies_and_lines_answer_quickly(mode, size):
    import asyncio
    import time

    from lexapi import AsyncLexAPI, InvalidResponseError, LexAPI

    truncated = TRUNCATED_ENDINGS["escaped quotes"](size)

    def handler(request):
        if request.url.path.endswith("/export"):
            content = b'{"_meta": {}}\n' + truncated + b'\n{"_done": {"written": 0}}\n'
            return httpx.Response(
                200, content=content, headers={"content-type": "application/x-ndjson"}
            )
        return httpx.Response(200, content=truncated, headers={"content-type": "application/json"})

    kw = {"base_url": "http://127.0.0.1:9/api/v1", "transport": httpx.MockTransport(handler)}
    start = time.perf_counter()
    if mode == "sync":
        with LexAPI("k", max_retries=0, **kw) as c:
            with pytest.raises(InvalidResponseError):
                c.get_info()
            with c.export(limit=1) as stream:
                assert list(stream) == [] and stream.invalid_lines == 1
    else:

        async def go():
            async with AsyncLexAPI("k", max_retries=0, **kw) as c:
                with pytest.raises(InvalidResponseError):
                    await c.get_info()
                stream = await c.export(limit=1)
                async with stream:
                    assert [r async for r in stream] == [] and stream.invalid_lines == 1

        asyncio.run(asyncio.wait_for(go(), 10))
    with pytest.raises(WebhookSignatureError):
        verify_webhook(truncated, compute_signature(truncated, "s"), "s", tolerance=None)
    assert time.perf_counter() - start < 5.0


def test_the_depth_check_costs_little_next_to_the_parser():
    import json
    import time

    from lexapi._json import nesting_depth

    article = {"id": "art_1", "title": "Title [x] {y}", "content": 'Lorem "q" \\ [1] {2} ' * 40}
    doc = {"document": {"articles": [dict(article, id=f"art_{i}") for i in range(6000)]}}
    body = json.dumps(doc).encode()  # several MB
    best_check = best_parse = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        nesting_depth(body)
        best_check = min(best_check, time.perf_counter() - start)
        start = time.perf_counter()
        json.loads(body)
        best_parse = min(best_parse, time.perf_counter() - start)
    assert best_check <= 5 * best_parse  # measured about 1.7x


# ── one parse per response ───────────────────────────────────────────────

STACK_SCRIPT = textwrap.dedent(
    """
    import sys
    import httpx
    from lexapi import LexAPI, LexAPIError
    from lexapi._retry import RetryConfig

    def at(n, f):
        return f() if n == 0 else at(n - 1, f)

    for levels in (100, 300, 512):
        body = b"[" * levels + b"]" * levels
        for status in (429, 500):
            for retries in (0, 3):
                def handler(request, body=body, status=status):
                    return httpx.Response(status, content=body,
                                          headers={"content-type": "application/json"})
                def call(retries=retries, handler=handler):
                    config = RetryConfig(max_retries=retries, backoff_base=0, backoff_cap=0)
                    with LexAPI("k", base_url="http://127.0.0.1:9/api/v1",
                                transport=httpx.MockTransport(handler),
                                retry_config=config) as c:
                        try:
                            c.get_info()
                        except LexAPIError as err:
                            return type(err).__name__
                for depth in (930, 944, 956, 968):
                    at(depth, call)
    print("survived")
    """
)


@pytest.mark.skipif(not GUARDED, reason="the parser has its own C-stack limit from 3.12")
def test_error_bodies_from_a_deep_stack_never_abort():
    run = subprocess.run(
        [sys.executable, "-c", STACK_SCRIPT], capture_output=True, text=True, timeout=300
    )
    assert run.returncode == 0, (run.returncode, run.stderr[-1500:])
    assert run.stdout.strip() == "survived"


def test_a_response_body_is_parsed_once(monkeypatch):
    from lexapi import LexAPI, RateLimitedError, _base

    calls = {"n": 0}
    real = _base.json.loads

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(_base.json, "loads", counting)

    def handler(request):
        return httpx.Response(429, json={"error": "x", "code": "RATE_LIMITED"})

    with LexAPI(
        "k",
        base_url="http://127.0.0.1:9/api/v1",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    ) as client:
        with pytest.raises(RateLimitedError):
            client.get_info()
    assert calls["n"] == 1


# ── webhook body encoding ────────────────────────────────────────────────

ENCODINGS = ["utf-16-le", "utf-16-be", "utf-16", "utf-32-le", "utf-32-be", "utf-32"]


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("deep", [False, True])
def test_a_webhook_body_that_is_not_utf8_is_not_json(encoding, deep):
    text = '{"event": "webhook.test", "data": ' + ("[" * 5000 + "]" * 5000 if deep else "1") + "}"
    body = text.encode(encoding)  # "utf-16" / "utf-32" add a byte order mark
    with pytest.raises(WebhookSignatureError, match="not JSON"):
        verify_webhook(body, compute_signature(body, "s"), "s", tolerance=None)


def test_a_webhook_body_with_a_utf8_byte_order_mark_is_read():
    body = b'\xef\xbb\xbf{"event": "webhook.test"}'
    assert verify_webhook(body, compute_signature(body, "s"), "s", tolerance=None) == {
        "event": "webhook.test"
    }


ENCODING_SCRIPT = textwrap.dedent(
    """
    import sys
    from lexapi import WebhookSignatureError, compute_signature, verify_webhook

    if int(sys.argv[1]):
        sys.setrecursionlimit(int(sys.argv[1]))

    def at(n, f):
        return f() if n == 0 else at(n - 1, f)

    text = '{"a": ' + "[" * 100_000 + "]" * 100_000 + "}"
    for encoding in ("utf-16-le", "utf-16-be", "utf-16", "utf-32-le", "utf-32-be", "utf-32"):
        body = text.encode(encoding)
        signature = compute_signature(body, "s")
        def check(body=body, signature=signature):
            try:
                verify_webhook(body, signature, "s", tolerance=None)
            except WebhookSignatureError:
                return "ok"
        assert at(min(950, sys.getrecursionlimit() - 50), check) == "ok"
    print("survived")
    """
)


@pytest.mark.skipif(not GUARDED, reason="the parser has its own C-stack limit from 3.12")
@pytest.mark.parametrize("recursion_limit", [0, 200_000])
def test_deep_webhook_bodies_in_other_encodings_never_abort(recursion_limit):
    run = subprocess.run(
        [sys.executable, "-c", ENCODING_SCRIPT, str(recursion_limit)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, (run.returncode, run.stderr[-1500:])
    assert run.stdout.strip() == "survived"


# ── the refusal boundary ─────────────────────────────────────────────────


def _pinned(monkeypatch, limit, frames):
    from lexapi import _json

    monkeypatch.setattr(_json, "GUARDED", True)
    monkeypatch.setattr(_json.sys, "getrecursionlimit", lambda: limit)
    monkeypatch.setattr(_json, "_stack_depth", lambda: frames)
    return _json


@pytest.mark.parametrize("limit", [1000, 3000])
@pytest.mark.parametrize("depth", [10, 300])
def test_the_refusal_boundary_is_limit_minus_depth_minus_the_reserve(monkeypatch, limit, depth):
    # Shallow brackets in front make the scan run (more brackets than room).
    text = b"[]" * 600 + b"[" * depth + b"]" * depth
    boundary = limit - depth - 5  # the reserve is 5
    module = _pinned(monkeypatch, limit, boundary)
    assert module.RESERVED == 5
    assert module.too_deep(text) is False
    module = _pinned(monkeypatch, limit, boundary + 1)
    assert module.too_deep(text) is True


def test_depth_512_is_parsed_and_513_refused(monkeypatch):
    module = _pinned(monkeypatch, 100_000, 10)
    for prefix in (b"", b"[]" * 600):  # with and without the scan
        assert module.too_deep(prefix + b"[" * 512 + b"]" * 512) is False
        assert module.too_deep(prefix + b"[" * 513 + b"]" * 513) is True


@pytest.mark.parametrize("frames", [990, 1000, 5000])
def test_a_text_without_brackets_is_never_refused(monkeypatch, frames):
    module = _pinned(monkeypatch, 1000, frames)
    for text in (b'"x"', b"123", b"", "true", b'"[" ' * 3):
        if b"[" not in (text if isinstance(text, bytes) else text.encode()):
            assert module.too_deep(text) is False


def test_the_depth_scan_memory_stays_a_small_multiple_of_the_text():
    import tracemalloc

    from lexapi._json import nesting_depth

    for data in (b'"' * 5_000_000, b'""' * 2_500_000, b'"[{x}]"' * 700_000):
        tracemalloc.start()
        nesting_depth(data)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak <= 3 * len(data), (peak, len(data))


def test_timeouts_accept_any_real_number():
    import decimal
    import fractions

    from lexapi import LexAPI

    for value, expected in [
        (decimal.Decimal("2.5"), 2.5),
        (fractions.Fraction(5, 2), 2.5),
        (decimal.Decimal("Infinity"), 2_147_483.647),
    ]:
        client = LexAPI("k", base_url="http://127.0.0.1:9/api/v1", timeout=value)
        assert client._http.timeout.read == expected
        client.close()
    with pytest.raises(ValueError):
        LexAPI("k", base_url="http://127.0.0.1:9/api/v1", timeout=decimal.Decimal("NaN"))


# ── a small thread stack ─────────────────────────────────────────────────

THREAD_SCRIPT = textwrap.dedent(
    """
    import asyncio, sys, threading
    import httpx
    from lexapi import (AsyncLexAPI, InternalServerError, InvalidResponseError, LexAPI,
                        RateLimitedError, WebhookSignatureError, compute_signature,
                        verify_webhook)

    DEEP = b"[" * 100_000 + b"]" * 100_000
    EXPECTED = {200: InvalidResponseError, 500: InternalServerError, 429: RateLimitedError}
    BASE = "http://127.0.0.1:9/api/v1"
    results = []

    def handler(request):
        if request.url.path.endswith("/export"):
            lines = b'{"_meta": {}}\\n' + DEEP + b'\\n{"_done": {}}\\n'
            return httpx.Response(200, content=lines,
                                  headers={"content-type": "application/x-ndjson"})
        status = int(request.url.params["s"])
        return httpx.Response(status, content=DEEP, headers={"content-type": "application/json"})

    def work():
        transport = httpx.MockTransport(handler)
        with LexAPI("k", base_url=BASE, transport=transport, max_retries=0) as c:
            for status in (200, 500, 429):
                try:
                    c._request("GET", "/info", params={"s": status})
                except EXPECTED[status] as err:
                    assert err.body is None
                    results.append(("sync", status))
            with c.export(limit=1) as stream:
                assert list(stream) == [] and stream.invalid_lines == 1
                results.append(("export", "sync"))

        async def go():
            async with AsyncLexAPI("k", base_url=BASE, transport=transport, max_retries=0) as c:
                for status in (200, 500, 429):
                    try:
                        await c._request("GET", "/info", params={"s": status})
                    except EXPECTED[status] as err:
                        assert err.body is None
                        results.append(("async", status))
                stream = await c.export(limit=1)
                async with stream:
                    assert [r async for r in stream] == [] and stream.invalid_lines == 1
                    results.append(("export", "async"))

        asyncio.run(go())
        try:
            verify_webhook(DEEP, compute_signature(DEEP, "s"), "s", tolerance=None)
        except WebhookSignatureError:
            results.append(("webhook",))

    threading.stack_size(256 * 1024)
    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    assert len(results) == 9, results
    print("survived")
    """
)


def test_a_deep_body_in_a_thread_with_a_small_stack_never_aborts():
    run = subprocess.run(
        [sys.executable, "-c", THREAD_SCRIPT], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, (run.returncode, run.stderr[-1500:])
    assert run.stdout.strip() == "survived"


# ── the scan across chunks ───────────────────────────────────────────────


def test_a_string_open_across_a_chunk_boundary_hides_its_brackets():
    from lexapi._json import _CHUNK, nesting_depth

    # The string opens in the first chunk and closes in the third; the
    # brackets inside it must not count.
    text = b'["' + b"[" * (2 * _CHUNK) + b'"]'
    assert nesting_depth(text) == 1


def test_depth_carries_into_the_peak_across_chunks():
    from lexapi._json import _CHUNK, nesting_depth

    # 300 levels open in chunk 1, 300 more open in chunk 2: the peak is 600.
    # The scan keeps only quotes and brackets, so the padding is brackets
    # inside a string: it fills the rest of the first chunk.
    pad = b'"' + b"[" * (_CHUNK - 400) + b'"'
    text = b"[" * 300 + pad + b"[" * 300 + b"]" * 600
    assert nesting_depth(text) == 600


def test_depth_accumulates_across_three_chunks():
    from lexapi._json import _CHUNK, nesting_depth

    pad = b'"' + b"{" * _CHUNK + b'"'  # brackets in a string: a whole chunk each
    text = b"[" * 200 + pad + b"[" * 200 + pad + b"[" * 200 + pad + b"]" * 600
    assert nesting_depth(text) == 600
    assert too_deep(text) is True


# ── invalid UTF-8 in a webhook body ──────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        b"\xff",
        b"\xed\xa0\x80",  # an encoded surrogate (U+D800)
        b"\xc0\xaf",  # an overlong "/"
        b"\xe2\x82",  # a truncated three-byte sequence
    ],
)
def test_invalid_utf8_inside_a_webhook_string_is_not_json(bad):
    body = b'{"event": "webhook.test", "note": "a' + bad + b'b"}'
    with pytest.raises(WebhookSignatureError, match="not JSON"):
        verify_webhook(body, compute_signature(body, "s"), "s", tolerance=None)
