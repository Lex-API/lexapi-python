"""National-law resource namespace: request shapes, typed parsing, headers, 402."""

import asyncio
import json

import httpx

from lexapi import AsyncLexAPI, LexAPI, NationalAllowanceExhaustedError, NotFoundError


def capture_client(response_json, status=200, headers=None):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json, headers=headers or {})

    client = LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


COVERAGE_BODY = {
    "updatedAt": "2026-10-28T00:00:00.000Z",
    "jurisdictions": [
        {
            "code": "AT",
            "name": "Austria",
            "live": True,
            "launchedAt": "2026-11-03T00:00:00.000Z",
            "legislation": {
                "available": True,
                "description": "Federal law",
                "documents": 12000,
                "from": "1945",
            },
            "caseLaw": {
                "available": True,
                "description": "OGH decisions",
                "documents": 4000,
                "from": "2000",
                "courts": ["OGH"],
            },
            "semanticSearch": {"legislation": True, "caseLaw": True},
            "sources": [
                {
                    "name": "RIS",
                    "url": "https://ris.bka.gv.at",
                    "licence": "CC BY 4.0",
                    "attribution": "Data source: RIS, Bundeskanzleramt Österreich",
                }
            ],
            "lastSyncAt": "2026-10-27T00:00:00.000Z",
        }
    ],
}


def test_coverage():
    client, seen = capture_client(COVERAGE_BODY)
    with client:
        resp = client.national.coverage()

    assert seen["method"] == "GET"
    assert seen["path"] == "/api/v1/national/coverage"
    assert resp.updated_at == "2026-10-28T00:00:00.000Z"
    j = resp.jurisdictions[0]
    assert j.code == "AT"
    assert j.live is True
    assert j.legislation.documents == 12000
    assert j.case_law.courts == ["OGH"]
    assert j.semantic_search.case_law is True
    assert j.sources[0].attribution.startswith("Data source: RIS")


DOCUMENT_BODY = {
    "document": {
        "id": "AT-NOR-12345",
        "jurisdiction": "AT",
        "language": "de",
        "title": "Datenschutzgesetz",
        "documentType": "Federal Act",
        "ecli": None,
        "eli": None,
        "dateOfDocument": "2000-08-17",
        "version": 3,
        "fetchedAt": "2026-10-20T00:00:00.000Z",
        "content": {
            "fullText": "§ 1 ...",
            "articles": [],
            "recitals": [],
            "paragraphs": [],
            "sections": [],
            "tables": [],
            "annexes": [],
        },
        "textAvailability": {"available": True},
        "attribution": "Data source: RIS, Bundeskanzleramt Österreich (data.bka.gv.at), CC BY 4.0.",
    }
}


def test_get_document():
    client, seen = capture_client(DOCUMENT_BODY)
    with client:
        resp = client.national.get_document("at", "AT-NOR-12345", language="de")

    assert seen["method"] == "GET"
    assert seen["path"] == "/api/v1/at/documents/AT-NOR-12345"
    assert seen["params"] == {"language": "de"}
    doc = resp.document
    assert doc.id == "AT-NOR-12345"
    assert doc.jurisdiction == "AT"
    assert doc.content.full_text == "§ 1 ..."
    assert doc.attribution.startswith("Data source: RIS")


def test_get_document_404():
    client, _ = capture_client(
        {
            "error": "Document not found",
            "code": "NOT_FOUND",
            "id": "AT-NOR-999",
            "jurisdiction": "AT",
        },
        status=404,
    )

    with client:
        try:
            client.national.get_document("AT", "AT-NOR-999")
            raise AssertionError("expected NotFoundError")
        except NotFoundError as err:
            assert err.status == 404


LIST_BODY = {
    "jurisdiction": "AT",
    "total": 1,
    "page": 1,
    "limit": 20,
    "includeRepealed": False,
    "results": [
        {
            "id": "AT-NOR-12345",
            "jurisdiction": "AT",
            "language": "de",
            "title": "Datenschutzgesetz",
            "documentType": "Federal Act",
            "version": 3,
            "fetchedAt": "2026-10-20T00:00:00.000Z",
        }
    ],
}


def test_list_documents():
    client, seen = capture_client(LIST_BODY)
    with client:
        resp = client.national.list_documents(
            "AT", q="Datenschutz", document_type="regulation", limit=20, include_repealed=False
        )

    assert seen["path"] == "/api/v1/at/documents"
    assert seen["params"] == {
        "q": "Datenschutz",
        "type": "regulation",
        "limit": "20",
        "includeRepealed": "false",
    }
    assert resp.total == 1
    assert resp.results[0].id == "AT-NOR-12345"
    assert resp.results[0].content is None  # summary rows carry no content


SEMANTIC_CASE_LAW_BODY = {
    "success": True,
    "searchType": "semantic-case-law",
    "jurisdiction": "AT",
    "query": "Datenschutz",
    "resultCount": 1,
    "results": [
        {
            "score": 0.91,
            "snippet": "...",
            "case_name": "X v Y",
            "ecli": "ECLI:AT:OGH:2020:RS0123",
            "court": "OGH",
            "documentId": "AT-JUD-OGH-1",
            "jurisdiction": "AT",
            "metadata": {"celex_id": "AT-JUD-OGH-1"},
            "validTo": "2024-01-01",
            "inForce": False,
            "attribution": "Data source: RIS",
        }
    ],
    "attribution": "Data source: RIS",
    "tookMs": 120,
    "subscription": {"tier": "PROFESSIONAL", "maxResults": 50},
}

NATIONAL_ALLOWANCE_HEADERS = {
    "x-national-limit": "2000",
    "x-national-remaining": "1999",
    "x-national-reset": "2026-12-01T00:00:00.000Z",
}


def test_national_semantic_search():
    client, seen = capture_client(SEMANTIC_CASE_LAW_BODY, headers=NATIONAL_ALLOWANCE_HEADERS)
    with client:
        resp = client.national.semantic_search(
            "AT", "Datenschutz", hyde=True, filters={"court": "OGH"}
        )

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/at/search/semantic"
    assert seen["body"] == {"query": "Datenschutz", "filters": {"court": "OGH"}, "hyde": True}
    assert resp.jurisdiction == "AT"
    assert resp.attribution == "Data source: RIS"
    r = resp.results[0]
    assert r.document_id == "AT-JUD-OGH-1"
    assert r.valid_to == "2024-01-01"
    assert r.in_force is False
    assert r.attribution == "Data source: RIS"
    assert resp.national_allowance_limit == 2000
    assert resp.national_allowance_remaining == 1999
    assert resp.national_allowance_resets_at == "2026-12-01T00:00:00.000Z"


SEMANTIC_LEGISLATION_BODY = {
    "success": True,
    "searchType": "semantic-legislation",
    "jurisdiction": "AT",
    "query": "right to erasure",
    "resultCount": 1,
    "results": [
        {
            "score": 0.88,
            "snippet": "...",
            "article_ref": "§ 1",
            "law_id": "AT-NOR-12345",
            "law_title": "Datenschutzgesetz",
            "documentId": "AT-NOR-12345",
            "jurisdiction": "AT",
            "metadata": {},
            "in_force": False,
            "valid_to": "2023-05-01",
            "attribution": "Data source: RIS",
        }
    ],
    "attribution": "Data source: RIS",
}


def test_national_legislation_semantic_search():
    client, seen = capture_client(SEMANTIC_LEGISLATION_BODY)
    with client:
        resp = client.national.legislation_semantic_search("AT", "right to erasure")

    assert seen["path"] == "/api/v1/at/legislation/semantic"
    assert seen["body"] == {"query": "right to erasure"}
    r = resp.results[0]
    assert r.law_id == "AT-NOR-12345"
    assert r.document_id == "AT-NOR-12345"
    assert r.in_force is False
    assert r.valid_to == "2023-05-01"
    assert r.attribution == "Data source: RIS"


def test_national_allowance_exhausted_error():
    client, _ = capture_client(
        {
            "success": False,
            "error": {
                "code": "NATIONAL_ALLOWANCE_EXHAUSTED",
                "message": "This month's national-law allowance of 30 calls is used up.",
                "details": {
                    "jurisdiction": "AT",
                    "limit": 30,
                    "remaining": 0,
                    "resetsAt": "2026-12-01T00:00:00.000Z",
                    "upgradeUrl": "/pricing",
                },
            },
        },
        status=402,
        headers={
            "x-national-limit": "30",
            "x-national-remaining": "0",
            "x-national-reset": "2026-12-01T00:00:00.000Z",
        },
    )

    with client:
        try:
            client.national.get_document("AT", "AT-NOR-1")
            raise AssertionError("expected NationalAllowanceExhaustedError")
        except NationalAllowanceExhaustedError as err:
            assert err.status == 402
            assert err.jurisdiction == "AT"
            assert err.limit == 30
            assert err.remaining == 0
            assert err.resets_at == "2026-12-01T00:00:00.000Z"
            assert err.upgrade_url == "/pricing"


def make_async_client(handler, **kwargs) -> AsyncLexAPI:
    return AsyncLexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler), **kwargs)


def test_async_national_coverage_and_document():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/national/coverage"):
            return httpx.Response(200, json=COVERAGE_BODY)
        return httpx.Response(200, json=DOCUMENT_BODY)

    async def run():
        async with make_async_client(handler) as client:
            coverage = await client.national.coverage()
            doc = await client.national.get_document("AT", "AT-NOR-12345")
            return coverage, doc

    coverage, doc = asyncio.run(run())
    assert coverage.jurisdictions[0].code == "AT"
    assert doc.document.id == "AT-NOR-12345"


def test_async_national_semantic_search():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SEMANTIC_CASE_LAW_BODY)

    async def run():
        async with make_async_client(handler) as client:
            return await client.national.semantic_search("AT", "Datenschutz")

    resp = asyncio.run(run())
    assert resp.results[0].document_id == "AT-JUD-OGH-1"
