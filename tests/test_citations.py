"""Citation-graph namespace: request shapes and typed parsing."""

import asyncio
import json

import httpx

from lexapi import AsyncLexAPI, LexAPI


def capture_client(response_json, status=200):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json)

    client = LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


def test_extract_fresh():
    body = {
        "success": True,
        "message": "Extracted 3 citations",
        "document": {"celexNumber": "32016R0679", "title": "GDPR"},
        "citationCount": 3,
        "citations": [
            {
                "targetCelex": "31995L0046",
                "citationType": "repeal",
                "contextSnippet": "Directive 95/46/EC is repealed",
            }
        ],
        "alreadyExtracted": False,
    }
    client, seen = capture_client(body)
    with client:
        resp = client.citations.extract("32016R0679")

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/citations/extract"
    assert seen["body"] == {"celexNumber": "32016R0679"}
    assert resp.citation_count == 3
    assert resp.already_extracted is False
    edge = resp.citations[0]
    assert edge.target_celex == "31995L0046"
    assert edge.citation_type == "repeal"


def test_extract_already_extracted():
    client, _ = capture_client(
        {"success": True, "alreadyExtracted": True, "citationCount": 12, "citations": []}
    )
    with client:
        resp = client.citations.extract("32016R0679")
    assert resp.already_extracted is True
    assert resp.citations == []


def test_cited_by_params_and_groups():
    body = {
        "success": True,
        "targetDocument": "31995L0046",
        "totalCitations": 250,
        "uniqueDocuments": 40,
        "limit": 2,
        "offset": 10,
        "citedBy": [
            {
                "celexNumber": "32016R0679",
                "title": "GDPR",
                "citationCount": 2,
                "citations": [
                    {
                        "citationType": "repeal",
                        "contextSnippet": "...",
                        "extractedAt": "2026-06-01T00:00:00.000Z",
                    }
                ],
            }
        ],
    }
    client, seen = capture_client(body)
    with client:
        resp = client.citations.cited_by("31995L0046", citation_type="repeal", limit=2, offset=10)

    assert seen["path"] == "/api/v1/citations/cited-by/31995L0046"
    assert seen["params"] == {"citationType": "repeal", "limit": "2", "offset": "10"}
    assert resp.target_document == "31995L0046"
    assert resp.total_citations == 250
    group = resp.cited_by[0]
    assert group.celex_number == "32016R0679"
    assert group.citations[0].citation_type == "repeal"
    assert group.citations[0].extracted_at == "2026-06-01T00:00:00.000Z"


def test_cites():
    body = {
        "success": True,
        "sourceDocument": "32016R0679",
        "totalCitations": 5,
        "uniqueDocuments": 5,
        "limit": 100,
        "offset": 0,
        "cites": [{"celexNumber": "31995L0046", "citationCount": 1, "citations": []}],
    }
    client, seen = capture_client(body)
    with client:
        resp = client.citations.cites("32016R0679")

    assert seen["path"] == "/api/v1/citations/cites/32016R0679"
    assert seen["params"] == {}
    assert resp.source_document == "32016R0679"
    assert resp.cites[0].celex_number == "31995L0046"


def test_network_counts_and_paging():
    body = {
        "success": True,
        "document": "32016R0679",
        "network": {
            "celexNumber": "32016R0679",
            "citesCount": 1,
            "citedByCount": 1,
            "cites": [
                {
                    "celexNumber": "31995L0046",
                    "title": "Directive 95/46",
                    "count": 2,
                    "types": ["repeal", "reference"],
                }
            ],
            "citedBy": [{"celexNumber": "62018CJ0311", "count": 1, "types": ["reference"]}],
        },
        "paging": {"limit": 100, "offset": 0, "citesTotal": 5, "citedByTotal": 250},
    }
    client, seen = capture_client(body)
    with client:
        resp = client.citations.network("32016R0679", limit=100)

    assert seen["path"] == "/api/v1/citations/network/32016R0679"
    assert resp.cites[0].types == ["repeal", "reference"]
    assert resp.cited_by[0].celex_number == "62018CJ0311"
    assert resp.cites_count == 1
    assert resp.cites_total == 5
    assert resp.cited_by_total == 250
    assert resp.partial is False


def test_network_partial_timeout_is_not_an_error():
    body = {
        "success": True,
        "document": "62018CJ0311",
        "network": {
            "celexNumber": "62018CJ0311",
            "citesCount": 0,
            "citedByCount": 0,
            "cites": [],
            "citedBy": [],
        },
        "paging": {"limit": 100, "offset": 0, "citesTotal": None, "citedByTotal": None},
        "partial": True,
        "partialReason": "TIMEOUT",
        "message": "Citation network exceeded the 30000ms server-side budget.",
        "timeoutMs": 30000,
    }
    client, _ = capture_client(body)
    with client:
        resp = client.citations.network("62018CJ0311")

    assert resp.partial is True
    assert resp.partial_reason == "TIMEOUT"
    assert resp.timeout_ms == 30000
    assert resp.cites == []
    assert resp.cites_total is None


def test_path_found_and_not_found():
    found_body = {
        "success": True,
        "from": "32016R0679",
        "to": "31995L0046",
        "found": True,
        "pathLength": 1,
        "maxDepth": 3,
        "path": [
            {"celexNumber": "32016R0679", "title": "GDPR"},
            {"celexNumber": "31995L0046", "title": "Directive 95/46"},
        ],
    }
    client, seen = capture_client(found_body)
    with client:
        resp = client.citations.path("32016R0679", "31995L0046", max_depth=4)

    assert seen["path"] == "/api/v1/citations/path/32016R0679/31995L0046"
    assert seen["params"] == {"maxDepth": "4"}
    assert resp.found is True
    assert resp.path_length == 1
    assert [node.celex_number for node in resp.path] == ["32016R0679", "31995L0046"]

    not_found = {
        "success": True,
        "from": "32016R0679",
        "to": "99999X9999",
        "found": False,
        "pathLength": None,
        "maxDepth": 3,
        "path": [],
        "message": "No citation path from 32016R0679 to 99999X9999 within 3 hops",
    }
    client, _ = capture_client(not_found)
    with client:
        resp = client.citations.path("32016R0679", "99999X9999")
    assert resp.found is False
    assert resp.path_length is None
    assert "No citation path" in resp.message


def test_related_and_graceful_empty():
    body = {
        "success": True,
        "celexNumber": "32016R0679",
        "method": "bibliographic-coupling",
        "seedCitationCount": 5,
        "count": 1,
        "related": [
            {
                "celexNumber": "32018R1725",
                "title": "EUDPR",
                "sharedCount": 3,
                "sharedTargets": ["31995L0046"],
            }
        ],
    }
    client, seen = capture_client(body)
    with client:
        resp = client.citations.related("32016R0679", limit=5)

    assert seen["path"] == "/api/v1/citations/related/32016R0679"
    assert seen["params"] == {"limit": "5"}
    assert resp.related[0].shared_count == 3
    assert resp.method == "bibliographic-coupling"

    empty = {
        "success": True,
        "celexNumber": "39999X9999",
        "method": "bibliographic-coupling",
        "count": 0,
        "related": [],
        "message": "Document has no outbound citations to couple on",
    }
    client, _ = capture_client(empty)
    with client:
        resp = client.citations.related("39999X9999")
    assert resp.related == []
    assert "no outbound citations" in resp.message


def test_stats():
    body = {
        "success": True,
        "stats": {
            "totalCitations": 1000,
            "uniqueSourceDocuments": 200,
            "uniqueTargetDocuments": 400,
            "mostCited": [{"celexNumber": "31995L0046", "title": "D", "citedByCount": 90}],
            "mostCiting": [{"celexNumber": "32016R0679", "title": "G", "citesCount": 50}],
        },
    }
    client, seen = capture_client(body)
    with client:
        resp = client.citations.stats()

    assert seen["path"] == "/api/v1/citations/stats"
    assert resp.total_citations == 1000
    assert resp.most_cited[0].cited_by_count == 90
    assert resp.most_citing[0].cites_count == 50


def test_async_citations_parity():
    body = {
        "success": True,
        "targetDocument": "31995L0046",
        "totalCitations": 1,
        "citedBy": [{"celexNumber": "32016R0679", "citationCount": 1, "citations": []}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async def run():
        async with AsyncLexAPI(
            api_key="lex_test_key", transport=httpx.MockTransport(handler)
        ) as client:
            return await client.citations.cited_by("31995L0046")

    resp = asyncio.run(run())
    assert resp.cited_by[0].celex_number == "32016R0679"
