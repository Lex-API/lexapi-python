"""Semantic search endpoints, including HyDE billing visibility."""

import asyncio
import json

import httpx
import pytest

from lexapi import AsyncLexAPI, LexAPI, TierForbiddenError

CASE_LAW_BODY = {
    "success": True,
    "searchType": "semantic-case-law",
    "query": "transfer of personal data to third countries",
    "resultCount": 1,
    "results": [
        {
            "case_id": "b2a6a5c0-0000-0000-0000-000000000000",
            "score": 0.83,
            "text": "The Court holds that...",
            "case_name": "Schrems II",
            "case_number": "C-311/18",
            "ecli": "ECLI:EU:C:2020:559",
            "court": "Court of Justice",
            "metadata": {"celex_id": "62018CJ0311", "document_type": "judgment"},
        }
    ],
    "tookMs": 812,
    "subscription": {"tier": "PROFESSIONAL", "maxResults": 20},
    "credits": {"operation_weight": 5, "remaining": 995},
}


def capture_client(response_json, status=200):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json)

    client = LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


def test_semantic_search_request_and_parse():
    client, seen = capture_client(CASE_LAW_BODY)
    with client:
        resp = client.semantic_search(
            "transfer of personal data to third countries",
            min_score=0.6,
            limit=10,
            language="en",
        )

    assert seen["path"] == "/api/v1/search/semantic"
    assert seen["body"] == {
        "query": "transfer of personal data to third countries",
        "min_score": 0.6,
        "limit": 10,
        "language": "en",
    }
    hit = resp.results[0]
    assert hit.score == 0.83
    assert hit.celex == "62018CJ0311"  # persistent identity via metadata.celex_id
    assert hit.document_type == "judgment"
    assert hit.ecli == "ECLI:EU:C:2020:559"
    assert resp.took_ms == 812
    assert resp.units_charged == 5
    # no hyde requested → field absent → None
    assert resp.hyde is None
    assert resp.hypothetical_document is None
    assert resp.hint is None


def test_hyde_flag_sent_only_when_true():
    client, seen = capture_client(CASE_LAW_BODY)
    with client:
        client.semantic_search("credit scoring article 22", hyde=True)
    assert seen["body"] == {"query": "credit scoring article 22", "hyde": True}

    client, seen = capture_client(CASE_LAW_BODY)
    with client:
        client.semantic_search("credit scoring article 22")
    assert "hyde" not in seen["body"]


def test_hyde_response_fields_exposed():
    body = dict(CASE_LAW_BODY)
    body["hyde"] = True
    body["hypotheticalDocument"] = "A hypothetical judgment passage about Article 22..."
    body["credits"] = {"operation_weight": 15, "remaining": 985}
    client, _ = capture_client(body)
    with client:
        resp = client.semantic_search("credit scoring article 22", hyde=True)

    assert resp.hyde is True
    assert resp.hypothetical_document.startswith("A hypothetical judgment")
    assert resp.units_charged == 15  # HyDE premium visible via credits


def test_hyde_fallback_reports_false_and_base_rate():
    # Best-effort: generation failed → hyde: false, premium auto-refunded.
    body = dict(CASE_LAW_BODY)
    body["hyde"] = False
    body["credits"] = {"operation_weight": 5, "remaining": 995}
    client, _ = capture_client(body)
    with client:
        resp = client.semantic_search("credit scoring article 22", hyde=True)

    assert resp.hyde is False
    assert resp.hypothetical_document is None
    assert resp.units_charged == 5  # refunded to base rate


def test_include_text_maps_to_include_array():
    client, seen = capture_client(CASE_LAW_BODY)
    with client:
        client.semantic_search("adequacy decision", include_text=True)
    assert seen["body"]["include"] == ["text"]


def test_low_confidence_hint_surfaced():
    body = dict(CASE_LAW_BODY)
    body["hint"] = "Results are low-confidence; consider lowering min_score."
    client, _ = capture_client(body)
    with client:
        resp = client.semantic_search("obscure query")
    assert "low-confidence" in resp.hint


def test_semantic_legislation_search():
    body = {
        "success": True,
        "searchType": "semantic-legislation",
        "query": "right to be forgotten",
        "resultCount": 1,
        "results": [
            {
                "score": 0.91,
                "text": "The data subject shall have the right...",
                "article_ref": "Article 17",
                "law_id": "32016R0679",
                "law_title": "GDPR",
                "metadata": {},
            }
        ],
        "tookMs": 300,
    }
    client, seen = capture_client(body)
    with client:
        resp = client.semantic_legislation_search(
            "right to be forgotten", min_score=0.5, limit=5
        )

    assert seen["path"] == "/api/v1/legislation/semantic"
    assert seen["body"] == {"query": "right to be forgotten", "min_score": 0.5, "limit": 5}
    hit = resp.results[0]
    assert hit.article_ref == "Article 17"
    assert hit.law_id == "32016R0679"
    assert resp.search_type == "semantic-legislation"


def test_semantic_tier_forbidden():
    client, _ = capture_client(
        {
            "success": False,
            "error": {"code": "TIER_FORBIDDEN", "message": "Plan lacks semantic search"},
        },
        status=403,
    )
    with client:
        with pytest.raises(TierForbiddenError):
            client.semantic_search("anything")


def test_semantic_usage_block_legacy_plans():
    body = dict(CASE_LAW_BODY)
    del body["credits"]
    body["semanticUsage"] = {"current": 2, "limit": 50, "remaining": 48}
    client, _ = capture_client(body)
    with client:
        resp = client.semantic_search("query")
    assert resp.semantic_usage == {"current": 2, "limit": 50, "remaining": 48}


def test_async_semantic_parity():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CASE_LAW_BODY)

    async def run():
        async with AsyncLexAPI(
            api_key="lex_test_key", transport=httpx.MockTransport(handler)
        ) as client:
            return await client.semantic_search("schrems", hyde=True)

    resp = asyncio.run(run())
    assert resp.results[0].case_number == "C-311/18"
