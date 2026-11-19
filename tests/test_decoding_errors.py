"""Webhook and export bodies that are not strict UTF-8 JSON raise the
documented errors, never a raw decoding error."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    IncompleteExportError,
    LexAPI,
    WebhookSignatureError,
    compute_signature,
    verify_signature,
    verify_webhook,
)

LOOPBACK = "http://127.0.0.1:9/api/v1"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


# ── webhook bodies ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        b'{"event": "webhook.test", "n": NaN}',
        b'{"event": "webhook.test", "n": Infinity}',
        b'{"event": "webhook.test", "n": -Infinity}',
        b"NaN",
    ],
)
def test_nan_and_infinity_are_not_json(body):
    with pytest.raises(WebhookSignatureError, match="not JSON"):
        verify_webhook(body, compute_signature(body, "s"), "s", tolerance=None)


def test_large_numbers_are_still_json():
    body = b'{"event": "webhook.test", "n": 1e999, "m": 123456789012345678901234567890}'
    event = verify_webhook(body, compute_signature(body, "s"), "s", tolerance=None)
    assert event["m"] == 123456789012345678901234567890


@pytest.mark.parametrize("payload", ['{"event": "a\ud800b"}', "\udfff"])
def test_a_str_payload_with_a_lone_surrogate_fails_verification(payload):
    signature = "0" * 64
    assert verify_signature(payload, signature, "s") is False
    with pytest.raises(WebhookSignatureError):
        verify_webhook(payload, signature, "s", tolerance=None)


def test_a_str_payload_that_encodes_is_still_verified():
    payload = '{"event": "webhook.test", "note": "é"}'
    signature = compute_signature(payload.encode("utf-8"), "s")
    assert verify_signature(payload, signature, "s") is True
    assert verify_webhook(payload, signature, "s", tolerance=None)["note"] == "é"


# ── export lines and the declared charset ────────────────────────────────

ROWS = (
    b'{"_meta": {"total": 1}}\n'
    b'{"celex": "32016R0679", "t": "\xc3\xa9"}\n'
    b'{"_done": {"written": 1}}\n'
)


def export(mode, content, content_type):
    def handler(request):
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, content=content, headers={"content-type": content_type})

    transport = httpx.MockTransport(handler)
    if mode == "sync":
        with LexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
            with c.export(limit=1) as stream:
                return list(stream), stream.invalid_lines

    async def go():
        async with AsyncLexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
            stream = await c.export(limit=1)
            async with stream:
                return [r async for r in stream], stream.invalid_lines

    return asyncio.run(asyncio.wait_for(go(), 10))


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "charset", ["utf-16", "utf-16-le", "utf-32", "latin-1", "x-unknown-charset", "ascii"]
)
def test_utf8_lines_are_read_as_utf8_whatever_the_charset(mode, charset):
    rows, invalid = export(mode, ROWS, f"application/x-ndjson; charset={charset}")
    assert rows == [{"celex": "32016R0679", "t": "é"}] and invalid == 0


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_an_export_in_another_encoding_is_incomplete_not_a_raw_error(mode, encoding):
    content = ROWS.decode("utf-8").encode(encoding)
    with pytest.raises(IncompleteExportError):
        export(mode, content, f"application/x-ndjson; charset={encoding}")


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("bad", [b"\xff", b"\xed\xa0\x80", b"\xc0\xaf", b"\xe2\x82"])
def test_a_line_that_is_not_utf8_is_an_invalid_line(mode, bad):
    content = b'{"_meta": {}}\n{"celex": "A", "t": "' + bad + b'"}\n{"celex": "B"}\n{"_done": {}}\n'
    rows, invalid = export(mode, content, "application/x-ndjson")
    assert rows == [{"celex": "B"}] and invalid == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_lines_split_across_chunks_and_crlf_endings(mode):
    content = b'\xef\xbb\xbf{"_meta": {}}\r\n{"celex": "A"}\r\n\r\n{"_done": {}}'
    rows, invalid = export(mode, content, "application/x-ndjson")
    assert rows == [{"celex": "A"}] and invalid == 0


def test_unencodable_payloads_leak_nothing_and_encodable_ones_compare_in_constant_time(
    monkeypatch,
):
    from lexapi import webhooks

    calls = []
    real = webhooks.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(webhooks.hmac, "compare_digest", spy)

    secret_text = "SECRET-PAYLOAD-TEXT"
    payload = '{"note": "' + secret_text + '\ud800"}'
    with pytest.raises(WebhookSignatureError) as info:
        verify_webhook(payload, "0" * 64, "s", tolerance=None)
    err = info.value
    for text in (str(err), repr(err), " ".join(map(str, err.args))):
        assert secret_text not in text and "\ud800" not in text
    assert err.__cause__ is None and err.__context__ is None
    assert calls == []  # nothing to compare: the payload has no UTF-8 form

    good = '{"note": "' + secret_text + '"}'
    signature = compute_signature(good.encode("utf-8"), "s")
    assert verify_signature(good, signature, "s") is True
    assert verify_signature(good, "f" * 64, "s") is False
    assert len(calls) == 2  # both encodable checks went through compare_digest
