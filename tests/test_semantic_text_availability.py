"""``textAvailability`` on semantic case-law hits (API 2.30.0)."""

import asyncio
import json

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    LexAPI,
    NationalSemanticCaseLawResult,
    SemanticHitTextAvailability,
    TextAvailability,
)

LOOPBACK = "http://127.0.0.1:9/api/v1"


def _hit(celex, **extra):
    return {
        "case_id": f"id-{celex}",
        "score": 0.81,
        "case_name": "Case",
        "metadata": {"celex_id": celex, "document_type": "judgment"},
        **extra,
    }


def _body(*hits):
    return {
        "success": True,
        "searchType": "semantic-case-law",
        "resultCount": len(hits),
        "results": list(hits),
        "credits": {"operation_weight": 5, "remaining": 995},
    }


def _search(body, **kwargs):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with LexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as client:
        resp = client.semantic_search("data transfers", **kwargs)
    return resp, seen["body"]


def test_text_present():
    resp, sent = _search(
        _body(_hit("62018CJ0311", text="The Court holds...", textAvailability={"available": True})),
        include_text=True,
    )
    assert sent["include"] == ["text"]
    hit = resp.results[0]
    assert hit.text == "The Court holds..."
    assert hit.snippet is None
    ta = hit.text_availability
    assert isinstance(ta, SemanticHitTextAvailability)
    assert not isinstance(ta, TextAvailability)
    assert ta.available is True
    assert ta.reason is None
    assert ta.raw == {"available": True}


def test_not_in_corpus_keeps_snippet():
    resp, _ = _search(
        _body(
            _hit(
                "62019CJ0001",
                snippet="Summary head...",
                textAvailability={"available": False, "reason": "not_in_corpus"},
            )
        ),
        include_text=True,
    )
    hit = resp.results[0]
    assert hit.text is None
    assert hit.snippet == "Summary head..."
    ta = hit.text_availability
    assert ta is not None
    assert (ta.available, ta.reason) == (False, "not_in_corpus")


def test_unknown_reason():
    resp, _ = _search(
        _body(
            _hit(
                "62019CJ0002",
                snippet="s",
                textAvailability={"available": False, "reason": "unknown"},
            )
        ),
        include_text=True,
    )
    ta = resp.results[0].text_availability
    assert ta is not None
    assert (ta.available, ta.reason) == (False, "unknown")


def test_future_reason_is_kept_as_string():
    payload = {"available": False, "reason": "some_future_reason", "extra": 1}
    resp, _ = _search(
        _body(_hit("62019CJ0003", snippet="s", textAvailability=payload)), include_text=True
    )
    ta = resp.results[0].text_availability
    assert ta is not None
    assert ta.reason == "some_future_reason"
    assert ta.raw == payload


def test_blank_text_is_passed_through():
    resp, _ = _search(
        _body(
            _hit(
                "62019CJ0004",
                text="",
                snippet="s",
                textAvailability={"available": False, "reason": "unknown"},
            )
        ),
        include_text=True,
    )
    hit = resp.results[0]
    assert hit.text == ""
    assert hit.snippet == "s"
    assert hit.text_availability is not None and hit.text_availability.available is False


def test_without_include_field_is_none():
    resp, sent = _search(_body(_hit("62018CJ0311", snippet="s")))
    assert "include" not in sent
    hit = resp.results[0]
    assert hit.text_availability is None
    assert hit.snippet == "s"


@pytest.mark.parametrize(
    "value",
    [
        "available",
        True,
        None,
        3,
        ["available"],
        {},
        {"reason": "not_in_corpus"},
        {"available": "false", "reason": "not_in_corpus"},
        {"available": 0},
        {"available": None},
    ],
)
def test_malformed_reads_as_absent(value):
    resp, _ = _search(
        _body(_hit("62019CJ0005", snippet="s", textAvailability=value)), include_text=True
    )
    hit = resp.results[0]
    assert hit.text_availability is None
    assert hit.raw["textAvailability"] == value
    assert hit.snippet == "s"


def test_non_string_reason_reads_as_none():
    resp, _ = _search(
        _body(_hit("62019CJ0006", snippet="s", textAvailability={"available": False, "reason": 7})),
        include_text=True,
    )
    ta = resp.results[0].text_availability
    assert ta is not None
    assert (ta.available, ta.reason) == (False, None)
    assert ta.raw["reason"] == 7


def test_mixed_hits_and_async():
    body = _body(
        _hit("62018CJ0311", text="T", textAvailability={"available": True}),
        _hit(
            "62019CJ0001",
            snippet="s",
            textAvailability={"available": False, "reason": "not_in_corpus"},
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, json=body)

    async def run():
        async with AsyncLexAPI(
            "k", base_url=LOOPBACK, transport=httpx.MockTransport(handler), max_retries=0
        ) as client:
            return await client.semantic_search("q", include_text=True)

    resp = asyncio.run(run())
    got = [(h.celex, h.text_availability.available) for h in resp.results if h.text_availability]
    assert got == [("62018CJ0311", True), ("62019CJ0001", False)]
    assert resp.results[1].text_availability.reason == "not_in_corpus"


def test_new_names_are_exported():
    import lexapi

    assert "SemanticHitTextAvailability" in lexapi.__all__
    assert "SemanticHitTextUnavailableReason" in lexapi.__all__


def _national_search(hit):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, json=_body(hit))

    transport = httpx.MockTransport(handler)
    with LexAPI("k", base_url=LOOPBACK, transport=transport, max_retries=0) as client:
        return client.national.semantic_search("AT", "Datenschutz").results[0]


def test_national_hit_has_no_text_availability():
    hit = _national_search({"documentId": "AT-JUS-1", "score": 0.8, "snippet": "s"})
    assert isinstance(hit, NationalSemanticCaseLawResult)
    assert hit.text_availability is None


def test_national_hit_reads_text_availability_if_sent():
    hit = _national_search(
        {
            "documentId": "AT-JUS-1",
            "score": 0.8,
            "snippet": "s",
            "textAvailability": {"available": False, "reason": "not_in_corpus"},
        }
    )
    ta = hit.text_availability
    assert ta is not None
    assert (ta.available, ta.reason) == (False, "not_in_corpus")
