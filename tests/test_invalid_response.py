"""Malformed 2xx answers raise InvalidResponseError, and no error message
quotes a response body.

Every body below is synthetic. No test opens a socket: clients use a mock
transport and a loopback base URL, and the real httpx transports are
patched to fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pickle
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
    RateLimitedError,
    UpstreamError,
    passages,
)

LOOPBACK = "http://127.0.0.1:9/api/v1"
SENTINEL = "SENTINEL-7f3a-unestablished-provision-text"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


Responder = Callable[[httpx.Request], httpx.Response]


def _handler(respond: Responder) -> Responder:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return respond(request)

    return handler


def make_client(respond: Responder, cls: Any = LexAPI, base_url: str = LOOPBACK) -> Any:
    if not base_url.startswith("http://127.0.0.1:"):
        raise RuntimeError(f"Offline tests refuse the base URL {base_url!r}")
    client = cls(
        "lex_offline_test_key",
        base_url=base_url,
        transport=httpx.MockTransport(_handler(respond)),
        max_retries=0,
    )
    assert str(client._http.base_url).startswith("http://127.0.0.1:")
    return client


def test_harness_refuses_a_non_loopback_base_url():
    with pytest.raises(RuntimeError, match="refuse the base URL"):
        make_client(lambda r: httpx.Response(200), base_url="https://lex-api.com/api/v1")


def raw(status: int, content: bytes | str, content_type: str | None) -> Responder:
    data = content.encode() if isinstance(content, str) else content
    headers = {"content-type": content_type} if content_type else {}
    return lambda request: httpx.Response(status, content=data, headers=headers)


def as_json(body: Any, status: int = 200) -> Responder:
    return raw(status, json.dumps(body), "application/json")


# ── endpoint families ───────────────────────────────────────────────────

# name -> (call, minimal valid body, primary list field, list field required)
FAMILIES: dict[str, tuple[Callable[[Any], Any], dict[str, Any], str | None, bool]] = {
    "semantic_legislation": (
        lambda c: c.semantic_legislation_search("own funds"),
        {"success": True, "results": []},
        "results",
        True,
    ),
    "semantic_case_law": (
        lambda c: c.semantic_search("own funds"),
        {"success": True, "results": []},
        "results",
        True,
    ),
    "keyword_search": (
        lambda c: c.search("own funds"),
        {"success": True, "results": []},
        "results",
        True,
    ),
    "document": (
        lambda c: c.get_document("32099R0001"),
        {"success": True, "document": {"celex": "32099R0001"}},
        None,
        False,
    ),
    "batch": (
        lambda c: c.get_documents_batch(["32099R0001"]),
        {"success": True, "documents": []},
        "documents",
        False,
    ),
    "versions": (
        lambda c: c.list_document_versions("32099R0001"),
        {"success": True, "versions": []},
        "versions",
        False,
    ),
    "citations": (
        lambda c: c.citations.cites("32099R0001"),
        {"success": True, "cites": []},
        "cites",
        False,
    ),
    "as_of": (
        lambda c: c.legislation_as_of("32099R0001", "2099-01-01"),
        {
            "success": True,
            "document": "32099R0001",
            "asOfResolved": False,
            "asOfReason": "no_version_listed",
            "act": {"celex": "32099R0001"},
            "provenance": {},
        },
        None,
        False,
    ),
    "national_list": (
        lambda c: c.national.list_documents("at"),
        {"results": []},
        "results",
        False,
    ),
    "national_semantic": (
        lambda c: c.national.legislation_semantic_search("at", "own funds"),
        {"success": True, "results": []},
        "results",
        True,
    ),
}
LISTED = [name for name, family in FAMILIES.items() if family[2] is not None]
REQUIRED = [name for name, family in FAMILIES.items() if family[3]]
OPTIONAL = [name for name, family in FAMILIES.items() if family[2] is not None and not family[3]]
MODES = ["sync", "async"]


def run(family: str, mode: str, respond: Responder) -> Any:
    call = FAMILIES[family][0]
    if mode == "sync":
        with make_client(respond) as client:
            return call(client)

    async def go() -> Any:
        async with make_client(respond, cls=AsyncLexAPI) as client:
            return await call(client)

    return asyncio.run(go())


def raises_invalid(family: str, mode: str, respond: Responder) -> InvalidResponseError:
    with pytest.raises(InvalidResponseError) as info:
        run(family, mode, respond)
    assert info.value.status == 200
    assert info.value.code == "INVALID_RESPONSE"
    return info.value


def assert_no_sentinel(err: BaseException) -> None:
    assert SENTINEL not in str(err)
    assert SENTINEL not in repr(err)
    assert all(SENTINEL not in str(arg) for arg in err.args)
    assert SENTINEL not in getattr(err, "message", "")


# ── Problem 2: malformed 2xx shapes ─────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("family", list(FAMILIES))
def test_valid_body_with_unknown_extra_fields_is_accepted(family, mode):
    body = dict(FAMILIES[family][1], someFutureField={"nested": [1, "x"]}, anotherOne=None)
    response = run(family, mode, as_json(body))
    assert response["someFutureField"] == {"nested": [1, "x"]}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "value, kind",
    [([1, 2], "array"), ("x", "string"), (None, "null"), (3, "number"), (True, "boolean")],
)
@pytest.mark.parametrize("family", list(FAMILIES))
def test_body_that_is_not_an_object_raises(family, mode, value, kind):
    err = raises_invalid(family, mode, as_json(value))
    assert err.message == f"Expected a JSON object, got {kind}"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("family", list(FAMILIES))
def test_success_false_without_error_raises(family, mode):
    body = dict(FAMILIES[family][1], success=False)
    err = raises_invalid(family, mode, as_json(body))
    assert err.message == "Response reported success: false without an error"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("family", list(FAMILIES))
def test_success_false_with_error_envelope_raises_the_mapped_error(family, mode):
    body = {"success": False, "error": {"code": "NOT_FOUND", "message": "No such act"}}
    with pytest.raises(NotFoundError) as info:
        run(family, mode, as_json(body))
    assert info.value.message == "No such act"
    assert info.value.status == 200


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", [200, 204])
@pytest.mark.parametrize("family", list(FAMILIES))
def test_empty_body_raises(family, mode, status):
    with pytest.raises(InvalidResponseError) as info:
        run(family, mode, raw(status, b"", None))
    assert info.value.message == "Expected JSON, got no body"
    assert info.value.status == status


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "value, kind", [({"a": 1}, "object"), ("x", "string"), (7, "number"), (False, "boolean")]
)
@pytest.mark.parametrize("family", LISTED)
def test_list_field_that_is_not_a_list_raises(family, mode, value, kind):
    _, body, field, _ = FAMILIES[family]
    err = raises_invalid(family, mode, as_json(dict(body, **{field: value})))
    assert err.message == f'Expected "{field}" to be a list, got {kind}'


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("item, kind", [("x", "string"), (1, "number"), ([], "array")])
@pytest.mark.parametrize("family", LISTED)
def test_list_items_that_are_not_objects_raise(family, mode, item, kind):
    _, body, field, _ = FAMILIES[family]
    err = raises_invalid(family, mode, as_json(dict(body, **{field: [{}, item]})))
    assert err.message == f'Expected every item of "{field}" to be an object, got {kind} at index 1'


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("family", REQUIRED)
def test_missing_or_null_required_list_raises(family, mode):
    _, body, field, _ = FAMILIES[family]
    missing = {k: v for k, v in body.items() if k != field}
    for candidate in (missing, dict(body, **{field: None})):
        err = raises_invalid(family, mode, as_json(candidate))
        assert err.message == f'Expected "{field}" in the response; it is missing'


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("family", OPTIONAL)
def test_absent_or_null_optional_list_is_accepted(family, mode):
    _, body, field, _ = FAMILIES[family]
    missing = {k: v for k, v in body.items() if k != field}
    for candidate in (missing, dict(body, **{field: None})):
        run(family, mode, as_json(candidate))


def test_nested_list_fields_are_checked():
    respond = as_json({"success": True, "network": {"cites": [], "citedBy": "x"}})
    with make_client(respond) as client:
        with pytest.raises(InvalidResponseError) as info:
            client.citations.network("32099R0001")
    assert info.value.message == 'Expected "network.citedBy" to be a list, got string'
    respond = as_json({"success": True, "stats": {"mostCited": [1]}})
    with make_client(respond) as client:
        with pytest.raises(InvalidResponseError):
            client.citations.stats()
    with make_client(as_json({"success": True, "network": {}})) as client:
        assert client.citations.network("32099R0001").cites == []


def test_batch_errors_list_is_checked():
    respond = as_json({"success": True, "documents": [], "errors": ["x"]})
    with make_client(respond) as client:
        with pytest.raises(InvalidResponseError):
            client.get_documents_batch(["32099R0001"])


def test_webhook_test_success_false_is_still_a_result():
    respond = as_json({"success": False, "message": "upstream said 500", "delivery": {}})
    with make_client(respond) as client:
        assert client.webhooks.test("w1").ok is False


def test_delete_accepts_204_and_empty_200_but_not_success_false():
    for respond in (raw(204, b"", None), raw(200, b"", None)):
        with make_client(respond) as client:
            assert client.webhooks.delete("w1") is None
    with make_client(as_json({"success": False})) as client:
        with pytest.raises(InvalidResponseError):
            client.webhooks.delete("w1")


# ── Problem 1: no body text in error messages ──────────────────────────

TRUNCATED_JSON = json.dumps({"success": True, "results": [{"text": SENTINEL * 3}]})[:-5]
HTML = f"<html><body><h1>Just a moment</h1><p>{SENTINEL}</p></body></html>"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "content, content_type, media",
    [
        (TRUNCATED_JSON, "application/json; charset=utf-8", "application/json"),
        (HTML, "text/html; charset=UTF-8", "text/html"),
        (SENTINEL, None, "no content type"),
    ],
)
def test_unparseable_200_never_quotes_the_body(caplog, mode, content, content_type, media):
    caplog.set_level(logging.DEBUG)
    err = raises_invalid("semantic_legislation", mode, raw(200, content, content_type))
    assert_no_sentinel(err)
    assert SENTINEL not in caplog.text
    size = len(content.encode())
    assert err.message == f"Expected JSON, got {media} that does not parse as JSON ({size} bytes)"
    assert str(err) == f"[INVALID_RESPONSE] {err.message} (HTTP 200)"
    assert SENTINEL in (err.body_excerpt or "")
    assert len(err.body_excerpt or "") <= 200


@pytest.mark.parametrize("mode", MODES)
def test_wrong_shape_200_never_quotes_the_body(caplog, mode):
    caplog.set_level(logging.DEBUG)
    bodies = [
        {"success": True, "results": {"text": SENTINEL}},
        {"success": True, "results": [SENTINEL]},
        {"success": False, "message": SENTINEL},
        [SENTINEL],
        SENTINEL,
    ]
    for body in bodies:
        err = raises_invalid("semantic_legislation", mode, as_json(body))
        assert_no_sentinel(err)
        assert err.body_excerpt is None
    assert SENTINEL not in caplog.text


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "status, content, content_type, error_cls",
    [
        (502, HTML, "text/html", UpstreamError),
        (500, f"Internal error: {SENTINEL}", "text/plain", InternalServerError),
    ],
)
def test_non_json_error_never_quotes_the_body(
    caplog, mode, status, content, content_type, error_cls
):
    caplog.set_level(logging.DEBUG)

    async def go() -> None:
        async with make_client(raw(status, content, content_type), cls=AsyncLexAPI) as client:
            await client.get_info()

    with pytest.raises(error_cls) as info:
        if mode == "sync":
            with make_client(raw(status, content, content_type)) as client:
                client.get_info()
        else:
            asyncio.run(go())
    err = info.value
    assert_no_sentinel(err)
    assert SENTINEL not in caplog.text
    assert err.message == f"HTTP {status}"
    assert err.body is None
    assert SENTINEL in (err.body_excerpt or "")


def test_json_error_envelope_passes_its_own_message_only(caplog):
    caplog.set_level(logging.DEBUG)
    body = {
        "success": False,
        "error": {"code": "INVALID_PARAMS", "message": "asOf is invalid", "details": SENTINEL},
        "echo": SENTINEL,
    }
    with make_client(as_json(body, status=400)) as client:
        with pytest.raises(InvalidParamsError) as info:
            client.semantic_legislation_search("own funds", as_of="2099-01-01")
    err = info.value
    assert_no_sentinel(err)
    assert SENTINEL not in caplog.text
    assert str(err) == "[INVALID_PARAMS] asOf is invalid (HTTP 400)"
    assert err.body_excerpt is None
    assert err.details == SENTINEL


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (
            404,
            {"success": False, "error": {"code": "NOT_FOUND", "message": "Document not found"}},
            "[NOT_FOUND] Document not found (HTTP 404)",
        ),
        (
            429,
            {"error": "Too many requests", "message": "Retry later", "code": "RATE_LIMITED"},
            "[RATE_LIMITED] Too many requests: Retry later (HTTP 429)",
        ),
        (
            400,
            {"error": "Invalid criteria", "details": ["documentType: unknown"]},
            "[INVALID_PARAMS] Invalid criteria (HTTP 400)",
        ),
        (418, {"success": False, "error": {"code": "TEAPOT", "message": "Short"}}, None),
    ],
)
def test_well_formed_api_errors_are_unchanged(status, body, expected):
    with make_client(as_json(body, status=status)) as client:
        with pytest.raises(LexAPIError) as info:
            client.get_info()
    if expected is None:
        expected = "[TEAPOT] Short (HTTP 418)"
    assert str(info.value) == expected
    assert info.value.body == body
    assert info.value.body_excerpt is None
    if status == 429:
        assert isinstance(info.value, RateLimitedError)


# ── through lexapi.passages ─────────────────────────────────────────────


def _unresolved_hit() -> dict[str, Any]:
    return {
        "id": "asof:32099R0001:art_1",
        "law_id": "32099R0001",
        "law_title": "Regulation (EU) 2099/1 (synthetic)",
        "article_ref": "Article 1",
        "score": 0.9,
        "text": f"[SYNTHETIC] {SENTINEL}",
        "asOfResolved": False,
        "asOfReason": "no_version_listed",
    }


@pytest.mark.parametrize("mode", MODES)
def test_truncated_semantic_body_cannot_leak_unresolved_text(mode):
    full = json.dumps({"success": True, "asOf": "2099-01-01", "results": [_unresolved_hit()]})
    cut = full[: full.index(SENTINEL) + len(SENTINEL) + 2]

    def search(client: Any) -> Any:
        return client.semantic_legislation_search("own funds", as_of="2099-01-01")

    with pytest.raises(InvalidResponseError) as info:
        if mode == "sync":
            with make_client(raw(200, cut, "application/json")) as client:
                passages.from_legislation_search(search(client))
        else:

            async def go() -> None:
                async with make_client(raw(200, cut, "application/json"), cls=AsyncLexAPI) as c:
                    passages.from_legislation_search(await search(c))

            asyncio.run(go())
    assert_no_sentinel(info.value)


def test_complete_semantic_body_still_withholds_unresolved_text():
    body = {"success": True, "asOf": "2099-01-01", "results": [_unresolved_hit()]}
    with make_client(as_json(body)) as client:
        result = passages.from_legislation_search(
            client.semantic_legislation_search("own funds", as_of="2099-01-01")
        )
    assert all(SENTINEL not in (p.text or "") for p in result.passages)


# ── exception objects hold no full body ─────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "content",
    [TRUNCATED_JSON.encode(), b'{"text": "\xff\xfe ' + SENTINEL.encode() + b'"}'],
    ids=["unparsable", "undecodable"],
)
def test_decode_error_is_not_chained(mode, content):
    err = raises_invalid("semantic_legislation", mode, raw(200, content, "application/json"))
    assert err.__context__ is None
    assert err.__cause__ is None


@pytest.mark.parametrize("mode", MODES)
def test_body_excerpt_is_the_first_200_characters(mode):
    content = "<html>" + "x" * 194 + SENTINEL + "y" * 500
    err = raises_invalid("keyword_search", mode, raw(200, content, "text/html"))
    assert err.body_excerpt == content[:200]
    assert SENTINEL not in (err.body_excerpt or "")


@pytest.mark.parametrize("mode", MODES)
def test_wrong_shape_keeps_the_parsed_body(mode):
    body = {"success": True, "results": "x", "extra": SENTINEL}
    err = raises_invalid("keyword_search", mode, as_json(body))
    assert err.body == body
    assert err.body_excerpt is None
    err = raises_invalid("keyword_search", mode, as_json([1, 2]))
    assert err.body == [1, 2]


@pytest.mark.parametrize("content", [b"", b'{"success": true, "results": []}'])
def test_204_is_no_body_with_or_without_content(content):
    with make_client(raw(204, content, "application/json")) as client:
        with pytest.raises(InvalidResponseError) as info:
            client.search("own funds")
        assert info.value.message == "Expected JSON, got no body"
        assert info.value.status == 204
        assert client.webhooks.delete("w1") is None


# ── only string codes and messages are read ─────────────────────────────

ODD_ENVELOPES = [
    {"success": False, "error": {"code": {"x": 1}, "message": {"text": SENTINEL}}},
    {"success": False, "error": {"code": ["NOT_FOUND"], "message": [SENTINEL]}},
    {"success": False, "error": {"code": "", "message": ""}},
    {"success": False, "error": "x", "code": {"x": 1}, "message": {"text": SENTINEL}},
    {"success": False, "error": {}, "code": [1], "message": {"text": SENTINEL}},
]


@pytest.mark.parametrize("body", ODD_ENVELOPES)
@pytest.mark.parametrize("status", [200, 404])
def test_non_string_code_or_message_counts_as_absent(status, body):
    with make_client(as_json(body, status=status)) as client:
        with pytest.raises(LexAPIError) as info:
            client.get_info()
    err = info.value
    assert_no_sentinel(err)
    expected_code = "NOT_FOUND" if status == 404 else "INTERNAL"
    assert err.code == expected_code
    if body["error"] == "x":
        assert err.message == "x"
    else:
        assert err.message == f"HTTP {status}"


def test_non_string_legacy_fields_count_as_absent():
    body = {"error": ["x"], "code": {"a": 1}, "message": {"text": SENTINEL}}
    with make_client(as_json(body, status=429)) as client:
        with pytest.raises(RateLimitedError) as info:
            client.get_info()
    assert info.value.message == "HTTP 429"
    assert info.value.code == "RATE_LIMITED"


# ── pickling ─────────────────────────────────────────────────────────────


def test_errors_survive_a_pickle_round_trip():
    body = {"success": False, "error": {"code": "NOT_FOUND", "message": "gone"}}
    with make_client(as_json(body, status=404)) as client:
        with pytest.raises(NotFoundError) as info:
            client.get_info()
    copy = pickle.loads(pickle.dumps(info.value))
    assert type(copy) is NotFoundError
    assert copy.args == info.value.args == ("[NOT_FOUND] gone (HTTP 404)",)
    assert str(copy) == str(info.value)
    assert (copy.code, copy.message, copy.status, copy.body) == ("NOT_FOUND", "gone", 404, body)
    err = raises_invalid("keyword_search", "sync", raw(200, HTML, "text/html"))
    copy = pickle.loads(pickle.dumps(err))
    assert copy.args == err.args and copy.body_excerpt == err.body_excerpt


def test_empty_legacy_error_string_is_not_an_envelope():
    body = {"error": "", "message": "Readable reason"}
    with make_client(as_json(body, status=400)) as client:
        with pytest.raises(InvalidParamsError) as info:
            client.get_info()
    assert info.value.message == "Readable reason"
    err = raises_invalid("keyword_search", "sync", as_json(dict(body, success=False)))
    assert err.message == "Response reported success: false without an error"
