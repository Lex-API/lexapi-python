"""Required fields, strict JSON parsing, excerpts and media types
(SDK contract §1.3, checks 1-6).

Every body below is synthetic. No test opens a socket: clients use a mock
transport and a loopback base URL, and the real httpx transports are
patched to fail.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
from typing import Any, Callable

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    InternalServerError,
    InvalidParamsError,
    InvalidResponseError,
    LexAPI,
    LexAPIError,
    NotFoundError,
    UpstreamError,
    models,
    passages,
)
from lexapi._response import APIResponse

LOOPBACK = "http://127.0.0.1:9/api/v1"
MODES = ["sync", "async"]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


Responder = Callable[[httpx.Request], httpx.Response]


def make_client(respond: Responder, cls: Any = LexAPI, **kwargs: Any) -> Any:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        calls["n"] += 1
        return respond(request)

    kwargs.setdefault("max_retries", 0)
    client = cls(
        "lex_offline_test_key",
        base_url=LOOPBACK,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )
    assert str(client._http.base_url).startswith("http://127.0.0.1:")
    client._test_calls = calls
    return client


def raw(status: int, content: bytes | str, content_type: Any = None, **headers: str) -> Responder:
    data = content.encode() if isinstance(content, str) else content
    hdrs: list[tuple[bytes, bytes]] = [(k.encode(), v.encode()) for k, v in headers.items()]
    if content_type is not None:
        value = content_type if isinstance(content_type, bytes) else content_type.encode("latin-1")
        hdrs.append((b"content-type", value))
    return lambda request: httpx.Response(status, content=data, headers=hdrs)


def as_json(body: Any, status: int = 200) -> Responder:
    return raw(status, json.dumps(body), "application/json")


def run(call: Callable[[Any], Any], respond: Responder, mode: str = "sync", **kw: Any) -> Any:
    if mode == "sync":
        with make_client(respond, **kw) as client:
            return call(client)

    async def go() -> Any:
        async with make_client(respond, cls=AsyncLexAPI, **kw) as client:
            return await call(client)

    return asyncio.run(go())


def invalid(call: Callable[[Any], Any], respond: Responder, mode: str = "sync") -> Any:
    with pytest.raises(InvalidResponseError) as info:
        run(call, respond, mode)
    return info.value


def without(body: dict[str, Any], field: str) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != field}


def search(c: Any) -> Any:
    return c.search("x")


# ── required fields ─────────────────────────────────────────────────────

AS_OF_OK = {
    "success": True,
    "document": "32099R0001",
    "asOfResolved": True,
    "act": {},
    "provenance": {},
}


def as_of(c: Any) -> Any:
    return c.legislation_as_of("32099R0001", "2099-01-01")


@pytest.mark.parametrize("mode", MODES)
def test_as_of_document_is_a_non_empty_string_and_as_of_resolved_a_boolean(mode):
    assert run(as_of, as_json(AS_OF_OK), mode).as_of_resolved is True
    assert run(as_of, as_json(dict(AS_OF_OK, asOfResolved=False)), mode).as_of_resolved is False
    for field, value in [
        ("document", ...),
        ("document", None),
        ("document", ""),
        ("asOfResolved", ...),
        ("asOfResolved", None),
    ]:
        body = without(AS_OF_OK, field) if value is ... else dict(AS_OF_OK, **{field: value})
        err = invalid(as_of, as_json(body), mode)
        assert err.message == f'Expected "{field}" in the response; it is missing'
        assert err.body == body
    for field, value, message in [
        ("document", {}, 'Expected "document" to be a string, got object'),
        ("document", 3, 'Expected "document" to be a string, got number'),
        ("document", ["X"], 'Expected "document" to be a string, got array'),
        ("document", True, 'Expected "document" to be a string, got boolean'),
        ("asOfResolved", "true", 'Expected "asOfResolved" to be a boolean, got string'),
        ("asOfResolved", 1, 'Expected "asOfResolved" to be a boolean, got number'),
        ("asOfResolved", 0, 'Expected "asOfResolved" to be a boolean, got number'),
        ("asOfResolved", {}, 'Expected "asOfResolved" to be a boolean, got object'),
    ]:
        err = invalid(as_of, as_json(dict(AS_OF_OK, **{field: value})), mode)
        assert err.message == message


OBJECT_FAMILIES = [
    ("get_document", lambda c: c.get_document("32099R0001"), {"success": True, "document": {}}),
    (
        "get_document_by_url",
        lambda c: c.get_document_by_url("https://eur-lex.europa.eu/eli/reg/2099/1/oj"),
        {"success": True, "document": {}},
    ),
    (
        "get_document_version",
        lambda c: c.get_document_version("32099R0001", 1),
        {"success": True, "document": {}},
    ),
    (
        "get_document_at_date",
        lambda c: c.get_document_at_date("32099R0001", "2099-01-01"),
        {"success": True, "document": {}},
    ),
    ("national_get_document", lambda c: c.national.get_document("at", "AT-1"), {"document": {}}),
    ("as_of_act", as_of, AS_OF_OK),
    ("as_of_provenance", as_of, AS_OF_OK),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name, call, body", OBJECT_FAMILIES, ids=[f[0] for f in OBJECT_FAMILIES])
def test_required_object_missing_null_or_wrong_kind(name, call, body, mode):
    field = name.rsplit("_", 1)[1] if name.startswith("as_of") else "document"
    run(call, as_json(body), mode)
    for sent in (without(body, field), dict(body, **{field: None})):
        err = invalid(call, as_json(sent), mode)
        assert err.message == f'Expected "{field}" in the response; it is missing'
    for value, kind in [("x", "string"), ("", "string"), ([], "array"), ([{}], "array")]:
        err = invalid(call, as_json(dict(body, **{field: value})), mode)
        assert err.message == f'Expected "{field}" to be an object, got {kind}'
    for value, kind in [(0, "number"), (1.5, "number"), (False, "boolean"), (True, "boolean")]:
        err = invalid(call, as_json(dict(body, **{field: value})), mode)
        assert err.message == f'Expected "{field}" to be an object, got {kind}'


CONTENT_READS = [
    ("get_document", lambda c: c.get_document("32099R0001"), {"success": True}),
    (
        "get_document_by_url",
        lambda c: c.get_document_by_url("https://eur-lex.europa.eu/eli/reg/2099/1/oj"),
        {"success": True},
    ),
    ("national_get_document", lambda c: c.national.get_document("at", "AT-1"), {}),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name, call, base", CONTENT_READS, ids=[f[0] for f in CONTENT_READS])
def test_document_content_and_articles(name, call, base, mode):
    for document in [
        {},
        {"content": None},
        {"content": {}},
        {"content": {"articles": None}},
        {"content": {"articles": []}},
        {"content": {"articles": [{"id": "art_1"}]}},
    ]:
        run(call, as_json(dict(base, document=document)), mode)
    for document, message in [
        ({"content": "text"}, 'Expected "document.content" to be an object, got string'),
        ({"content": [{}]}, 'Expected "document.content" to be an object, got array'),
        ({"content": 1}, 'Expected "document.content" to be an object, got number'),
        ({"content": True}, 'Expected "document.content" to be an object, got boolean'),
        (
            {"content": {"articles": {}}},
            'Expected "document.content.articles" to be a list, got object',
        ),
        (
            {"content": {"articles": "x"}},
            'Expected "document.content.articles" to be a list, got string',
        ),
        (
            {"content": {"articles": [{}, "x"]}},
            'Expected every item of "document.content.articles" to be an object, '
            "got string at index 1",
        ),
        (
            {"content": {"articles": [{}, None]}},
            'Expected every item of "document.content.articles" to be an object, '
            "got null at index 1",
        ),
    ]:
        err = invalid(call, as_json(dict(base, document=document)), mode)
        assert err.message == message


def test_check_order():
    def message(call: Any, body: Any) -> str:
        return str(invalid(call, as_json(body)).message)

    assert message(as_of, {"act": "x"}) == 'Expected "document" in the response; it is missing'
    assert (
        message(as_of, {"document": 1, "asOfResolved": 1})
        == 'Expected "document" to be a string, got number'
    )
    assert (
        message(as_of, {"document": "X", "asOfResolved": 1, "act": 1})
        == 'Expected "asOfResolved" to be a boolean, got number'
    )
    assert (
        message(as_of, dict(AS_OF_OK, act="x", provenance=1))
        == 'Expected "act" to be an object, got string'
    )
    assert (
        message(as_of, dict(AS_OF_OK, provenance=[]))
        == 'Expected "provenance" to be an object, got array'
    )
    assert (
        message(as_of, dict(AS_OF_OK, document="", act=None))
        == 'Expected "document" in the response; it is missing'
    )
    assert (
        message(search, {"document": "x", "results": None})
        == 'Expected "results" in the response; it is missing'
    )

    def doc(c: Any) -> Any:
        return c.get_document("X")

    assert message(doc, {"document": "x"}) == 'Expected "document" to be an object, got string'
    assert (
        message(doc, {"document": {"content": 1}})
        == 'Expected "document.content" to be an object, got number'
    )
    # optional object before its list
    assert (
        message(doc, {"document": {"content": [{"articles": 1}]}})
        == 'Expected "document.content" to be an object, got array'
    )


# ── legitimate answers are accepted, also through lexapi.passages ───────

DOC_CONTENT = {
    "articles": [{"id": "art_1", "number": "Article 1", "content": "Synthetic text."}],
}
LEGIT_DOCUMENTS = [
    {"success": True, "document": {"celex": "32099R0001", "content": None}},
    {"success": True, "document": {"celex": "32099R0001"}},
    {"success": True, "document": {"celex": "32099R0001", "content": {"articles": []}}},
    {"success": True, "document": {"celex": "32099R0001", "content": DOC_CONTENT}},
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("body", LEGIT_DOCUMENTS)
def test_legitimate_document_answers_parse_and_feed_passages(body, mode):
    response = run(lambda c: c.get_document("32099R0001"), as_json(body), mode)
    result = passages.from_document(response)
    assert all(p.celex == "32099R0001" for p in result.passages)


@pytest.mark.parametrize("mode", MODES)
def test_legitimate_national_version_at_date_and_unresolved_as_of(mode):
    national = {"document": {"id": "AT-1", "content": {"articles": []}}, "sources": []}
    assert run(lambda c: c.national.get_document("at", "AT-1"), as_json(national), mode)
    version = {"success": True, "document": {"celex": "32099R0001", "version": 1}}
    run(lambda c: c.get_document_version("32099R0001", 1), as_json(version), mode)
    run(lambda c: c.get_document_at_date("32099R0001", "2099-01-01"), as_json(version), mode)
    unresolved = {
        "success": True,
        "document": "32099R0001",
        "asOf": "2099-01-01",
        "asOfResolved": False,
        "asOfReason": "no_version_listed",
        "act": {"celex": "32099R0001"},
        "provenance": {},
    }
    response = run(as_of, as_json(unresolved), mode)
    assert response.as_of_resolved is False
    result = passages.from_legislation_as_of(response)
    assert all(p.text is None for p in result.passages)


# ── parsing: strict UTF-8, RFC 8259 JSON ────────────────────────────────

NOT_JSON = [
    (b'{"results": [], "x": NaN}', 25),
    (b'{"results": [], "x": Infinity}', 30),
    (b'{"results": [], "x": -Infinity}', 31),
    (b'{"results": ["\xed\xa0\x80"]}', 20),  # a UTF-8-encoded surrogate
    (b'{"x": "\xff\xfe"}', 11),
    (b"\xff\xfe" + "".join(ch + "\x00" for ch in '{"results":[]}').encode("latin-1"), 30),
    ("﻿".encode("utf-16-be") + '{"results":[]}'.encode("utf-16-be"), 30),
    ('{"results":[]}'.encode("utf-32"), 60),
    (b" \n\t", 3),
    (b'{"results": []} trailing', 24),
    ("é".encode(), 2),
    (b"\xef\xbb\xbf\xef\xbb\xbf{}", 8),  # only one byte order mark is ignored
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("content, size", NOT_JSON)
def test_rejects_what_is_not_strict_utf8_json(content, size, mode):
    assert len(content) == size
    err = invalid(search, raw(200, content, "application/json; charset=utf-8"), mode)
    assert err.message == (
        f"Expected JSON, got application/json that does not parse as JSON ({size} bytes)"
    )
    assert err.body is None
    assert err.body_excerpt is not None
    assert err.__context__ is None and err.__cause__ is None


DEEP = b"[" * 100_000 + b"]" * 100_000


def _refuse_nesting(monkeypatch):
    """Make the parser refuse nesting, as CPython does below 3.14."""
    import lexapi._base as base

    def loads(*args: Any, **kwargs: Any) -> Any:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(base.json, "loads", loads)


@pytest.mark.parametrize("mode", MODES)
def test_nesting_the_parser_refuses_is_not_json(monkeypatch, mode):
    _refuse_nesting(monkeypatch)
    err = invalid(search, raw(200, DEEP, "application/json"), mode)
    assert err.message == (
        "Expected JSON, got application/json that does not parse as JSON (200000 bytes)"
    )
    assert err.body is None and err.body_excerpt == "[" * 200
    assert err.__context__ is None and err.__cause__ is None
    with pytest.raises(UpstreamError) as info:
        run(lambda c: c.get_info(), raw(502, DEEP, "application/json"), mode)
    assert info.value.body is None
    assert info.value.message == "HTTP 502"
    assert info.value.body_excerpt == "[" * 200


@pytest.mark.parametrize("mode", MODES)
def test_deep_nesting_never_escapes_as_a_raw_error(mode):
    # Whether this runtime's parser takes this depth or not, the caller gets
    # InvalidResponseError: "not JSON" if refused, "not an object" if read.
    err = invalid(search, raw(200, DEEP, "application/json"), mode)
    assert err.message in (
        "Expected JSON, got application/json that does not parse as JSON (200000 bytes)",
        "Expected a JSON object, got array",
    )


@pytest.mark.parametrize("mode", MODES)
def test_accepts_one_leading_byte_order_mark(mode):
    result = run(search, raw(200, b'\xef\xbb\xbf{"results": []}', "application/json"), mode)
    assert result.results == []


def test_large_numbers_are_read_as_python_reads_them():
    result = run(search, raw(200, b'{"results": [], "n": 1e400, "m": 12345678901234567890}'))
    assert result["n"] == float("inf") and result["m"] == 12345678901234567890


@pytest.mark.parametrize(
    "header, media",
    [
        ("Text/HTML ; charset=UTF-8", "Text/HTML"),
        (" text/plain", "text/plain"),
        ("; charset=utf-8", "no content type"),
        (None, "no content type"),
        ("", "no content type"),
        (" \t ", "no content type"),
        ("x/" + "a" * 150, "x/" + "a" * 98),
        ("text/plain \t ;charset=utf-8", "text/plain"),
        (" text/plain ;x", " text/plain "),
        ("\x0btext/plain\x0b", "\x0btext/plain\x0b"),
        (b"text/\xe9t\xe9", "text/été"),  # non-ASCII bytes read as latin-1
        (b"x/" + "é".encode() + b"a" * 120, "x/Ã©" + "a" * 96),
    ],
)
def test_media_type(header, media):
    err = invalid(search, raw(200, "<p>", header))
    assert err.message == f"Expected JSON, got {media} that does not parse as JSON (3 bytes)"


# ── excerpts ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
def test_excerpt_is_200_code_points_decoded_as_utf8_with_replacement(mode):
    content = "<html>" + "x" * 194 + "SENTINEL" + "y" * 500
    assert invalid(search, raw(200, content, "text/html"), mode).body_excerpt == content[:200]
    astral = "\U0001f600" * 300
    assert invalid(search, raw(200, astral, "text/plain"), mode).body_excerpt == "\U0001f600" * 200
    latin1 = bytes([0xE9, 0x74, 0xE9])
    err = invalid(search, raw(200, latin1, "text/plain; charset=iso-8859-1"), mode)
    assert err.body_excerpt == "�t�"
    bom = b"\xef\xbb\xbf<p>"
    assert invalid(search, raw(200, bom, "text/html"), mode).body_excerpt == "﻿<p>"


@pytest.mark.parametrize("mode", MODES)
def test_non_2xx_excerpt_only_when_not_json_and_not_empty(mode):
    def info(c: Any) -> Any:
        return c.get_info()

    def fail(respond: Responder) -> LexAPIError:
        with pytest.raises(LexAPIError) as caught:
            run(info, respond, mode)
        return caught.value

    err = fail(raw(502, "  ", "text/plain"))
    assert (err.body_excerpt, err.body) == ("  ", None)
    err = fail(raw(502, ""))
    assert (err.body_excerpt, err.body, err.message) == (None, None, "HTTP 502")
    err = fail(raw(502, "null", "application/json"))
    assert (err.body_excerpt, err.body) == (None, None)
    err = fail(raw(502, b"{}\xff", "application/json"))
    assert (err.body_excerpt, err.body) == ("{}�", None)
    err = fail(raw(404, b'{"success": false}', "application/json"))
    assert (err.body_excerpt, err.body) == (None, {"success": False})


def test_excerpt_decodes_the_first_800_bytes_like_a_full_decode():
    rng = random.Random(0x5EED)
    pieces = [
        b"A",
        b"\xc3\xa9",
        b"\xe2\x82\xac",
        b"\xf0\x9f\x98\x80",
        b"\xff",
        b"\xc3",
        b"\xe2\x82",
        b"\xf0\x9f",
        b"\xef\xbb\xbf",
        b"\x80",
        b"\xed\xa0\x80",
        b"<",
    ]
    for _ in range(300):
        target = 700 + rng.randrange(300)
        content = b""
        while len(content) < target:
            content += rng.choice(pieces)
        full = content.decode("utf-8", "replace")[:200]
        assert invalid(search, raw(200, content, "text/plain")).body_excerpt == full


# ── envelopes on a 2xx, legacy messages, public surface ─────────────────


@pytest.mark.parametrize("mode", MODES)
def test_mapped_error_from_a_2xx_keeps_status_and_is_never_retried(mode):
    respond = raw(
        200,
        json.dumps({"success": False, "error": {"details": {"retryAfter": 1}}, "retryAfter": 1}),
        "application/json",
        **{"Retry-After": "1", "RateLimit-Limit": "1"},
    )
    calls: dict[str, int] = {}

    def call(c: Any) -> Any:
        calls["c"] = c
        return c.get_document("X")

    with pytest.raises(InternalServerError) as info:
        run(call, respond, mode, max_retries=3)
    err = info.value
    assert (err.status, err.code, err.message, err.retry_after) == (
        200,
        "INTERNAL",
        "HTTP 200",
        None,
    )
    assert str(err) == "[INTERNAL] HTTP 200 (HTTP 200)"
    assert calls["c"]._test_calls["n"] == 1
    with pytest.raises(NotFoundError) as found:
        run(call, as_json({"success": False, "error": {"code": "NOT_FOUND"}}), mode)
    assert (found.value.status, found.value.message) == (200, "HTTP 200")


def test_legacy_error_with_an_empty_message():
    with pytest.raises(InvalidParamsError) as info:
        run(lambda c: c.get_info(), as_json({"error": "Bad", "message": ""}, status=400))
    assert str(info.value) == "[INVALID_PARAMS] Bad (HTTP 400)"
    assert info.value.message == "Bad"


def test_array_error_member_is_not_the_typed_envelope():
    body = {"success": False, "error": [{"code": "NOT_FOUND", "message": "x"}]}
    err = invalid(search, as_json(body))
    assert err.message == "Response reported success: false without an error"
    with pytest.raises(LexAPIError) as info:
        run(lambda c: c.get_info(), as_json(body, status=404))
    assert (info.value.code, info.value.message) == ("NOT_FOUND", "HTTP 404")


SHAPE_NAMES = {
    "list_fields",
    "required_list_fields",
    "required_fields",
    "optional_object_fields",
    "success_false_ok",
    "allow_empty",
}


def test_response_models_expose_no_public_shape_attribute():
    classes = [
        obj
        for _, obj in inspect.getmembers(models, inspect.isclass)
        if issubclass(obj, APIResponse)
    ]
    assert len(classes) > 30
    for cls in classes:
        assert not SHAPE_NAMES & set(dir(cls)), cls.__name__


def test_only_the_webhook_delete_takes_no_body():
    for respond in (raw(204, b"x"), raw(200, b"")):
        with make_client(respond) as client:
            assert client.webhooks.delete("w1") is None
            for call in (lambda: client.get_info(), lambda: client.webhooks.list()):
                with pytest.raises(InvalidResponseError) as info:
                    call()
                assert info.value.message == "Expected JSON, got no body"
    with make_client(raw(200, b" ", "text/plain")) as client:
        with pytest.raises(InvalidResponseError) as info:
            client.webhooks.delete("w1")
    assert (
        info.value.message == "Expected JSON, got text/plain that does not parse as JSON (1 bytes)"
    )


class _AllGroups(APIResponse):
    """A synthetic shape with every group, to pin the check order."""

    _required_list_fields = ("items",)
    _required_fields = (("name", "string"), ("flag", "boolean"))
    _optional_object_fields = ("meta",)
    _list_fields = ("items", "extra")


def test_shape_check_order_across_groups():
    from lexapi._base import shape_problem

    def problem(**body: Any) -> Any:
        return shape_problem(body, _AllGroups)

    ok = {"items": [], "name": "n", "flag": True}
    assert problem(**ok) is None
    assert problem(name=1, flag=1) == 'Expected "items" in the response; it is missing'
    assert problem(items=[], name=1, flag=1) == 'Expected "name" to be a string, got number'
    assert problem(items=[], name="n", flag=1) == 'Expected "flag" to be a boolean, got number'
    bad_meta_and_list = dict(ok, flag=1, meta="x", extra="x")
    assert problem(**bad_meta_and_list) == 'Expected "flag" to be a boolean, got number'
    assert problem(**dict(ok, meta="x", items="x")) == 'Expected "meta" to be an object, got string'
    assert problem(**dict(ok, items="x", extra="y")) == 'Expected "items" to be a list, got string'
    assert (
        problem(**dict(ok, items=[{}, 1], extra=[2]))
        == 'Expected every item of "items" to be an object, got number at index 1'
    )
    assert problem(**dict(ok, meta=None, extra=None)) is None


def test_repeated_content_type_headers_are_joined():
    respond = lambda request: httpx.Response(  # noqa: E731
        200,
        content=b"<p>",
        headers=[(b"content-type", b" text/html"), (b"content-type", b"text/plain; x")],
    )
    err = invalid(search, respond)
    assert err.message == (
        "Expected JSON, got text/html, text/plain that does not parse as JSON (3 bytes)"
    )
