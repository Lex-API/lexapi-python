"""Incomplete national semantic searches, the 422 FILTER_UNREACHABLE code,
503 retry-after vs rate limits, and /resolve ``verified`` / ``eli-cellar``."""

import asyncio

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    InvalidParamsError,
    LexAPI,
    NationalSemanticFilterUnreachableError,
    NationalSemanticUnavailableError,
    RateLimitedError,
    ServiceUnavailableError,
)
from lexapi._retry import RetryConfig

APP_HEADERS = {"x-lexapi-version": "2.16.0"}

INCOMPLETE_503 = {
    "success": False,
    "code": "NATIONAL_SEMANTIC_UNAVAILABLE",
    "error": "National semantic search incomplete",
    "message": "The Czech Republic (CZ) semantic index could not complete this search in time.",
    "jurisdiction": "CZ",
    "partialReason": "SEARCH_INCOMPLETE",
    "retryAfter": 10,
}

REPEATED_503 = {
    "success": False,
    "code": "NATIONAL_SEMANTIC_UNAVAILABLE",
    "error": "National semantic search incomplete",
    "message": "The Czech Republic (CZ) semantic index has repeatedly been unable to complete it.",
    "jurisdiction": "CZ",
    "partialReason": "SEARCH_INCOMPLETE",
}

UNREACHABLE_422 = {
    "success": False,
    "code": "NATIONAL_SEMANTIC_FILTER_UNREACHABLE",
    "error": "National semantic search cannot cover these filters",
    "message": "The Germany (DE) semantic index cannot search this slice completely.",
    "jurisdiction": "DE",
    "partialReason": "SEARCH_INCOMPLETE",
}

EMPTY_OK = {"success": True, "jurisdiction": "CZ", "resultCount": 0, "results": []}

CASE_LAW_HIT = {
    "documentId": "AT-JUD-OGH-1",
    "jurisdiction": "AT",
    "score": 0.81,
    "court": "OGH",
    "snippet": "Der Oberste Gerichtshof …",
    "metadata": {"celex_id": "AT-JUD-OGH-1"},
    "document": {"id": "AT-JUD-OGH-1", "jurisdiction": "AT", "documentKind": "decision"},
    "attribution": "Source: RIS.",
}

LEGISLATION_HIT = {
    "law_id": "DE-1",
    "documentId": "DE-1",
    "jurisdiction": "DE",
    "score": 0.7,
    "snippet": "s",
    "law_title": "Bundesdatenschutzgesetz",
    "article_ref": "§ 1",
    "section": None,
    "language": "de",
    "in_force": True,
    "valid_from": None,
    "valid_to": None,
    "law_type": None,
    "document": {"id": "DE-1", "jurisdiction": "DE", "documentKind": "act"},
    "attribution": "Source: gesetze-im-internet.de.",
}


def sequence_client(*responses, client_cls=LexAPI, **kwargs):
    seen = []
    items = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return items[min(len(seen) - 1, len(items) - 1)]

    client = client_cls(api_key="lex_test_key", transport=httpx.MockTransport(handler), **kwargs)
    return client, seen


# ── 200 partial ─────────────────────────────────────────────────────


def test_case_law_partial_fields():
    body = {
        "success": True,
        "searchType": "semantic-case-law",
        "jurisdiction": "AT",
        "resultCount": 1,
        "results": [CASE_LAW_HIT],
        "partial": True,
        "partialReason": "SEARCH_INCOMPLETE",
        "hint": "The national index did not finish this search. A retry may help.",
    }
    client, _ = sequence_client(httpx.Response(200, json=body))
    with client:
        resp = client.national.semantic_search("AT", "Datenschutz")
    assert resp.partial is True
    assert resp.partial_reason == "SEARCH_INCOMPLETE"
    assert "retry" in (resp.hint or "")
    assert len(resp.results) == 1


def test_legislation_partial_fields_and_complete_default():
    partial = {
        "success": True,
        "searchType": "semantic-legislation",
        "jurisdiction": "DE",
        "resultCount": 1,
        "results": [LEGISLATION_HIT],
        "partial": True,
        "partialReason": "SEARCH_INCOMPLETE",
    }
    client, _ = sequence_client(
        httpx.Response(200, json=partial), httpx.Response(200, json=EMPTY_OK)
    )
    with client:
        first = client.national.legislation_semantic_search("DE", "Haftung")
        second = client.national.legislation_semantic_search("DE", "Haftung")
    assert first.partial is True
    assert first.partial_reason == "SEARCH_INCOMPLETE"
    assert len(first.results) == 1
    assert second.partial is False
    assert second.partial_reason is None


# ── 503 NATIONAL_SEMANTIC_UNAVAILABLE + SEARCH_INCOMPLETE ───────────


def test_incomplete_503_is_not_a_rate_limit():
    client, seen = sequence_client(
        httpx.Response(503, json=INCOMPLETE_503, headers={**APP_HEADERS, "retry-after": "10"})
    )
    with client:
        with pytest.raises(NationalSemanticUnavailableError) as info:
            client.national.semantic_search("CZ", "liability for damage")
    err = info.value
    assert not isinstance(err, RateLimitedError)
    assert isinstance(err, ServiceUnavailableError)
    assert err.code == "NATIONAL_SEMANTIC_UNAVAILABLE"
    assert err.retry_after == 10
    assert err.partial_reason == "SEARCH_INCOMPLETE"
    assert err.jurisdiction == "CZ"
    # retry_read_only_posts is off by default: a POST is not retried.
    assert len(seen) == 1


def test_incomplete_503_body_retry_after_without_header():
    client, _ = sequence_client(httpx.Response(503, json=INCOMPLETE_503, headers=APP_HEADERS))
    with client:
        with pytest.raises(NationalSemanticUnavailableError) as info:
            client.national.semantic_search("CZ", "liability for damage")
    assert info.value.retry_after == 10


def test_incomplete_503_with_wait_is_retried_when_read_only_posts_opt_in():
    client, seen = sequence_client(
        httpx.Response(
            503,
            json={**INCOMPLETE_503, "retryAfter": 0},
            headers={**APP_HEADERS, "retry-after": "0"},
        ),
        httpx.Response(200, json=EMPTY_OK),
        retry_config=RetryConfig(retry_read_only_posts=True),
    )
    with client:
        resp = client.national.semantic_search("CZ", "liability for damage")
    assert resp.jurisdiction == "CZ"
    assert len(seen) == 2


def test_repeated_incomplete_503_without_wait_is_never_retried():
    client, seen = sequence_client(
        httpx.Response(503, json=REPEATED_503, headers=APP_HEADERS),
        httpx.Response(200, json=EMPTY_OK),
        retry_config=RetryConfig(retry_read_only_posts=True),
    )
    with client:
        with pytest.raises(NationalSemanticUnavailableError) as info:
            client.national.semantic_search("CZ", "liability for damage")
    assert info.value.retry_after is None
    assert info.value.partial_reason == "SEARCH_INCOMPLETE"
    assert len(seen) == 1


def test_incomplete_503_is_retried_at_most_once_per_call():
    retryable = httpx.Response(
        503, json={**INCOMPLETE_503, "retryAfter": 0}, headers={**APP_HEADERS, "retry-after": "0"}
    )
    client, seen = sequence_client(
        retryable,
        retryable,
        httpx.Response(200, json=EMPTY_OK),
        retry_config=RetryConfig(max_retries=3, retry_read_only_posts=True),
    )
    with client:
        with pytest.raises(NationalSemanticUnavailableError):
            client.national.semantic_search("CZ", "liability for damage")
    assert len(seen) == 2


def test_async_incomplete_503_is_retried_at_most_once_per_call():
    async def run():
        retryable = httpx.Response(
            503,
            json={**INCOMPLETE_503, "retryAfter": 0},
            headers={**APP_HEADERS, "retry-after": "0"},
        )
        client, seen = sequence_client(
            retryable,
            retryable,
            httpx.Response(200, json=EMPTY_OK),
            client_cls=AsyncLexAPI,
            retry_config=RetryConfig(max_retries=3, retry_read_only_posts=True),
        )
        async with client:
            with pytest.raises(NationalSemanticUnavailableError):
                await client.national.semantic_search("CZ", "liability for damage")
        return seen

    assert len(asyncio.run(run())) == 2


def test_typed_envelope_details_partial_reason_without_wait_is_not_retried():
    body = {
        "success": False,
        "error": {
            "code": "NATIONAL_SEMANTIC_UNAVAILABLE",
            "message": "National semantic search incomplete",
            "details": {"jurisdiction": "CZ", "partialReason": "SEARCH_INCOMPLETE"},
        },
    }
    client, seen = sequence_client(
        httpx.Response(503, json=body, headers=APP_HEADERS),
        httpx.Response(200, json=EMPTY_OK),
        retry_config=RetryConfig(retry_read_only_posts=True),
    )
    with client:
        with pytest.raises(NationalSemanticUnavailableError) as info:
            client.national.semantic_search("CZ", "liability for damage")
    assert info.value.partial_reason == "SEARCH_INCOMPLETE"
    assert len(seen) == 1


def test_typed_envelope_wait_only_in_details_is_retried_once():
    body = {
        "success": False,
        "error": {
            "code": "NATIONAL_SEMANTIC_UNAVAILABLE",
            "message": "National semantic search incomplete",
            "details": {
                "jurisdiction": "CZ",
                "partialReason": "SEARCH_INCOMPLETE",
                "retryAfter": 0,
            },
        },
    }
    client, seen = sequence_client(
        httpx.Response(503, json=body, headers=APP_HEADERS),
        httpx.Response(503, json=body, headers=APP_HEADERS),
        httpx.Response(200, json=EMPTY_OK),
        retry_config=RetryConfig(max_retries=3, retry_read_only_posts=True),
    )
    with client:
        with pytest.raises(NationalSemanticUnavailableError) as info:
            client.national.semantic_search("CZ", "liability for damage")
    assert info.value.retry_after == 0
    assert len(seen) == 2


def test_async_repeated_incomplete_503_without_wait_is_never_retried():
    async def run():
        client, seen = sequence_client(
            httpx.Response(503, json=REPEATED_503, headers=APP_HEADERS),
            httpx.Response(200, json=EMPTY_OK),
            client_cls=AsyncLexAPI,
            retry_config=RetryConfig(retry_read_only_posts=True),
        )
        async with client:
            with pytest.raises(NationalSemanticUnavailableError):
                await client.national.semantic_search("CZ", "liability for damage")
        return seen

    assert len(asyncio.run(run())) == 1


def test_plain_unavailable_503_has_no_partial_reason():
    body = {
        "success": False,
        "code": "NATIONAL_SEMANTIC_UNAVAILABLE",
        "error": "National semantic index unavailable",
        "jurisdiction": "AT",
    }
    client, _ = sequence_client(
        httpx.Response(503, json=body, headers=APP_HEADERS), retry_config=RetryConfig(max_retries=0)
    )
    with client:
        with pytest.raises(NationalSemanticUnavailableError) as info:
            client.national.semantic_search("AT", "Datenschutz")
    assert info.value.partial_reason is None
    assert info.value.retry_after is None


# ── 503 with a retry-after is never a rate limit ────────────────────


def test_codeless_503_with_retry_after_is_service_unavailable():
    body = {"success": False, "error": "Service unavailable", "retryAfter": 5}
    client, _ = sequence_client(
        httpx.Response(503, json=body, headers={**APP_HEADERS, "retry-after": "0"}),
        retry_config=RetryConfig(max_retries=0),
    )
    with client:
        with pytest.raises(ServiceUnavailableError) as info:
            client.citations.counterparts("AT-NOR-10001597")
    err = info.value
    assert not isinstance(err, RateLimitedError)
    assert err.code == "SERVICE_UNAVAILABLE"
    # Only the header is read on a code-less 503.
    assert err.retry_after == 0


def test_codeless_app_503_with_retry_after_keeps_get_retry_policy():
    body = {"success": False, "error": "Service unavailable", "retryAfter": 0}
    client, seen = sequence_client(
        httpx.Response(503, json=body, headers={**APP_HEADERS, "retry-after": "0"}),
        httpx.Response(200, json={"success": True, "via": []}, headers=APP_HEADERS),
    )
    with client:
        client.citations.counterparts("AT-NOR-10001597")
    assert len(seen) == 2


def test_only_429_is_rate_limited():
    client, _ = sequence_client(
        httpx.Response(429, json={"error": "Rate limit exceeded", "retryAfter": 1}),
        retry_config=RetryConfig(max_retries=0),
    )
    with client:
        with pytest.raises(RateLimitedError):
            client.citations.counterparts("AT-NOR-10001597")


# ── 422 NATIONAL_SEMANTIC_FILTER_UNREACHABLE ────────────────────────


def test_filter_unreachable_422():
    client, seen = sequence_client(
        httpx.Response(422, json=UNREACHABLE_422, headers=APP_HEADERS),
        httpx.Response(200, json=EMPTY_OK),
        retry_config=RetryConfig(retry_read_only_posts=True),
    )
    with client:
        with pytest.raises(NationalSemanticFilterUnreachableError) as info:
            client.national.legislation_semantic_search("DE", "Haftung", filters={"inForce": True})
    err = info.value
    assert isinstance(err, InvalidParamsError)
    assert err.code == "NATIONAL_SEMANTIC_FILTER_UNREACHABLE"
    assert err.status == 422
    assert err.jurisdiction == "DE"
    assert err.partial_reason == "SEARCH_INCOMPLETE"
    assert err.retry_after is None
    assert err.param is None
    assert len(seen) == 1


# ── /resolve ────────────────────────────────────────────────────────


def test_resolve_verified_and_eli_cellar():
    body = {
        "success": True,
        "identifier": "http://data.europa.eu/eli/dec/2019/166/oj",
        "identifierType": "eli",
        "resolvedVia": "eli-cellar",
        "verified": True,
        "celex": "32019D0003(01)",
        "urls": {},
    }
    client, _ = sequence_client(httpx.Response(200, json=body))
    with client:
        resp = client.resolve("http://data.europa.eu/eli/dec/2019/166/oj")
    assert resp.resolved_via == "eli-cellar"
    assert resp.verified is True


def test_resolve_verified_is_optional():
    body = {
        "success": True,
        "identifier": "32016R0679",
        "identifierType": "celex",
        "resolvedVia": "celex",
        "celex": "32016R0679",
        "urls": {},
    }
    client, _ = sequence_client(httpx.Response(200, json=body))
    with client:
        resp = client.resolve("32016R0679")
    assert resp.verified is None
