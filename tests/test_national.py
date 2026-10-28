"""National-law resource namespace: request shapes, typed parsing, headers, 402."""

import asyncio
import json

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    DocumentWithdrawnError,
    LexAPI,
    NationalAllowanceExhaustedError,
    NationalSemanticNotEnabledError,
    NationalSemanticUnavailableError,
    NotFoundError,
    ServiceUnavailableError,
)


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
                # legislation.from is currently always null server-side —
                # see nationalCoverageController.js liveJurisdiction().
                "from": None,
            },
            "caseLaw": {
                "available": True,
                "description": "OGH decisions",
                "documents": 4000,
                # caseLaw.from IS populated: an int year, not a string.
                "from": 2000,
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
        },
        {
            # darkJurisdiction() shape: registered but not live — nothing
            # beyond name/code is populated.
            "code": "DE",
            "name": "Germany",
            "live": False,
            "launchedAt": None,
            "legislation": {"available": False, "description": "", "documents": None, "from": None},
            "caseLaw": {
                "available": False,
                "description": "",
                "documents": None,
                "from": None,
                "courts": [],
            },
            "semanticSearch": {"legislation": False, "caseLaw": False},
            "sources": [],
            "lastSyncAt": None,
        },
    ],
}


def test_coverage():
    client, seen = capture_client(COVERAGE_BODY)
    with client:
        resp = client.national.coverage()

    assert seen["method"] == "GET"
    assert seen["path"] == "/api/v1/national/coverage"
    assert resp.updated_at == "2026-10-28T00:00:00.000Z"
    at, de = resp.jurisdictions
    assert at.code == "AT"
    assert at.live is True
    assert at.legislation.documents == 12000
    assert at.legislation.from_ is None
    assert at.case_law.from_ == 2000
    assert at.case_law.courts == ["OGH"]
    assert at.semantic_search.case_law is True
    assert at.sources[0].attribution.startswith("Data source: RIS")

    assert de.code == "DE"
    assert de.live is False
    assert de.legislation.available is False
    assert de.legislation.documents is None
    assert de.legislation.description == ""  # always a string, never None
    assert de.case_law.from_ is None
    assert de.sources == []


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
    # Typed envelope since lex-api commit 6ec7440 (part of #314's stack):
    # the national 404 used to be the legacy bare shape, now it matches the
    # EU document routes.
    client, _ = capture_client(
        {
            "success": False,
            "error": {
                "code": "NOT_FOUND",
                "message": "No AT document found with id AT-NOR-999",
                "details": {"id": "AT-NOR-999", "jurisdiction": "AT"},
            },
        },
        status=404,
    )

    with client:
        try:
            client.national.get_document("AT", "AT-NOR-999")
            raise AssertionError("expected NotFoundError")
        except NotFoundError as err:
            assert err.status == 404
            assert err.details == {"id": "AT-NOR-999", "jurisdiction": "AT"}


LIST_BODY = {
    "jurisdiction": "AT",
    "total": 1,
    "totalExact": True,
    "page": 1,
    "limit": 20,
    "includeRepealed": False,
    "hasMore": False,
    "nextCursor": None,
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
    assert resp.total_exact is True
    assert resp.has_more is False
    assert resp.next_cursor is None
    assert resp.results[0].id == "AT-NOR-12345"
    assert resp.results[0].content is None  # summary rows carry no content


def test_list_documents_cursor_paging():
    page1 = {
        "jurisdiction": "AT",
        "total": 3,
        "totalExact": True,
        "limit": 2,
        "includeRepealed": False,
        "hasMore": True,
        "nextCursor": "c1",
        "results": [
            {
                "id": "AT-NOR-1",
                "jurisdiction": "AT",
                "language": "de",
                "version": 1,
                "fetchedAt": "2026-10-01T00:00:00.000Z",
            }
        ],
    }
    page2 = {
        "jurisdiction": "AT",
        "total": 3,
        "totalExact": True,
        "limit": 2,
        "includeRepealed": False,
        "hasMore": False,
        "nextCursor": None,
        "results": [
            {
                "id": "AT-NOR-2",
                "jurisdiction": "AT",
                "language": "de",
                "version": 1,
                "fetchedAt": "2026-10-02T00:00:00.000Z",
            }
        ],
    }
    calls = {"n": 0, "params": []}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["params"].append(dict(request.url.params))
        calls["n"] += 1
        return httpx.Response(200, json=page1 if calls["n"] == 1 else page2)

    client = LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    with client:
        seen = [p.results[0].id for p in client.national.iter_documents("AT", limit=2)]

    assert seen == ["AT-NOR-1", "AT-NOR-2"]
    assert calls["n"] == 2
    assert "cursor" not in calls["params"][0]
    assert calls["params"][1]["cursor"] == "c1"


def test_async_iter_documents_cursor_paging():
    page1 = {
        "jurisdiction": "AT",
        "total": 3,
        "totalExact": True,
        "limit": 2,
        "includeRepealed": False,
        "hasMore": True,
        "nextCursor": "c1",
        "results": [
            {
                "id": "AT-NOR-1",
                "jurisdiction": "AT",
                "language": "de",
                "version": 1,
                "fetchedAt": "2026-10-01T00:00:00.000Z",
            }
        ],
    }
    page2 = {
        "jurisdiction": "AT",
        "total": 3,
        "totalExact": True,
        "limit": 2,
        "includeRepealed": False,
        "hasMore": False,
        "nextCursor": None,
        "results": [
            {
                "id": "AT-NOR-2",
                "jurisdiction": "AT",
                "language": "de",
                "version": 1,
                "fetchedAt": "2026-10-02T00:00:00.000Z",
            }
        ],
    }
    calls = {"n": 0, "params": []}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["params"].append(dict(request.url.params))
        calls["n"] += 1
        return httpx.Response(200, json=page1 if calls["n"] == 1 else page2)

    async def run():
        async with make_async_client(handler) as client:
            return [p.results[0].id async for p in client.national.iter_documents("AT", limit=2)]

    seen = asyncio.run(run())
    assert seen == ["AT-NOR-1", "AT-NOR-2"]
    assert calls["n"] == 2
    assert "cursor" not in calls["params"][0]
    assert calls["params"][1]["cursor"] == "c1"


def test_list_documents_sends_cursor_not_page():
    client, seen = capture_client(
        {
            "jurisdiction": "AT",
            "total": 1200,
            "totalExact": False,
            "limit": 20,
            "includeRepealed": False,
            "hasMore": True,
            "nextCursor": "eyJhIjoxfQ",
            "results": [],
        }
    )
    with client:
        resp = client.national.list_documents("AT", cursor="abc123")
    assert seen["params"]["cursor"] == "abc123"
    assert "page" not in seen["params"]
    assert resp.page is None
    assert resp.has_more is True


def test_list_documents_page_past_1000_rows_is_invalid_params():
    from lexapi import InvalidParamsError

    client, _ = capture_client(
        {
            "success": False,
            "error": {
                "code": "INVALID_PARAMS",
                "message": (
                    "page reaches past the first 1000 results — page further "
                    "with cursor (each page's nextCursor)"
                ),
            },
        },
        status=400,
    )
    with client, pytest.raises(InvalidParamsError):
        client.national.list_documents("AT", page=51, limit=20)


def test_list_documents_slow_page_is_retry_safe_timeout():
    from lexapi import TimeoutError as LexAPITimeoutError

    client, _ = capture_client(
        {
            "success": False,
            "error": {
                "code": "TIMEOUT",
                "message": "The listing took too long. Narrow it (q, type) and retry.",
            },
        },
        status=504,
    )
    with client, pytest.raises(LexAPITimeoutError) as exc_info:
        client.national.list_documents("AT", q="xyz")
    assert exc_info.value.status == 504


def test_document_withdrawn_error_typed_envelope():
    client, _ = capture_client(
        {
            "success": False,
            "error": {
                "code": "DOCUMENT_WITHDRAWN",
                "message": (
                    "AT document AT-NOR-5 was withdrawn by its source and is no longer served"
                ),
                "details": {
                    "id": "AT-NOR-5",
                    "jurisdiction": "AT",
                    "withdrawnAt": "2026-10-20T00:00:00.000Z",
                },
            },
        },
        status=410,
    )
    with client, pytest.raises(DocumentWithdrawnError) as exc_info:
        client.national.get_document("AT", "AT-NOR-5")
    err = exc_info.value
    assert err.status == 410
    assert err.id == "AT-NOR-5"
    assert err.jurisdiction == "AT"
    assert err.withdrawn_at == "2026-10-20T00:00:00.000Z"


def test_document_withdrawn_error_legacy_shape_on_citations():
    body = {
        "success": False,
        "code": "DOCUMENT_WITHDRAWN",
        "error": "Document withdrawn: AT-NOR-5",
        "message": "AT document AT-NOR-5 was withdrawn by its source and is no longer served.",
        "id": "AT-NOR-5",
        "jurisdiction": "AT",
        "withdrawnAt": "2026-10-20T00:00:00.000Z",
    }
    client, _ = capture_client(body, status=410)
    with client, pytest.raises(DocumentWithdrawnError) as exc_info:
        client.citations.cites("AT-NOR-5")
    err = exc_info.value
    assert err.id == "AT-NOR-5"
    assert err.jurisdiction == "AT"
    assert err.withdrawn_at == "2026-10-20T00:00:00.000Z"


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


def test_national_allowance_exhausted_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            402,
            json={
                "success": False,
                "error": {
                    "code": "NATIONAL_ALLOWANCE_EXHAUSTED",
                    "message": "used up",
                    "details": {"jurisdiction": "AT", "limit": 30, "remaining": 0},
                },
            },
        )

    client = LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    with client:
        try:
            client.national.get_document("AT", "AT-NOR-1")
            raise AssertionError("expected NationalAllowanceExhaustedError")
        except NationalAllowanceExhaustedError:
            pass

    assert calls["n"] == 1


def test_national_semantic_not_enabled_409():
    client, _ = capture_client(
        {
            "success": False,
            "code": "NATIONAL_SEMANTIC_NOT_ENABLED",
            "error": "National semantic search not enabled",
            "message": "Semantic search over Austria (AT) is not enabled yet. No credits charged.",
            "jurisdiction": "AT",
        },
        status=409,
    )

    with client:
        try:
            client.national.semantic_search("AT", "Datenschutz")
            raise AssertionError("expected NationalSemanticNotEnabledError")
        except NationalSemanticNotEnabledError as err:
            assert err.status == 409
            assert err.jurisdiction == "AT"


def test_national_semantic_unavailable_503():
    client, _ = capture_client(
        {
            "success": False,
            "code": "NATIONAL_SEMANTIC_UNAVAILABLE",
            "error": "National semantic index unavailable",
            "message": "The Austria (AT) semantic index is temporarily unavailable.",
            "jurisdiction": "AT",
        },
        status=503,
    )

    with client:
        try:
            client.national.legislation_semantic_search("AT", "right to erasure")
            raise AssertionError("expected NationalSemanticUnavailableError")
        except NationalSemanticUnavailableError as err:
            assert err.status == 503
            assert err.jurisdiction == "AT"
            assert isinstance(err, ServiceUnavailableError)


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
