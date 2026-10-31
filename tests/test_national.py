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


# ── API 2.8.0 fixtures (one national shape for every
# jurisdiction — every documented field present, null / [] when the source
# has nothing; source extras only under content.details / articles[].details).


def summary(**overrides):
    row = {
        "id": "AT-NOR-12345",
        "jurisdiction": "AT",
        "language": "de",
        "documentKind": "act",
        "title": "Datenschutzgesetz",
        "documentType": "Bundesgesetz (konsolidiert)",
        "ecli": None,
        "eli": "https://www.ris.bka.gv.at/eli/bgbl/i/1999/165",
        "dateOfDocument": None,
        "version": 3,
        "fetchedAt": "2026-10-20T00:00:00.000Z",
        "validTo": None,
        "inForce": None,
    }
    row.update(overrides)
    return row


SUMMARY_KEYS = sorted(summary())
CONTENT_KEYS = sorted(
    [
        "fullText",
        "articles",
        "textLanguages",
        "textLanguage",
        "embeddedForeignText",
        "repeal",
        "source",
        "details",
    ]
)


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
        **summary(),
        "decision": None,
        "content": {
            "fullText": "§ 1 ...",
            "articles": [
                {
                    "number": "§ 1",
                    "title": "Grundrecht auf Datenschutz",
                    "section": None,
                    "text": "(1) Jedermann hat ...",
                    "inForceFrom": "2018-05-25",
                    "inForceTo": None,
                    "details": {"norId": "NOR40198753"},
                },
                {
                    "number": "Anl. 1",
                    "title": None,
                    "section": None,
                    "text": None,
                    "inForceFrom": None,
                    "inForceTo": None,
                    "details": {"noHtmlRendition": True},
                },
            ],
            "textLanguages": ["de"],
            "textLanguage": None,
            "embeddedForeignText": False,
            "repeal": None,
            "source": {"system": "RIS", "gesetzesnummer": "10001597"},
            "details": {"kundmachungsorgan": "BGBl. I Nr. 165/1999"},
        },
        "textAvailability": {"available": True},
        "attribution": "Data source: RIS, Bundeskanzleramt Österreich (data.bka.gv.at), CC BY 4.0.",
        "textBasis": None,
    }
}

DECISION_BODY = {
    "document": {
        **summary(
            id="DE-JUD-BFH-KBRE202550123",
            jurisdiction="DE",
            documentKind="decision",
            title="Kindergeld: Berücksichtigung eines volljährigen Kindes",
            documentType="Urteil",
            ecli="ECLI:DE:BFH:2025:U.120625.IIIR27.24.0",
            eli=None,
            dateOfDocument="2025-06-12",
            version=1,
        ),
        "decision": {
            "court": "BFH 3. Senat",
            "chamber": None,
            "caseNumbers": ["III R 27/24"],
            "citedNorms": [{"text": "§ 62 Abs 1a S 3 EStG 2009", "documentId": None}],
        },
        "content": {
            "fullText": "Tenor ... Tatbestand ... Entscheidungsgründe ...",
            "articles": [],
            "textLanguages": ["de"],
            "textLanguage": None,
            "embeddedForeignText": False,
            "repeal": None,
            "source": {"system": "rechtsprechung-im-internet.de"},
            # The source's own field names stay here, unchanged.
            "details": {"gericht": "BFH 3. Senat", "aktenzeichen": "III R 27/24"},
        },
        "textAvailability": {"available": True},
        "attribution": "Source: rechtsprechung-im-internet.de (BMJ).",
        "textBasis": None,
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
    assert sorted(seen_keys(DOCUMENT_BODY)) == sorted(SUMMARY_KEYS)


def seen_keys(body):
    """The summary keys of a document body (fixture sanity: every summary field present)."""
    return [k for k in body["document"] if k in SUMMARY_KEYS]


def test_get_document_act_shape():
    client, _ = capture_client(DOCUMENT_BODY)
    with client:
        doc = client.national.get_document("AT", "AT-NOR-12345").document

    assert doc.document_kind == "act"
    assert doc.is_act and not doc.is_decision
    assert doc.decision is None
    assert doc.text_basis is None
    assert doc.ecli is None and doc.in_force is None and doc.valid_to is None
    assert sorted(doc.content.raw) == CONTENT_KEYS
    first, annex = doc.content.articles
    assert first.number == "§ 1"
    assert first.title == "Grundrecht auf Datenschutz"
    assert first.in_force_from == "2018-05-25"
    assert first.details == {"norId": "NOR40198753"}
    # A unit the source publishes no text for: None, never "".
    assert annex.text is None and annex.number == "Anl. 1"
    assert doc.content.text_languages == ["de"]
    assert doc.content.text_language is None
    assert doc.content.embedded_foreign_text is False
    assert doc.content.repeal is None
    assert doc.content.source["system"] == "RIS"
    assert doc.content.details == {"kundmachungsorgan": "BGBl. I Nr. 165/1999"}


def test_get_document_decision_shape():
    client, _ = capture_client(DECISION_BODY)
    with client:
        doc = client.national.get_document("DE", "DE-JUD-BFH-KBRE202550123").document

    assert doc.is_decision
    assert doc.decision.court == "BFH 3. Senat"
    assert doc.decision.chamber is None
    assert doc.decision.case_numbers == ["III R 27/24"]
    assert doc.decision.cited_norms[0].text == "§ 62 Abs 1a S 3 EStG 2009"
    assert doc.decision.cited_norms[0].document_id is None
    assert doc.content.articles == []
    assert doc.date_of_document == "2025-06-12"


def test_decision_without_text_keeps_decision_block():
    body = {
        "document": {
            **DECISION_BODY["document"],
            "content": None,
            "textAvailability": {"available": False, "reason": "unknown"},
        }
    }
    client, _ = capture_client(body)
    with client:
        doc = client.national.get_document("DE", "DE-JUD-BFH-KBRE202550123").document

    assert doc.content is None
    assert doc.text_availability.available is False
    assert doc.decision.case_numbers == ["III R 27/24"]


def test_repeal_and_validity():
    body = {
        "document": {
            **DOCUMENT_BODY["document"],
            "validTo": "2024-12-31",
            "inForce": False,
            "content": {
                **DOCUMENT_BODY["document"]["content"],
                "repeal": {
                    "validTo": "2024-12-31",
                    "basis": "aufgehoben durch BGBl. I Nr. 1/2025",
                    "detectedAt": "2025-01-03T00:00:00.000Z",
                },
            },
        }
    }
    client, _ = capture_client(body)
    with client:
        doc = client.national.get_document("AT", "AT-NOR-12345").document

    assert doc.valid_to == "2024-12-31"
    assert doc.in_force is False
    assert doc.content.repeal.valid_to == "2024-12-31"
    assert doc.content.repeal.basis.startswith("aufgehoben")


def test_get_document_404():
    # The national 404 is the typed envelope, matching the EU document
    # routes (not the legacy bare shape).
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
        summary(),
        summary(
            id="AT-JUD-JJT_20250115_OGH0002_0060OB00123_24X0000_000",
            documentKind="decision",
            title=None,
            documentType="Entscheidungstext (Justiz)",
            ecli="ECLI:AT:OGH0002:2025:0060OB00123.24X.0115.000",
            eli=None,
            dateOfDocument="2025-01-15",
            version=1,
        ),
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
    assert resp.results[0].decision is None
    assert resp.results[0].document_kind == "act"
    assert resp.results[1].is_decision
    assert resp.results[1].title is None
    assert resp.results[1].date_of_document == "2025-01-15"
    assert all(sorted(row) == SUMMARY_KEYS for row in LIST_BODY["results"])


def test_list_documents_cursor_paging():
    page1 = {
        "jurisdiction": "AT",
        "total": 3,
        "totalExact": True,
        "limit": 2,
        "includeRepealed": False,
        "hasMore": True,
        "nextCursor": "c1",
        "results": [summary(id="AT-NOR-1", version=1)],
    }
    page2 = {
        "jurisdiction": "AT",
        "total": 3,
        "totalExact": True,
        "limit": 2,
        "includeRepealed": False,
        "hasMore": False,
        "nextCursor": None,
        "results": [summary(id="AT-NOR-2", version=1)],
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
        "results": [summary(id="AT-NOR-1", version=1)],
    }
    page2 = {
        "jurisdiction": "AT",
        "total": 3,
        "totalExact": True,
        "limit": 2,
        "includeRepealed": False,
        "hasMore": False,
        "nextCursor": None,
        "results": [summary(id="AT-NOR-2", version=1)],
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
            "document": summary(
                id="AT-JUD-OGH-1",
                documentKind="decision",
                title=None,
                documentType="Entscheidungstext (Justiz)",
                ecli="ECLI:AT:OGH:2020:RS0123",
                eli=None,
                dateOfDocument="2020-03-04",
                validTo="2024-01-01",
                inForce=False,
            ),
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
    # API 2.8.0: the listing-shaped `document` summary on every hit.
    assert r.document.id == "AT-JUD-OGH-1"
    assert r.document.is_decision
    assert r.document.ecli == "ECLI:AT:OGH:2020:RS0123"
    assert r.document.date_of_document == "2020-03-04"
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
            # API 2.8.0: always present, null when the index has nothing.
            "section": None,
            "language": "de",
            "in_force": False,
            "valid_from": None,
            "valid_to": "2023-05-01",
            "law_type": None,
            "documentId": "AT-NOR-12345",
            "jurisdiction": "AT",
            "metadata": {},
            "document": summary(validTo="2023-05-01", inForce=False),
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
    assert r.jurisdiction == "AT"
    assert r.section is None and r.valid_from is None and r.law_type is None
    assert r.language == "de"
    assert r.document.document_kind == "act"
    assert r.document.valid_to == "2023-05-01"


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


# ── Full-field round trip: every key of the API 2.8.0 shape's
# NationalDocument / NationalDecision / NationalCitedNorm /
# NationalDocumentContent / NationalArticle / textBasis / repeal populated
# with a non-default value, and each must land on its snake_case attribute.

_SPEC_DOCUMENT_KEYS = {
    "id": "id",
    "jurisdiction": "jurisdiction",
    "language": "language",
    "documentKind": "document_kind",
    "title": "title",
    "documentType": "document_type",
    "ecli": "ecli",
    "eli": "eli",
    "dateOfDocument": "date_of_document",
    "version": "version",
    "fetchedAt": "fetched_at",
    "validTo": "valid_to",
    "inForce": "in_force",
    "decision": "decision",
    "content": "content",
    "textAvailability": "text_availability",
    "attribution": "attribution",
    "textBasis": "text_basis",
}


def test_national_document_full_field_round_trip() -> None:
    from lexapi import NationalDocumentResponse

    payload = {
        "document": {
            "id": "PL-DU-2020-1",
            "jurisdiction": "PL",
            "language": "pl",
            "documentKind": "decision",
            "title": "T",
            "documentType": "Wyrok",
            "ecli": "ECLI:PL:X",
            "eli": "https://eli/1",
            "dateOfDocument": "2025-06-12",
            "version": 4,
            "fetchedAt": "2026-11-02T22:10:00.000Z",
            "validTo": "2027-01-01",
            "inForce": True,
            "decision": {
                "court": "Sąd Najwyższy",
                "chamber": "Izba Cywilna",
                "caseNumbers": ["III CZP 1/25", "III CZP 2/25"],
                "citedNorms": [
                    {"text": "art. 415 k.c.", "documentId": "PL-DU-1964-93"},
                    {"text": "art. 5 k.c.", "documentId": None},
                ],
            },
            "content": {
                "fullText": "ft",
                "articles": [
                    {
                        "number": "Art. 1",
                        "title": "t",
                        "section": "Dział I",
                        "text": None,
                        "inForceFrom": "2018-05-25",
                        "inForceTo": "2020-01-01",
                        "details": {"unitId": "u1"},
                    }
                ],
                "textLanguages": ["en", "pl"],
                "textLanguage": "en",
                "embeddedForeignText": True,
                "repeal": {
                    "validTo": "2027-01-01",
                    "basis": "uchylony",
                    "detectedAt": "2026-10-01T00:00:00Z",
                },
                "source": {"system": "ISAP"},
                "details": {"sygnatura": "III CZP 1/25"},
            },
            "textAvailability": {"available": True},
            "attribution": "Source: ISAP.",
            "textBasis": {
                "basis": "official_consolidated",
                "asOf": "2024-01-01",
                "newerConsolidationExists": True,
                "newestPdfOnlyDate": "2025-01-01",
                "newerPdfOnly": ["2025-01-01"],
                "amendedSince": False,
                "textFrom": "PL-DU-2024-5",
            },
        }
    }
    doc = NationalDocumentResponse(payload).document
    for wire, attr in _SPEC_DOCUMENT_KEYS.items():
        assert hasattr(doc, attr), f"{wire} -> {attr} missing"
    assert (doc.id, doc.jurisdiction, doc.language, doc.document_kind, doc.title) == (
        "PL-DU-2020-1",
        "PL",
        "pl",
        "decision",
        "T",
    )
    assert (doc.document_type, doc.ecli, doc.eli, doc.date_of_document, doc.version) == (
        "Wyrok",
        "ECLI:PL:X",
        "https://eli/1",
        "2025-06-12",
        4,
    )
    assert (doc.fetched_at, doc.valid_to, doc.in_force) == (
        "2026-11-02T22:10:00.000Z",
        "2027-01-01",
        True,
    )
    assert doc.is_decision and not doc.is_act
    d = doc.decision
    assert d is not None
    assert (d.court, d.chamber, d.case_numbers) == (
        "Sąd Najwyższy",
        "Izba Cywilna",
        ["III CZP 1/25", "III CZP 2/25"],
    )
    assert [(n.text, n.document_id) for n in d.cited_norms] == [
        ("art. 415 k.c.", "PL-DU-1964-93"),
        ("art. 5 k.c.", None),
    ]
    c = doc.content
    assert c is not None
    a = c.articles[0]
    assert (a.number, a.title, a.section, a.text, a.in_force_from, a.in_force_to, a.details) == (
        "Art. 1",
        "t",
        "Dział I",
        None,
        "2018-05-25",
        "2020-01-01",
        {"unitId": "u1"},
    )
    assert (c.full_text, c.text_languages, c.text_language, c.embedded_foreign_text) == (
        "ft",
        ["en", "pl"],
        "en",
        True,
    )
    assert c.repeal is not None
    assert (c.repeal.valid_to, c.repeal.basis, c.repeal.detected_at) == (
        "2027-01-01",
        "uchylony",
        "2026-10-01T00:00:00Z",
    )
    assert c.source == {"system": "ISAP"} and c.details == {"sygnatura": "III CZP 1/25"}
    assert doc.text_availability is not None and doc.text_availability.available is True
    assert doc.attribution == "Source: ISAP."
    tb = doc.text_basis
    assert tb is not None
    assert (tb.basis, tb.as_of, tb.newer_consolidation_exists, tb.newest_pdf_only_date) == (
        "official_consolidated",
        "2024-01-01",
        True,
        "2025-01-01",
    )
    assert (tb.newer_pdf_only, tb.amended_since, tb.text_from) == (
        ["2025-01-01"],
        False,
        "PL-DU-2024-5",
    )
