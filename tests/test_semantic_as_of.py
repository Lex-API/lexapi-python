"""Legislation semantic search: asOf, consolidated-text merge labels, and
the new per-hit / response fields (API #284, lex-api PR #298)."""

import asyncio
import datetime as dt
import json

import httpx
import pytest

from lexapi import (
    AS_OF_REASONS,
    AsyncLexAPI,
    CreditsExhaustedError,
    InvalidParamsError,
    LexAPI,
    SemanticLegislationResponse,
)

# Spec order, copied from openapi.yaml SemanticLegislationResult.asOfReason.
SPEC_AS_OF_REASONS = [
    "not_yet_adopted",
    "not_yet_in_force",
    "no_longer_in_force",
    "in_force_dates_unknown",
    "not_in_language",
    "text_unavailable",
    "article_not_in_version",
    "part_not_resolvable",
    "article_unknown",
    "version_list_unavailable",
    "version_lookup_cap",
    "fetch_budget_exhausted",
    "fetch_pending",
    "fetch_capacity",
    "fetch_failed",
    "fetch_timeout",
    "request_deadline",
    "insufficient_credits",
    "credit_check_failed",
]

NOTE = (
    "The act as amended, consolidated as of 2014-01-01; not the authentic text. "
    "Only the act as published in the Official Journal is authentic (32013R0575)."
)

AS_OF_BODY = {
    "success": True,
    "searchType": "semantic-legislation",
    "query": "own funds requirements",
    "resultCount": 3,
    "results": [
        {
            # resolved, text swapped to the consolidated version in force
            "id": "asof:02013R0575-20140101:art_25",
            "law_id": "02013R0575-20140101",
            "law_title": "Regulation (EU) No 575/2013 (consolidated 2014-01-01)",
            "article_ref": "art_25",
            "chapter": None,
            "section": "Scope",
            "language": "en",
            "score": 0.91,
            "snippet": "Own funds of an institution shall consist of...",
            "consolidated": True,
            "consolidatedAsOf": "2014-01-01",
            "baseCelex": "32013R0575",
            "consolidatedNote": NOTE,
            "baseAct": {"law_id": "32013R0575", "article_ref": "art_25", "score": 0.88},
            "matchedVersion": {
                "id": "c-art_25",
                "law_id": "02013R0575-20250101",
                "consolidatedAsOf": "2025-01-01",
            },
            "asOf": "2019-06-01",
            "asOfResolved": True,
            "asOfVersion": {
                "celex": "02013R0575-20140101",
                "date": "2014-01-01",
                "kind": "consolidated",
            },
            "metadata": {},
        },
        {
            # resolved as the act as published (before its first consolidation)
            "id": "o-32016R0679-art_17",
            "law_id": "32016R0679",
            "law_title": "GDPR",
            "article_ref": "art_17",
            "score": 0.8,
            "snippet": "The data subject shall have the right...",
            "asOf": "2019-06-01",
            "asOfResolved": True,
            "asOfVersion": {"celex": "32016R0679", "date": None, "kind": "as_published"},
        },
        {
            # unresolved: keeps its latest text
            "id": "o-31995L0046-art_12",
            "law_id": "31995L0046",
            "law_title": "Directive 95/46/EC",
            "article_ref": "art_12",
            "score": 0.75,
            "snippet": "Member States shall guarantee every data subject...",
            "asOf": "2019-06-01",
            "asOfResolved": False,
            "asOfReason": "no_longer_in_force",
        },
    ],
    "metadata": {"model": "qwen3-embed"},
    "asOf": "2019-06-01",
    "asOfSummary": {"liveFetches": 1, "unresolved": 1},
    "tookMs": 2410,
    "subscription": {"tier": "PROFESSIONAL", "maxResults": 50},
    "credits": {"operation_weight": 9, "remaining": 991},
}


def capture_client(response_json, status=200, client_cls=LexAPI):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json)

    client = client_cls(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


def test_as_of_reasons_match_spec():
    assert list(AS_OF_REASONS) == SPEC_AS_OF_REASONS


def test_as_of_string_sent_as_asOf():
    client, seen = capture_client(AS_OF_BODY)
    with client:
        client.semantic_legislation_search("own funds requirements", as_of="2019-06-01")
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/legislation/semantic"
    assert seen["body"] == {"query": "own funds requirements", "asOf": "2019-06-01"}


def test_as_of_accepts_date_and_datetime():
    for value in (dt.date(2019, 6, 1), dt.datetime(2019, 6, 1, 23, 59)):
        client, seen = capture_client(AS_OF_BODY)
        with client:
            client.semantic_legislation_search("own funds", as_of=value)
        assert seen["body"]["asOf"] == "2019-06-01"


def test_as_of_omitted_by_default():
    client, seen = capture_client(AS_OF_BODY)
    with client:
        client.semantic_legislation_search("own funds", limit=5, include_text=True)
    assert "asOf" not in seen["body"]
    assert seen["body"] == {"query": "own funds", "limit": 5, "include": ["text"]}


def test_as_of_response_fields_typed():
    client, _ = capture_client(AS_OF_BODY)
    with client:
        resp = client.semantic_legislation_search("own funds", as_of="2019-06-01")

    assert isinstance(resp, SemanticLegislationResponse)
    assert resp.as_of == "2019-06-01"
    assert resp.as_of_summary.live_fetches == 1
    assert resp.as_of_summary.unresolved == 1
    assert resp.max_results == 50
    assert resp.upstream_metadata == {"model": "qwen3-embed"}
    assert resp.units_charged == 9  # 5 search + 4 for one Cellar-served fetch

    swapped, as_published, unresolved = resp.results

    assert swapped.id == "asof:02013R0575-20140101:art_25"
    assert swapped.law_id == "02013R0575-20140101"
    assert swapped.chapter is None
    assert swapped.section == "Scope"
    assert swapped.language == "en"
    assert swapped.snippet.startswith("Own funds")
    assert swapped.text is None  # snippet by default
    assert swapped.consolidated is True
    assert swapped.consolidated_as_of == "2014-01-01"
    assert swapped.base_celex == "32013R0575"
    assert swapped.consolidated_note == NOTE
    assert swapped.authentic_celex == "32013R0575"
    assert swapped.base_act.law_id == "32013R0575"
    assert swapped.base_act.article_ref == "art_25"
    assert swapped.base_act.score == 0.88
    assert swapped.as_of == "2019-06-01"
    assert swapped.as_of_resolved is True
    assert swapped.as_of_reason is None
    assert swapped.as_of_version.celex == "02013R0575-20140101"
    assert swapped.as_of_version.date == "2014-01-01"
    assert swapped.as_of_version.kind == "consolidated"
    assert swapped.matched_version.id == "c-art_25"
    assert swapped.matched_version.law_id == "02013R0575-20250101"
    assert swapped.matched_version.consolidated_as_of == "2025-01-01"

    assert as_published.consolidated is False  # key absent on non-consolidated hits
    assert as_published.base_act is None
    assert as_published.authentic_celex == "32016R0679"
    assert as_published.as_of_version.kind == "as_published"
    assert as_published.as_of_version.date is None
    assert as_published.matched_version is None

    assert unresolved.as_of_resolved is False
    assert unresolved.as_of_reason == "no_longer_in_force"
    assert unresolved.as_of_reason in AS_OF_REASONS
    assert unresolved.as_of_version is None


def test_consolidated_merge_without_as_of():
    body = {
        "success": True,
        "searchType": "semantic-legislation",
        "query": "q",
        "resultCount": 1,
        "results": [
            {
                "id": "c-art_25",
                "law_id": "02013R0575-20250101",
                "article_ref": "art_25",
                "score": 0.91,
                "consolidated": True,
                "consolidatedAsOf": "2025-01-01",
                "baseCelex": "32013R0575",
                "consolidatedNote": "The act as amended, consolidated as of 2025-01-01; ...",
                "baseAct": {"law_id": "32013R0575", "article_ref": "art_25", "score": None},
            }
        ],
        "tookMs": 900,
    }
    client, _ = capture_client(body)
    with client:
        resp = client.semantic_legislation_search("q")
    hit = resp.results[0]
    assert resp.as_of is None
    assert resp.as_of_summary is None
    assert hit.as_of is None and hit.as_of_resolved is None
    assert hit.consolidated is True
    assert hit.base_act.score is None  # nullable per spec
    assert hit.authentic_celex == "32013R0575"


def test_unknown_future_reason_is_kept():
    body = dict(AS_OF_BODY)
    body["results"] = [
        {"law_id": "x", "asOf": "2019-06-01", "asOfResolved": False, "asOfReason": "brand_new"}
    ]
    client, _ = capture_client(body)
    with client:
        hit = client.semantic_legislation_search("q", as_of="2019-06-01").results[0]
    assert hit.as_of_reason == "brand_new"  # forward compatible, not dropped


def test_invalid_as_of_is_typed_400():
    # The controller answers in the legacy shape; the credit middleware refunds it.
    client, _ = capture_client(
        {"error": "Invalid asOf", "message": "asOf cannot be in the future"}, status=400
    )
    with client:
        with pytest.raises(InvalidParamsError) as info:
            client.semantic_legislation_search("q", as_of="2999-01-01")
    assert info.value.status == 400
    assert info.value.code == "INVALID_PARAMS"
    assert "asOf cannot be in the future" in info.value.message


def test_search_402_carries_pool_details():
    client, _ = capture_client(
        {
            "success": False,
            "error": {
                "code": "CREDITS_EXHAUSTED",
                "message": "Credit pool exhausted",
                "details": {
                    "operation": "semantic_legis",
                    "weight": 5,
                    "available": 2,
                    "included": 500,
                    "topup": 0,
                    "used": 498,
                    "resetsAt": "2026-11-01T00:00:00.000Z",
                },
            },
        },
        status=402,
    )
    with client:
        with pytest.raises(CreditsExhaustedError) as info:
            client.semantic_legislation_search("q", as_of="2019-06-01")
    err = info.value
    assert err.operation == "semantic_legis"
    assert err.weight == 5
    assert err.available == 2
    assert err.resets_at == "2026-11-01T00:00:00.000Z"


def test_async_as_of_parity():
    async def run():
        client, seen = capture_client(AS_OF_BODY, client_cls=AsyncLexAPI)
        async with client:
            resp = await client.semantic_legislation_search("own funds", as_of=dt.date(2019, 6, 1))
        return resp, seen

    resp, seen = asyncio.run(run())
    assert seen["body"]["asOf"] == "2019-06-01"
    assert resp.results[0].as_of_version.kind == "consolidated"
    assert resp.as_of_summary.unresolved == 1
