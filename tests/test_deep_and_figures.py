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
    assert too_deep(text) is (deep and GUARDED)


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
                assert err.body is None, err.body
                return "ok"

    async def async_call(status):
        async with AsyncLexAPI("k", base_url="http://127.0.0.1:9/api/v1",
                               transport=httpx.MockTransport(handler), max_retries=0) as c:
            try:
                await c._request("GET", "/info", params={"s": status})
            except EXPECTED[status] as err:
                assert err.body is None, err.body
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
