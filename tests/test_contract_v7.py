"""Rules of the shared SDK contract (export lines, quota 429s, past-due
retries, webhook verification, page-size bounds) that both SDKs implement
the same way."""

from __future__ import annotations

import asyncio
import math
import pickle
import random

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    IncompleteExportError,
    LexAPI,
    PaymentPastDueError,
    RateLimitedError,
    WebhookSignatureError,
    compute_signature,
    verify_signature,
    verify_webhook,
)
from lexapi._retry import RetryConfig, is_quota_exhausted
from lexapi.errors import error_from_response

LOOPBACK = "http://127.0.0.1:9/api/v1"
FAST = RetryConfig(max_retries=2, backoff_base=0.0001, backoff_cap=0.001)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


# ── export streams ───────────────────────────────────────────────────────


class Chunked(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def export(mode, content, chunks=None):
    """(rows, invalid_lines, meta, done, error) for an export body."""
    stream = Chunked(chunks if chunks is not None else [content])

    def handler(request):
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, stream=stream, headers={"content-type": "application/x-ndjson"})

    transport = httpx.MockTransport(handler)
    if mode == "sync":
        with LexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
            with c.export(limit=1) as s:
                try:
                    rows = list(s)
                    error = None
                except IncompleteExportError as err:
                    rows, error = None, err
                return rows, s.invalid_lines, s.meta, s.done, error, s.truncated

    async def go():
        async with AsyncLexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
            s = await c.export(limit=1)
            async with s:
                try:
                    rows = [r async for r in s]
                    error = None
                except IncompleteExportError as err:
                    rows, error = None, err
                return rows, s.invalid_lines, s.meta, s.done, error, s.truncated

    return asyncio.run(asyncio.wait_for(go(), 10))


MODES = ["sync", "async"]
DONE = b'{"_done": {"written": 1}}\n'


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_nan_and_infinity_make_an_invalid_line(mode, token):
    body = b'{"_meta": {}}\n{"celex": "A", "n": ' + token + b'}\n{"celex": "B"}\n' + DONE
    rows, invalid, *_ = export(mode, body)
    assert rows == [{"celex": "B"}] and invalid == 1


@pytest.mark.parametrize("mode", MODES)
def test_a_done_line_with_nan_is_an_incomplete_export(mode):
    body = b'{"_meta": {}}\n{"celex": "A"}\n{"_done": {"written": NaN}}\n'
    rows, invalid, meta, done, error, _ = export(mode, body)
    assert error is not None and invalid == 1 and done is None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "line, invalid",
    [
        (b' \t{"celex": "A"}\r \t', 0),  # space, tab, CR trimmed
        (' {"celex": "A"}'.encode(), 1),  # no-break space is not trimmed
        ('{"celex": "A"}　'.encode(), 1),
        (b'\x0b{"celex": "A"}', 1),  # vertical tab is not trimmed
        (b'{"celex": "A"}\x0c', 1),  # form feed is not trimmed
        (b'\x1c{"celex": "A"}', 1),
    ],
)
def test_only_spaces_tabs_and_carriage_returns_are_trimmed(mode, line, invalid):
    body = b'{"_meta": {}}\n' + line + b"\n" + DONE
    rows, got_invalid, *_ = export(mode, body)
    assert got_invalid == invalid
    assert rows == ([] if invalid else [{"celex": "A"}])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("separator", [" ", " ", "\u0085", "\r", "\x0b", "\x0c"])
def test_only_line_feed_ends_a_line(mode, separator):
    row = ('{"celex": "A", "t": "x' + separator + 'y"}').encode()
    if separator in ("\r", "\x0b", "\x0c"):
        row = ('{"celex": "A", "t": "x"}' + separator + '{"celex": "B"}').encode()
    body = b'{"_meta": {}}\n' + row + b"\n" + DONE
    rows, invalid, *_ = export(mode, body)
    if separator in ("\r", "\x0b", "\x0c"):
        assert rows == [] and invalid == 1  # one line holding two values
    else:
        assert rows == [{"celex": "A", "t": "x" + separator + "y"}] and invalid == 0


@pytest.mark.parametrize("mode", MODES)
def test_meta_lines_are_never_rows(mode):
    body = (
        b'{"_meta": {"total": 2}}\n{"celex": "A"}\n{"_meta": {"total": 9}}\n'
        b'{"_meta": true, "x": 1}\n{"celex": "B"}\n' + DONE
    )
    rows, invalid, meta, *_ = export(mode, body)
    assert rows == [{"celex": "A"}, {"celex": "B"}]
    assert invalid == 0 and meta == {"total": 2}


@pytest.mark.parametrize("mode", MODES)
def test_meta_counts_only_as_the_first_line(mode):
    body = b'\n{"_meta": {"total": 2}}\n{"celex": "A"}\n' + DONE
    rows, invalid, meta, *_ = export(mode, body)
    assert rows == [{"celex": "A"}] and invalid == 0 and meta is None


@pytest.mark.parametrize("mode", MODES)
def test_truncated_is_false_without_any_signal(mode):
    body = b'{"_meta": {}}\n{"celex": "A"}\n' + DONE
    assert export(mode, body)[5] is False


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "body, rows, invalid",
    [
        # mid-stream byte order mark: part of its line
        (b'{"_meta": {}}\n\xef\xbb\xbf{"celex": "A"}\n{"celex": "B"}\n' + DONE, ["B"], 1),
        # blank line, then a byte order mark: not at the very start
        (b'\n\xef\xbb\xbf{"_meta": {}}\n{"celex": "A"}\n' + DONE, ["A"], 1),
        # the very start
        (b'\xef\xbb\xbf{"_meta": {}}\n{"celex": "A"}\n' + DONE, ["A"], 0),
        # a lone carriage return between values is not a line break
        (b'{"_meta": {}}\n{"celex": "A"}\r{"celex": "B"}\n' + DONE, [], 1),
        # first-line flag: a non-UTF-8 first line is invalid, the next is read
        (b'\xff\n{"celex": "A"}\n' + DONE, ["A"], 1),
    ],
)
def test_byte_order_marks_and_carriage_returns(mode, body, rows, invalid):
    got_rows, got_invalid, *_ = export(mode, body)
    assert [r["celex"] for r in got_rows] == rows and got_invalid == invalid


STREAM = (
    b'\xef\xbb\xbf{"_meta": {"total": 3}}\n'
    b'{"celex": "A", "t": "\xc3\xa9\xe2\x80\xa8"}\r\n'
    b"\n"
    b'{"celex": "B"}\n'
    b'{"celex": "C", "x": [1, 2, 3]}\n'
    b'{"_done": {"written": 3}}'
)
EXPECTED = [{"celex": "A", "t": "é "}, {"celex": "B"}, {"celex": "C", "x": [1, 2, 3]}]


def _chunks(data, sizes):
    out, i = [], 0
    for size in sizes:
        out.append(data[i : i + size])
        i += size
        if i >= len(data):
            break
    if i < len(data):
        out.append(data[i:])
    return out


@pytest.mark.parametrize("mode", MODES)
def test_a_stream_split_into_single_bytes(mode):
    rows, invalid, meta, done, error, _ = export(mode, None, [bytes([b]) for b in STREAM])
    assert rows == EXPECTED and invalid == 0 and meta == {"total": 3}
    assert done == {"written": 3} and error is None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("seed", range(12))
def test_a_stream_split_at_random_points(mode, seed):
    rnd = random.Random(seed)
    chunks = _chunks(STREAM, [rnd.randint(0, 9) for _ in range(len(STREAM))])
    rows, invalid, meta, done, error, _ = export(mode, None, chunks)
    assert rows == EXPECTED and invalid == 0 and done == {"written": 3}


@pytest.mark.parametrize("mode", MODES)
def test_the_last_line_without_a_line_feed_is_read_once(mode):
    chunks = [b'{"_meta": {}}\n{"celex": "A"}\n{"_do', b'ne": ', b"{}}"]
    rows, invalid, meta, done, error, _ = export(mode, None, chunks)
    assert rows == [{"celex": "A"}] and done == {} and error is None and invalid == 0


# ── quota 429s and past-due retries ──────────────────────────────────────


@pytest.mark.parametrize(
    "body, code, quota",
    [
        ({"error": "x", "code": "QUOTA_EXHAUSTED"}, "QUOTA_EXHAUSTED", True),
        (
            {"code": "QUOTA_EXHAUSTED", "error": {"code": "INTERNAL", "message": "x"}},
            "QUOTA_EXHAUSTED",
            True,
        ),
        (
            {"code": "QUOTA_EXHAUSTED", "error": {"code": "RATE_LIMITED", "message": "x"}},
            "RATE_LIMITED",
            True,
        ),
        ({"error": {"code": "QUOTA_EXHAUSTED", "message": "x"}}, "QUOTA_EXHAUSTED", True),
        # the shape test only with the code RATE_LIMITED, sent or derived
        ({"error": "x", "usage": {"resetsAt": "2026-12-01T00:00:00Z"}}, "RATE_LIMITED", True),
        (
            {"error": "x", "code": "RATE_LIMITED", "usage": {"resetsAt": "2026-12-01T00:00:00Z"}},
            "RATE_LIMITED",
            True,
        ),
        (
            {"error": "x", "code": "SOMETHING_NEW", "usage": {"resetsAt": "2026-12-01T00:00:00Z"}},
            "SOMETHING_NEW",
            False,
        ),
        (
            {
                "error": {"code": "INTERNAL", "message": "x"},
                "usage": {"resetsAt": "2026-12-01T00:00:00Z"},
            },
            "INTERNAL",
            False,
        ),
    ],
)
def test_quota_429_codes(body, code, quota):
    err = error_from_response(429, body)
    assert err.code == code
    assert is_quota_exhausted(429, httpx.Headers(), body) is quota
    if quota:
        assert isinstance(err, RateLimitedError) and err.quota_exhausted is True
    else:
        assert not (isinstance(err, RateLimitedError) and err.quota_exhausted)


@pytest.mark.parametrize("status", [400, 403, 429, 500])
def test_the_quota_flag_follows_the_code_on_any_status(status):
    err = error_from_response(status, {"error": "x", "code": "QUOTA_EXHAUSTED"})
    assert isinstance(err, RateLimitedError) and err.quota_exhausted is True


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "body",
    [
        {"error": "Payment past due", "code": "PAYMENT_PAST_DUE", "billingUrl": "https://x"},
        {"success": False, "error": {"code": "PAYMENT_PAST_DUE", "message": "x"}},
    ],
)
def test_payment_past_due_is_never_retried_even_on_a_429(mode, body):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json=body, headers={"retry-after": "0"})

    transport = httpx.MockTransport(handler)
    if mode == "sync":
        with LexAPI("k", base_url=LOOPBACK, transport=transport, retry_config=FAST) as c:
            with pytest.raises(PaymentPastDueError):
                c.get_info()
    else:

        async def go():
            async with AsyncLexAPI(
                "k", base_url=LOOPBACK, transport=transport, retry_config=FAST
            ) as c:
                with pytest.raises(PaymentPastDueError):
                    await c.get_info()

        asyncio.run(go())
    assert calls["n"] == 1


# ── webhook verification ─────────────────────────────────────────────────

BODY = b'{"event": "webhook.test", "timestamp": "2026-11-19T10:00:00Z"}'
NOW = 1_795_082_400.0  # 2026-11-19T10:00:00Z
SIG = compute_signature(BODY, "s")


@pytest.mark.parametrize(
    "header, ok",
    [
        (SIG, True),
        (SIG.upper(), True),
        (" \t" + SIG + "\r\n", True),
        ("\u0085" + SIG, False),
        (SIG + " ", False),
        ("﻿" + SIG, False),
        ("\x1c" + SIG, False),
        (SIG + "\x1f", False),
        (SIG + "\x0b", False),
        (SIG[:-1], False),
        (SIG + "0", False),
        ("sha256=" + SIG, False),
        ("", False),
        (None, False),
    ],
)
def test_signature_header_trimming_and_shape(header, ok):
    assert verify_signature(BODY, header, "s") is ok


@pytest.mark.parametrize("secret", ["", b""])
def test_an_empty_secret_raises_from_every_helper(secret):
    for call in (
        lambda: compute_signature(BODY, secret),
        lambda: verify_signature(BODY, SIG, secret),
        lambda: verify_webhook(BODY, SIG, secret, tolerance=None),
        lambda: verify_webhook(BODY, None, secret, tolerance=None),  # secret first
    ):
        with pytest.raises(WebhookSignatureError) as info:
            call()
        assert info.value.reason == "missing_secret"


def test_a_secret_that_cannot_be_encoded_raises():
    for call in (
        lambda: compute_signature(BODY, "s\ud800"),
        lambda: verify_signature(BODY, SIG, "s\ud800"),
    ):
        with pytest.raises(WebhookSignatureError) as info:
            call()
        assert info.value.reason == "missing_secret"


@pytest.mark.parametrize(
    "tolerance, ok", [(float("inf"), True), (-1, False), (float("-inf"), False)]
)
def test_infinite_and_negative_windows(tolerance, ok):
    if ok:
        verify_webhook(BODY, SIG, "s", tolerance=tolerance, now=NOW + 10**9)
    else:
        assert _reason(BODY, SIG, tolerance=tolerance) == "stale_timestamp"


def _reason(payload, header, *, tolerance=None, now=NOW):
    with pytest.raises(WebhookSignatureError) as info:
        verify_webhook(payload, header, "s", tolerance=tolerance, now=now)
    return info.value.reason


def test_the_order_of_checks_and_their_reasons():
    assert _reason(BODY, None) == "missing_signature"
    assert _reason(BODY, "") == "missing_signature"
    assert _reason(b"not json", None) == "missing_signature"  # header before body
    assert _reason(b"not json", "0" * 64) == "invalid_signature"  # signature before body
    bad = b"[1, 2]"
    assert _reason(bad, compute_signature(bad, "s")) == "malformed_body"
    nan = b'{"timestamp": NaN}'
    assert _reason(nan, compute_signature(nan, "s"), tolerance=300) == "malformed_body"
    old = b'{"timestamp": "2026-11-19T09:00:00Z"}'
    assert _reason(old, compute_signature(old, "s"), tolerance=300) == "stale_timestamp"
    lone = '{"timestamp": "\ud800"}'
    assert _reason(lone, "0" * 64) == "invalid_signature"  # never malformed_body


def test_reason_survives_pickle():
    err = WebhookSignatureError("invalid webhook signature", reason="invalid_signature")
    again = pickle.loads(pickle.dumps(err))
    assert again.reason == "invalid_signature" and str(again) == str(err)
    assert WebhookSignatureError("x").reason == "invalid_signature"


@pytest.mark.parametrize(
    "stamp, ok",
    [
        ("2026-11-19T10:00:00Z", True),
        ("2026-11-19T10:00:00.5Z", True),
        ("2026-11-19T10:00:00.123456789Z", True),
        ("2026-11-19T11:00:00+01:00", True),
        ("2026-11-19T09:00:00.25-01:00", True),
        ("2026-11-19T10:00:00", False),  # no zone
        ("2026-11-19 10:00:00Z", False),
        ("2026-11-19t10:00:00Z", False),
        ("2026-11-19T10:00:00z", False),
        ("2026-11-19T10:00Z", False),
        ("2026-11-19T10:00:00.Z", False),
        ("2026-11-19T10:00:00+0100", False),
        ("2026-11-19T10:00:00+01", False),
        ("20261119T100000Z", False),
        ("2026-11-19T10:00:00Ｚ", False),
        ("2026-02-30T10:00:00Z", False),
        ("2026-11-19T24:00:00Z", False),
        ("2026-11-19T10:00:00+24:00", False),
        (" 2026-11-19T10:00:00Z", False),
        (1795082400, False),
    ],
)
def test_timestamp_format(stamp, ok):
    import json

    body = json.dumps({"timestamp": stamp}).encode()
    sig = compute_signature(body, "s")
    if ok:
        assert verify_webhook(body, sig, "s", tolerance=60, now=NOW)["timestamp"] == stamp
    else:
        assert _reason(body, sig, tolerance=60) == "stale_timestamp"


def test_a_nan_tolerance_is_refused():
    with pytest.raises(ValueError) as info:
        verify_webhook(BODY, SIG, "s", tolerance=math.nan, now=NOW)
    assert type(info.value) is ValueError  # a usage error, not a verification failure


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", [403, 500])
def test_the_client_sets_the_quota_flag_from_the_code_on_any_status(mode, status):
    body = {"error": "x", "code": "QUOTA_EXHAUSTED"}

    def handler(request):
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    if mode == "sync":
        with LexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
            with pytest.raises(RateLimitedError) as info:
                c.get_info()
    else:

        async def go():
            async with AsyncLexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
                await c.get_info()

        with pytest.raises(RateLimitedError) as info:
            asyncio.run(go())
    assert info.value.quota_exhausted is True and info.value.status == status


# ── page-size bounds ─────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("bad", [2**53, 2**60])
def test_page_sizes_above_the_safe_range_are_refused(mode, bad):
    def handler(request):
        raise AssertionError("no request may be sent")

    transport = httpx.MockTransport(handler)
    starts = [
        lambda c: c.citations.iter_cited_by("X", page_size=bad),
        lambda c: c.webhooks.iter_deliveries("wh_1", page_size=bad),
        lambda c: c.national.iter_documents("AT", limit=bad),
    ]
    for start in starts:
        if mode == "sync":
            with LexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
                with pytest.raises(ValueError):
                    list(start(c))
        else:

            async def go(start=start):
                async with AsyncLexAPI(
                    "k", base_url=LOOPBACK, transport=transport, max_retries=0
                ) as c:
                    with pytest.raises(ValueError):
                        [x async for x in start(c)]

            asyncio.run(go())


def test_the_largest_safe_page_size_is_accepted():
    from lexapi._paging import check_page_size

    assert check_page_size("page_size", 2**53 - 1) == 2**53 - 1


# ── round 3: resolved codes, origins, secrets, envelopes, the replay edge ──


def _calls(mode, status, body, headers=None, call=lambda c: c.get_info(), retry=FAST):
    """(error, number of requests)."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status, json=body, headers=headers or {})

    transport = httpx.MockTransport(handler)
    err = None
    if mode == "sync":
        with LexAPI("k", base_url=LOOPBACK, transport=transport, retry_config=retry) as c:
            try:
                call(c)
            except Exception as exc:  # noqa: BLE001
                err = exc
    else:

        async def go():
            async with AsyncLexAPI(
                "k", base_url=LOOPBACK, transport=transport, retry_config=retry
            ) as c:
                await call(c)

        try:
            asyncio.run(go())
        except Exception as exc:  # noqa: BLE001
            err = exc
    return err, calls["n"]


@pytest.mark.parametrize("mode", MODES)
def test_past_due_keys_on_the_resolved_code(mode):
    # A stray top-level code beside a typed error object is not the code.
    stray = {"code": "PAYMENT_PAST_DUE", "error": {"code": "RATE_LIMITED", "message": "x"}}
    err, n = _calls(mode, 429, stray, {"retry-after": "0"})
    assert isinstance(err, RateLimitedError) and n == 3
    typed = {"code": "RATE_LIMITED", "error": {"code": "PAYMENT_PAST_DUE", "message": "x"}}
    err, n = _calls(mode, 429, typed, {"retry-after": "0"})
    assert isinstance(err, PaymentPastDueError) and n == 1


@pytest.mark.parametrize(
    "status, body",
    [
        (
            200,
            {
                "success": False,
                "error": {"code": "RATE_LIMITED", "message": "x"},
                "usage": {"resetsAt": "2026-12-01T00:00:00Z"},
            },
        ),
        (
            200,
            {
                "success": False,
                "error": "Slow down",
                "code": "RATE_LIMITED",
                "usage": {"resetsAt": "2026-12-01T00:00:00Z"},
            },
        ),
        (
            503,
            {"error": "x", "code": "RATE_LIMITED", "usage": {"resetsAt": "2026-12-01T00:00:00Z"}},
        ),
    ],
)
def test_the_shape_test_never_applies_off_a_429(status, body):
    err = error_from_response(status, body)
    assert isinstance(err, RateLimitedError) and err.quota_exhausted is False


@pytest.mark.parametrize("mode", MODES)
def test_a_success_false_rate_limited_body_has_no_quota_flag(mode):
    body = {
        "success": False,
        "error": {"code": "RATE_LIMITED", "message": "x"},
        "usage": {"resetsAt": "2026-12-01T00:00:00Z"},
    }
    err, n = _calls(mode, 200, body)
    assert isinstance(err, RateLimitedError) and err.quota_exhausted is False and n == 1


def test_a_hand_built_rate_limit_error_without_a_429_has_no_quota_flag():
    usage = {"usage": {"resetsAt": "2026-12-01T00:00:00Z"}}
    assert RateLimitedError("x", body=usage).quota_exhausted is False
    assert RateLimitedError("x", status=429, body=usage).quota_exhausted is True
    assert RateLimitedError("x", code="QUOTA_EXHAUSTED").quota_exhausted is True


APP = {"x-lexapi-version": "2.29.0"}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", [520, 524])
@pytest.mark.parametrize("headers", [APP, {"ratelimit-limit": "60"}, {}])
def test_520_and_524_are_retried_only_on_free_gets_whatever_the_origin(mode, status, headers):
    err, n = _calls(mode, status, {"error": "x"}, headers)  # get_info is free
    assert n == 3
    billed = lambda c: c.get_document_version("X", 1)  # noqa: E731
    err, n = _calls(mode, status, {"error": "x"}, headers, call=billed)
    assert n == 1
    post = lambda c: c.resolve("32016R0679")  # noqa: E731
    optin = RetryConfig(
        max_retries=2, backoff_base=0.0001, backoff_cap=0.001, retry_read_only_posts=True
    )
    err, n = _calls(mode, status, {"error": "x"}, headers, call=post, retry=optin)
    assert n == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", [502, 503, 504])
def test_app_origin_502_503_504_are_still_retried_on_billed_gets(mode, status):
    err, n = _calls(
        mode, status, {"error": "x"}, APP, call=lambda c: c.get_document_version("X", 1)
    )
    assert n == 3


SECRET = "my-secret-\ud800"


def test_an_unencodable_secret_is_a_configuration_error_that_never_holds_it():
    for call in (
        lambda: compute_signature(BODY, SECRET),
        lambda: verify_signature(BODY, SIG, SECRET),
        lambda: verify_webhook(BODY, SIG, SECRET, tolerance=None),
        lambda: verify_webhook(BODY, None, SECRET, tolerance=None),
    ):
        with pytest.raises(WebhookSignatureError) as info:
            call()
        err = info.value
        assert err.reason == "missing_secret"
        for text in (str(err), repr(err), repr(err.args)):
            assert "my-secret" not in text
        assert err.__cause__ is None and err.__context__ is None
        assert not hasattr(err, "object")


@pytest.mark.parametrize("secret", [5, None, 1.5, ["s"], {"s": 1}])
def test_a_secret_of_the_wrong_type_raises_type_error(secret):
    for call in (
        lambda: compute_signature(BODY, secret),
        lambda: verify_signature(BODY, SIG, secret),
        lambda: verify_webhook(BODY, SIG, secret, tolerance=None),
    ):
        with pytest.raises(TypeError):
            call()


@pytest.mark.parametrize("secret", [b"s", bytearray(b"s"), memoryview(b"s"), "s"])
def test_secrets_as_bytes_like_or_str_still_work(secret):
    assert verify_signature(BODY, SIG, secret) is True


@pytest.mark.parametrize("mode", MODES)
def test_envelope_objects_are_read_alone(mode):
    body = (
        b'{"_meta": {"total": 1, "truncated": true}, "truncated": false, "x": 1}\n'
        b'{"celex": "A"}\n'
        b'{"_done": {"written": 1, "truncated": false}, "truncated": true, "complete": false}\n'
    )
    rows, invalid, meta, done, error, truncated = export(mode, body)
    assert meta == {"total": 1, "truncated": True}
    assert done == {"written": 1, "truncated": False} and error is None
    assert truncated is False


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "value, expected",
    [("1", True), ("0", False), ("true", None), ("2", None), (" 1 ", True), ("", None)],
)
def test_only_1_and_0_count_in_the_truncated_header(mode, value, expected):
    def handler(request):
        return httpx.Response(
            200,
            content=b'{"_meta": {"truncated": true}}\n',
            headers={"content-type": "application/x-ndjson", "x-export-truncated": value},
        )

    transport = httpx.MockTransport(handler)
    with LexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
        with c.export(limit=1) as s:
            s.read_meta()
            # an absent header falls back to _meta.truncated (true here)
            assert s.truncated is (True if expected is None else expected)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("key", [b'"_done": {}', b'"_error": {"code": "X"}'])
def test_a_later_meta_line_is_skipped_also_with_done_or_error(mode, key):
    body = (
        b'{"_meta": {}}\n{"celex": "A"}\n{"_meta": {}, '
        + key
        + b'}\n{"celex": "B"}\n'
        + b'{"_done": {"written": 2}}\n'
    )
    rows, invalid, meta, done, error, _ = export(mode, body)
    assert rows == [{"celex": "A"}, {"celex": "B"}] and invalid == 0
    assert done == {"written": 2} and error is None


@pytest.mark.parametrize("year, ok", [("0000", False), ("0001", True), ("9999", True)])
def test_timestamp_years(year, ok):
    import json

    from lexapi.webhooks import _signed_time

    stamp = f"{year}-06-15T00:00:00Z"
    assert (_signed_time(stamp) is not None) is ok
    body = json.dumps({"timestamp": stamp}).encode()
    if not ok:
        assert _reason(body, compute_signature(body, "s"), tolerance=1e15) == "stale_timestamp"


@pytest.mark.parametrize(
    "stamp, delta_us, ok",
    [
        ("2026-11-19T10:05:00.000000Z", 0, True),  # exactly the window: included
        ("2026-11-19T10:05:00.000001Z", 1, False),
        ("2026-11-19T10:04:59.999999Z", -1, True),
        ("2026-11-19T09:55:00Z", 0, True),
        ("2026-11-19T09:54:59.999999Z", 1, False),
        ("2026-11-19T10:05:00.1Z", 0, False),
        ("2026-11-19T11:05:00+01:00", 0, True),
    ],
)
def test_the_replay_window_edge_is_included_without_float_drift(stamp, delta_us, ok):
    import json

    body = json.dumps({"timestamp": stamp}).encode()
    sig = compute_signature(body, "s")
    if ok:
        verify_webhook(body, sig, "s", tolerance=300, now=NOW)
    else:
        assert _reason(body, sig, tolerance=300) == "stale_timestamp"


def test_the_edge_holds_without_float_drift():
    import json

    body = json.dumps({"timestamp": "2026-11-19T10:00:00.001Z"}).encode()
    sig = compute_signature(body, "s")
    # In floats, (NOW + 0.001 + 0.002) - (NOW + 0.001) > 0.002: the edge would fail.
    verify_webhook(body, sig, "s", tolerance=0.002, now=NOW + 0.001 + 0.002)
    assert _reason(body, sig, tolerance=0.002, now=NOW + 0.001 + 0.0021) == "stale_timestamp"


@pytest.mark.parametrize("millis, tol_ms", [(2, 2), (3, 5), (5, 2)])
def test_the_edge_holds_in_integer_microseconds(millis, tol_ms):
    import json

    stamp = f"2026-11-19T10:00:00.{millis:03d}Z"
    body = json.dumps({"timestamp": stamp}).encode()
    sig = compute_signature(body, "s")
    now = NOW + millis / 1000 + tol_ms / 1000  # float sums drift by a few ULPs
    verify_webhook(body, sig, "s", tolerance=tol_ms / 1000, now=now)


@pytest.mark.parametrize(
    "stamp, ok",
    [
        # now = 10:05:00 with a 300 s window: the edges are 10:00:00 and 10:10:00
        ("2026-11-19T10:00:00Z", True),  # exactly the window, in the past
        ("2026-11-19T09:59:59.999999Z", False),  # 1 µs past the edge
        ("2026-11-19T09:59:59.9999999Z", False),  # digits after the sixth ignored
        ("2026-11-19T10:10:00Z", True),  # exactly the window, in the future
        ("2026-11-19T10:10:00.000001Z", False),  # 1 µs past the edge
        ("2026-11-19T10:10:00.0000009Z", True),  # truncated, not rounded
        ("2026-11-19T10:10:00.000000999Z", True),
    ],
)
def test_the_timestamp_is_read_to_the_microsecond(stamp, ok):
    import json

    body = json.dumps({"timestamp": stamp}).encode()
    sig = compute_signature(body, "s")
    now = NOW + 300  # 10:05:00
    if ok:
        verify_webhook(body, sig, "s", tolerance=300, now=now)
    else:
        assert _reason(body, sig, tolerance=300, now=now) == "stale_timestamp"


@pytest.mark.parametrize(
    "now_offset, tolerance, ok",
    [
        (0.000001, 0.0000005, True),  # now 1 µs after; the window rounds up to 1 µs
        (0.000001, 0.0000004, False),  # the window rounds down to 0 µs
        (0.000001, 0.000001, True),
        (0.000002, 0.000001, False),
        (-0.000001, 0.000001, True),  # the other side of now
        (-0.000002, 0.000001, False),
    ],
)
def test_now_and_the_window_round_to_the_nearest_microsecond(now_offset, tolerance, ok):
    # BODY is signed at exactly NOW (10:00:00Z).
    if ok:
        verify_webhook(BODY, SIG, "s", tolerance=tolerance, now=NOW + now_offset)
    else:
        assert _reason(BODY, SIG, tolerance=tolerance, now=NOW + now_offset) == "stale_timestamp"
