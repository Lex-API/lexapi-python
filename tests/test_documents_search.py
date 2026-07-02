"""Documents + search surface: request shapes, typed results, partial signals."""

import asyncio
import json

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    DocumentBatchResponse,
    InvalidCelexError,
    LexAPI,
    SearchResponse,
)

SEARCH_BODY = {
    "totalResults": 42,
    "totalPages": 5,
    "pagesFetched": 1,
    "resultCount": 2,
    "results": [
        {
            "title": "Regulation (EU) 2016/679 (GDPR)",
            "celexNumber": "32016R0679",
            "celex": "32016R0679",
            "form": "Regulation",
            "documentType": "Regulation",
            "documentTypeCode": "regulation",
            "date": "2016-04-27",
            "dateOfDocumentISO": "2016-04-27",
            "author": "European Parliament and Council",
            "availableLanguages": ["EN", "FR"],
            "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679",
        },
        {"title": "Thin row"},
    ],
    "truncated": True,
    "truncatedAt": 10,
    "truncatedReason": "Tier FREE permits 1 pages; 5 pages exist for this query.",
    "partial": True,
    "partialReason": "Fetch of page 2 failed",
    "postFilteredBy": "author,date",
    "subscription": {"tier": "FREE"},
    "usage": {"current": 3, "limit": 100, "remaining": 97, "unit": "credits"},
    "credits": {"operation_weight": 1, "remaining": 97},
    "fetchedAt": "2026-07-02T10:00:00.000Z",
}

DOCUMENT = {
    "celex": "32016R0679",
    "title": "General Data Protection Regulation",
    "documentType": "Regulation",
    "documentTypeCode": "regulation",
    "author": "European Parliament and Council",
    "dateOfDocument": "27/04/2016",
    "dateOfDocumentISO": "2016-04-27",
    "ecli": None,
    "eli": "data.europa.eu/eli/reg/2016/679",
    "keywords": ["data protection"],
    "subject": ["Data protection"],
    "language": "en",
    "content": {
        "fullText": "Article 1 ...",
        "articles": [
            {
                "id": "art_17",
                "number": "Article 17",
                "title": "Right to erasure",
                "content": "The data subject shall have the right...",
            }
        ],
        "recitals": [{"number": 1, "text": "The protection of natural persons..."}],
        "paragraphs": [],
        "sections": [
            {
                "type": "chapter",
                "number": "I",
                "title": "General provisions",
                "articleRange": ["art_1", "art_4"],
            }
        ],
        "tables": [],
        "annexes": [],
    },
    "urls": {"html": "https://eur-lex.europa.eu/..."},
}


def capture_client(response_json, status=200, headers=None):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json, headers=headers or {})

    client = LexAPI(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


# ── /search ─────────────────────────────────────────────────────────────


def test_search_request_body_uses_spec_field_names():
    client, seen = capture_client(SEARCH_BODY)
    with client:
        client.search(
            "GDPR",
            text_scope="title",
            date_from="2016-01-01",
            date_to="2016-12-31",
            year=2016,
            month=[4, 5],
            document_type=["regulation", "directive"],
            author="commission",
            procedure="ordinary",
            domain="EU_LAW",
            subdomain="LEGISLATION",
            language="en",
            max_pages=2,
        )
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/search"
    assert seen["body"] == {
        "query": "GDPR",
        "textScope": "title",
        "dateFrom": "2016-01-01",
        "dateTo": "2016-12-31",
        "year": 2016,
        "month": [4, 5],
        "documentType": ["regulation", "directive"],
        "author": "commission",
        "procedure": "ordinary",
        "domain": "EU_LAW",
        "subdomain": "LEGISLATION",
        "language": "en",
        "maxPages": 2,
    }


def test_search_omits_none_filters():
    client, seen = capture_client(SEARCH_BODY)
    with client:
        client.search("GDPR")
    assert seen["body"] == {"query": "GDPR"}


def test_search_typed_results_and_truncation_signals():
    client, _ = capture_client(SEARCH_BODY, headers={"X-DocumentType-FanOut": "4"})
    with client:
        resp = client.search("GDPR")

    assert isinstance(resp, SearchResponse)
    assert resp.total_results == 42
    assert resp.result_count == 2
    hit = resp.results[0]
    assert hit.celex == "32016R0679"
    assert hit.document_type_code == "regulation"
    assert hit.available_languages == ["EN", "FR"]
    assert hit.raw["celexNumber"] == "32016R0679"
    # thin rows do not crash the parser
    assert resp.results[1].celex is None
    # truncation / partial signals are surfaced, not hidden
    assert resp.truncated is True
    assert resp.truncated_at == 10
    assert "Tier FREE" in resp.truncated_reason
    assert resp.partial is True
    assert resp.partial_reason == "Fetch of page 2 failed"
    assert resp.post_filtered_by == "author,date"
    assert resp.document_type_fan_out == 4
    # credit visibility still works on typed responses
    assert resp.units_charged == 1
    assert resp.credits_remaining == 97


def test_search_empty_results_hint():
    client, _ = capture_client({"results": [], "resultCount": 0, "hint": "Try semantic search"})
    with client:
        resp = client.search("very verbose natural language query")
    assert resp.results == []
    assert resp.hint == "Try semantic search"
    assert resp.truncated is False
    assert resp.partial is False


# ── /documentContent ────────────────────────────────────────────────────


def test_get_document_request_and_parse():
    client, seen = capture_client(
        {"success": True, "document": DOCUMENT},
        headers={
            "X-Source": "corpus",
            "X-Corpus-Version": "3",
            "X-Corpus-FetchedAt": "2026-06-01T00:00:00.000Z",
            "X-Language": "en",
        },
    )
    with client:
        resp = client.get_document("32016R0679")

    assert seen["path"] == "/api/v1/documentContent"
    assert seen["body"] == {"celexNumber": "32016R0679"}
    doc = resp.document
    assert doc.celex == "32016R0679"
    assert doc.title == "General Data Protection Regulation"
    assert doc.date_of_document_iso == "2016-04-27"
    assert doc.content.articles[0].number == "Article 17"
    assert doc.content.recitals[0].number == 1
    assert doc.content.sections[0].article_range == ["art_1", "art_4"]
    assert resp.source == "corpus"
    assert resp.corpus_version == 3
    assert resp.corpus_fetched_at == "2026-06-01T00:00:00.000Z"
    assert resp.language_served == "en"
    assert resp.language_fallback is None


def test_get_document_payload_control_params():
    client, seen = capture_client({"success": True, "document": DOCUMENT})
    with client:
        client.get_document(
            "32016R0679",
            bypass_corpus=True,
            language="fr",
            include=["metadata", "articles"],
            article_id="17",
        )
    assert seen["body"] == {
        "celexNumber": "32016R0679",
        "bypassCorpus": True,
        "language": "fr",
        "include": ["metadata", "articles"],
        "articleId": "17",
    }


def test_get_document_language_fallback_header():
    client, _ = capture_client(
        {"success": True, "document": DOCUMENT},
        headers={"X-Language": "en", "X-Language-Fallback": "en"},
    )
    with client:
        resp = client.get_document("32016R0679", language="mt")
    assert resp.language_fallback == "en"


def test_get_document_invalid_celex_error():
    client, _ = capture_client(
        {"success": False, "error": {"code": "INVALID_CELEX", "message": "bad celex"}},
        status=400,
    )
    with client:
        with pytest.raises(InvalidCelexError):
            client.get_document("nope")


# ── /documentContent/batch ──────────────────────────────────────────────


def test_batch_request_trimming_and_errors():
    body = {
        "success": True,
        "requested": 3,
        "processed": 2,
        "successful": 1,
        "failed": 1,
        "trimmed": True,
        "trimmedTo": 2,
        "trimmedReason": "Batch size limited to 2 for tier FREE",
        "documents": [DOCUMENT],
        "errors": [
            {"celexNumber": "39999X9999", "error": "TIMEOUT", "message": "upstream timeout"}
        ],
    }
    client, seen = capture_client(body, headers={"X-Warning": "batch clamped to 2"})
    with client:
        resp = client.get_documents_batch(
            ["32016R0679", "39999X9999", "62018CJ0311"], language="en"
        )

    assert seen["path"] == "/api/v1/documentContent/batch"
    assert seen["body"] == {
        "celexNumbers": ["32016R0679", "39999X9999", "62018CJ0311"],
        "language": "en",
    }
    assert isinstance(resp, DocumentBatchResponse)
    assert resp.requested == 3
    assert resp.trimmed is True
    assert resp.trimmed_to == 2
    assert "tier FREE" in resp.trimmed_reason
    assert resp.warning == "batch clamped to 2"
    assert resp.documents[0].celex == "32016R0679"
    err = resp.errors[0]
    assert err.celex_number == "39999X9999"
    assert err.error == "TIMEOUT"


# ── /documents/recent ───────────────────────────────────────────────────


def test_recent_documents_query_params():
    body = {
        "success": True,
        "results": [SEARCH_BODY["results"][0]],
        "resultCount": 1,
        "totalResults": 1,
        "parameters": {"days": 3, "dateFrom": "2026-06-29", "dateTo": "2026-07-02"},
        "languageFilter": {"requested": "EN", "kept": 1, "beforeFilter": 2},
    }
    client, seen = capture_client(body)
    with client:
        resp = client.get_recent_documents(
            days=3, document_type="regulation", author="commission", language="en", limit=10
        )

    assert seen["method"] == "GET"
    assert seen["path"] == "/api/v1/documents/recent"
    assert seen["params"] == {
        "days": "3",
        "documentType": "regulation",
        "author": "commission",
        "language": "en",
        "limit": "10",
    }
    assert resp.results[0].celex == "32016R0679"
    assert resp.parameters["dateFrom"] == "2026-06-29"
    assert resp.language_filter == {"requested": "EN", "kept": 1, "beforeFilter": 2}


# ── /documents/url ──────────────────────────────────────────────────────


def test_get_document_by_url():
    body = {
        "success": True,
        "sourceUrl": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32016R0679",
        "extractedCelex": "32016R0679",
        "document": DOCUMENT,
    }
    client, seen = capture_client(body)
    with client:
        resp = client.get_document_by_url(
            "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32016R0679"
        )

    assert seen["path"] == "/api/v1/documents/url"
    assert seen["body"]["url"].startswith("https://eur-lex.europa.eu/")
    assert resp.extracted_celex == "32016R0679"
    assert resp.source_url.endswith("32016R0679")
    assert resp.document.celex == "32016R0679"


# ── /documents/metadata ─────────────────────────────────────────────────


def test_get_document_metadata():
    metadata = {k: v for k, v in DOCUMENT.items() if k != "content"}
    metadata["celexNumber"] = "32016R0679"
    metadata["urls"] = {"html": "https://...", "pdf": "https://..."}
    client, seen = capture_client(
        {"success": True, "metadata": metadata}, headers={"X-Source": "corpus"}
    )
    with client:
        resp = client.get_document_metadata("32016R0679", bypass_corpus=False)

    assert seen["path"] == "/api/v1/documents/metadata"
    assert seen["body"] == {"celexNumber": "32016R0679", "bypassCorpus": False}
    assert resp.metadata.celex == "32016R0679"
    assert resp.metadata.content is None
    assert "pdf" in resp.metadata.urls
    assert resp.source == "corpus"


# ── /resolve ────────────────────────────────────────────────────────────


def test_resolve_identifier():
    body = {
        "success": True,
        "identifier": "ECLI:EU:C:2020:559",
        "identifierType": "ecli",
        "resolvedVia": "ecli",
        "celex": "62018CJ0311",
        "urls": {"html": "https://...", "metadata": "https://..."},
    }
    client, seen = capture_client(body)
    with client:
        resp = client.resolve("ECLI:EU:C:2020:559")

    assert seen["path"] == "/api/v1/resolve"
    assert seen["body"] == {"identifier": "ECLI:EU:C:2020:559"}
    assert resp.celex == "62018CJ0311"
    assert resp.identifier_type == "ecli"
    assert resp.resolved_via == "ecli"


# ── async parity ────────────────────────────────────────────────────────


def test_async_search_and_get_document():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=SEARCH_BODY)
        return httpx.Response(
            200, json={"success": True, "document": DOCUMENT}, headers={"X-Source": "live"}
        )

    async def run():
        async with AsyncLexAPI(
            api_key="lex_test_key", transport=httpx.MockTransport(handler)
        ) as client:
            search = await client.search("GDPR", max_pages=1)
            doc = await client.get_document("32016R0679")
            return search, doc

    search, doc = asyncio.run(run())
    assert search.results[0].celex == "32016R0679"
    assert search.truncated is True
    assert doc.document.content.articles[0].id == "art_17"
    assert doc.source == "live"
