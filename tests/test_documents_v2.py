"""Documents / search / info surface added in 0.2.0: textAvailability,
titleLanguage, acceptLanguages, search sources + cache, recent-documents
sources, typed /info, rate-limit headers, safe path segments."""

import asyncio
import datetime as dt
import json

import httpx

from lexapi import (
    AsyncLexAPI,
    InfoResponse,
    LexAPI,
)


def capture_client(response_json, status=200, headers=None, client_cls=LexAPI):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["raw_path"] = request.url.raw_path.decode()
        seen["params"] = request.url.params
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json, headers=headers or {})

    client = client_cls(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


TEXT_UNAVAILABLE_DOC = {
    "success": True,
    "document": {
        "celex": "32008R1272",
        "title": "Regulation (EC) No 1272/2008 ...",
        "documentType": "Regulation",
        "documentTypeCode": "regulation",
        "language": "en",
        "content": None,
        "textAvailability": {
            "available": False,
            "reason": "too_large",
            "retryable": False,
            "availableLanguages": None,
            "source": "parse-worker",
            "checkedAt": "2026-10-20T09:12:00.000Z",
            "latestConsolidated": {
                "celex": "02008R1272-20260701",
                "date": "2026-07-01",
                "source": "cellar",
            },
            "upcomingConsolidated": {
                "celex": "02008R1272-20270101",
                "date": "2027-01-01",
                "source": "cellar",
            },
        },
        "urls": {},
        "fetchedAt": "2026-10-20T09:12:00.000Z",
    },
    "credits": {"operation_weight": 1, "remaining": 499},
}


def test_text_unavailable_document_is_typed():
    client, _ = capture_client(
        TEXT_UNAVAILABLE_DOC, headers={"X-Text-Available": "false", "X-Source": "corpus"}
    )
    with client:
        resp = client.get_document("32008R1272")
    doc = resp.document
    assert doc.content is None
    assert resp.text_available is False
    assert doc.text_available is False
    ta = doc.text_availability
    assert ta.reason == "too_large"
    assert ta.retryable is False
    assert ta.available_languages is None  # unknown, distinct from []
    assert ta.source == "parse-worker"
    assert ta.latest_consolidated.celex == "02008R1272-20260701"
    assert ta.latest_consolidated.date == "2026-07-01"
    assert ta.latest_consolidated.source == "cellar"
    assert ta.upcoming_consolidated.celex == "02008R1272-20270101"
    assert ta.newest_consolidated is None
    assert doc.fetched_at == "2026-10-20T09:12:00.000Z"
    assert resp.units_charged == 1


def test_not_in_language_keeps_empty_vs_null_languages():
    body = {
        "success": True,
        "document": {
            "celex": "62019TJ0001",
            "content": None,
            "textAvailability": {
                "available": False,
                "reason": "not_in_language",
                "retryable": True,
                "availableLanguages": ["fr", "de"],
                "newestConsolidated": {
                    "celex": "02024R0370-20240423",
                    "date": "2024-04-23",
                    "source": "cellar",
                    "availableLanguages": ["et"],
                },
            },
        },
    }
    client, _ = capture_client(body)
    with client:
        ta = client.get_document("62019TJ0001").document.text_availability
    assert ta.available_languages == ["fr", "de"]
    assert ta.newest_consolidated.available_languages == ["et"]

    body["document"]["textAvailability"]["availableLanguages"] = []
    client, _ = capture_client(body)
    with client:
        ta = client.get_document("62019TJ0001").document.text_availability
    assert ta.available_languages == []


def test_text_available_header_true_and_absent():
    body = {
        "success": True,
        "document": {"celex": "32016R0679", "textAvailability": {"available": True}},
    }
    client, _ = capture_client(body)
    with client:
        resp = client.get_document("32016R0679")
    assert resp.text_available is True  # from the body block when no header
    assert resp.document.text_availability.reason is None


def test_accept_languages_and_title_language():
    body = {
        "success": True,
        "document": {
            "celex": "62019TJ0001",
            "language": "fr",
            "languageRequested": "en",
            "languageFallback": "fr",
            "title": "Arrêt du Tribunal",
            "titleLanguage": "fr",
            "textAvailability": {"available": True},
        },
    }
    client, seen = capture_client(body, headers={"X-Language": "fr", "X-Language-Fallback": "fr"})
    with client:
        resp = client.get_document("62019TJ0001", accept_languages=["fr", "de"])
    assert seen["body"] == {"celexNumber": "62019TJ0001", "acceptLanguages": ["fr", "de"]}
    assert resp.document.title_language == "fr"
    assert resp.document.language_fallback == "fr"
    assert resp.language_fallback == "fr"
    assert resp.language_served == "fr"

    client, seen = capture_client(body)
    with client:
        client.get_document("62019TJ0001", accept_languages="any")
    assert seen["body"]["acceptLanguages"] == "any"


def test_cellar_sources_documented_on_source():
    client, _ = capture_client({"success": True, "document": {}}, headers={"X-Source": "cellar-oj"})
    with client:
        assert client.get_document("92012E000001").source == "cellar-oj"


def test_batch_accept_languages_counts_and_partition_headers():
    body = {
        "success": True,
        "requested": 4,
        "processed": 4,
        "successful": 4,
        "failed": 0,
        "textUnavailable": 1,
        "documents": [
            {"celex": "32016R0679", "titleLanguage": "en"},
            {"celex": "32008R1272", "content": None, "textAvailability": {"available": False}},
        ],
        "errors": [],
    }
    headers = {
        "X-Batch-Corpus-Hits": "2",
        "X-Batch-Cellar-Served": "1",
        "X-Batch-OJ-Recovered": "0",
        "X-Batch-Live-Fetches": "1",
        "X-Batch-Text-Unavailable": "1",
    }
    client, seen = capture_client(body, headers=headers)
    with client:
        resp = client.get_documents_batch(
            ["32016R0679", "32008R1272"], language="de", accept_languages=["fr"]
        )
    assert seen["body"]["acceptLanguages"] == ["fr"]
    assert resp.text_unavailable == 1
    assert resp.corpus_hits == 2
    assert resp.cellar_served == 1
    assert resp.oj_recovered == 0
    assert resp.live_fetches == 1
    assert resp.documents[0].title_language == "en"
    assert resp.documents[1].text_available is False

    # header fallback when the body field is missing
    del body["textUnavailable"]
    client, _ = capture_client(body, headers=headers)
    with client:
        assert client.get_documents_batch(["32016R0679"]).text_unavailable == 1


def test_metadata_consolidated_hints_and_title_language():
    body = {
        "success": True,
        "metadata": {
            "celex": "32006R1907",
            "celexNumber": "32006R1907",
            "title": "REACH",
            "titleLanguage": "en",
            "latestConsolidated": {
                "celex": "02006R1907-20260622",
                "date": "2026-06-22",
                "source": "corpus",
            },
            "urls": {"pdf": "https://eur-lex.europa.eu/pdf"},
        },
    }
    client, _ = capture_client(body, headers={"X-Source": "cellar"})
    with client:
        resp = client.get_document_metadata("32006R1907")
    meta = resp.metadata
    assert resp.source == "cellar"
    assert meta.celex_number == "32006R1907"
    assert meta.title_language == "en"
    assert meta.latest_consolidated.source == "corpus"
    assert meta.urls["pdf"].endswith("pdf")


def test_by_url_bypass_corpus():
    client, seen = capture_client({"success": True, "document": {}})
    with client:
        client.get_document_by_url(
            "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679",
            bypass_corpus=True,
        )
    assert seen["body"]["bypassCorpus"] is True


def test_search_fresh_sources_cache_and_dates():
    body = {
        "searchParameters": {"query": "*", "dateFrom": "2026-10-01"},
        "totalResults": 1,
        "results": [
            {
                "celexNumber": "32026R0001",
                "celex": "32026R0001",
                "form": "Regulation",
                "dateOfDocument": "01/10/2026",
                "dateOfDocumentISO": "2026-10-01",
            }
        ],
    }
    client, seen = capture_client(
        body,
        headers={
            "X-Search-Source": "soap",
            "X-Search-Cache": "bypass",
            "X-PostFiltered-Kept": "author:3/10;date:2/3",
        },
    )
    with client:
        resp = client.search("*", date_from=dt.date(2026, 10, 1), date_to="2026-10-25", fresh=True)
    assert seen["body"] == {
        "query": "*",
        "dateFrom": "2026-10-01",
        "dateTo": "2026-10-25",
        "fresh": True,
    }
    assert resp.source == "soap"
    assert resp.cache == "bypass"
    assert resp.post_filtered_kept_raw == "author:3/10;date:2/3"
    kept = resp.post_filtered_kept
    assert (kept["author"].kept, kept["author"].before, kept["author"].dropped) == (3, 10, 7)
    assert (kept["date"].kept, kept["date"].before) == (2, 3)
    assert resp.search_parameters["query"] == "*"
    hit = resp.results[0]
    assert hit.celex_number == "32026R0001"
    assert hit.form == "Regulation"
    assert hit.date_of_document == "01/10/2026"


def test_search_corpus_fallback_source_from_body():
    body = {
        "results": [],
        "partial": True,
        "partialReason": "EUR-Lex is unreachable",
        "source": "corpus",
    }
    client, _ = capture_client(body)
    with client:
        resp = client.search("chemicals")
    assert resp.source == "corpus"
    assert resp.partial is True


def test_search_fresh_not_sent_by_default():
    client, seen = capture_client({"results": []})
    with client:
        client.search("gdpr")
    assert seen["body"] == {"query": "gdpr"}


def test_recent_lists_repeat_and_source():
    body = {"success": True, "results": [], "fetchedAt": "2026-10-26T08:00:00.000Z"}
    client, seen = capture_client(body, headers={"X-Source": "live"})
    with client:
        resp = client.get_recent_documents(
            days=14, document_type=["judgment", "order"], author="court-of-justice", limit=25
        )
    assert seen["params"].get_list("documentType") == ["judgment", "order"]
    assert seen["params"]["author"] == "court-of-justice"
    assert seen["params"]["days"] == "14"
    assert resp.source == "live"
    assert resp.fetched_at == "2026-10-26T08:00:00.000Z"


INFO = {
    "service": "LexAPI",
    "version": "2.0.0",
    "description": "European legal data",
    "endpoints": {"search": {"method": "POST"}},
    "subscription": {
        "tier": "PROFESSIONAL",
        "maxPages": 20,
        "maxBatchSize": 20,
        "maxWebhooks": 10,
        "pricingMode": "credits",
        "dailyLimit": None,
        "monthlyCredits": 150000,
        "rateLimit": "120 requests/minute",
    },
    "credits": {"operation_weight": 0, "remaining": 148760},
    "searchFilters": {"documentType": ["judgment", "regulation"], "author": ["commission"]},
    "supportedLanguages": ["en", "fr"],
}


def test_info_typed_and_rate_limit_headers():
    client, _ = capture_client(
        INFO,
        headers={"RateLimit-Limit": "120", "RateLimit-Remaining": "119", "RateLimit-Reset": "42"},
    )
    with client:
        info = client.get_info()
    assert isinstance(info, InfoResponse)
    assert info["service"] == "LexAPI"  # mapping access unchanged
    assert info.version == "2.0.0"
    assert info.endpoints == {"search": {"method": "POST"}}
    assert info.search_filters["documentType"] == ["judgment", "regulation"]
    assert info.supported_languages == ["en", "fr"]
    sub = info.subscription_info
    assert sub.tier == "PROFESSIONAL"
    assert sub.pricing_mode == "credits"
    assert sub.max_pages == 20
    assert sub.max_batch_size == 20
    assert sub.max_webhooks == 10
    assert sub.daily_limit is None
    assert sub.monthly_credits == 150000
    assert sub.rate_limit == "120 requests/minute"
    assert info.rate_limit_limit == 120
    assert info.rate_limit_remaining == 119
    assert info.rate_limit_reset == 42
    assert info.units_charged == 0


def test_path_segments_are_encoded_safely():
    body = {"success": True, "versions": []}
    client, seen = capture_client(body)
    with client:
        client.list_document_versions("32008R1272R(01)")
    assert seen["raw_path"] == "/api/v1/documents/32008R1272R(01)/versions"

    client, seen = capture_client(body)
    with client:
        client.list_document_versions("a/b?c")
    assert seen["raw_path"] == "/api/v1/documents/a%2Fb%3Fc/versions"


def test_at_date_accepts_date_object():
    client, seen = capture_client({"success": True, "document": {}})
    with client:
        client.get_document_at_date("02016R0679-20180524", dt.date(2026, 6, 15))
    assert seen["path"] == "/api/v1/documents/02016R0679-20180524/at/2026-06-15"


def test_async_document_parity():
    async def run():
        client, seen = capture_client(
            TEXT_UNAVAILABLE_DOC, headers={"X-Text-Available": "false"}, client_cls=AsyncLexAPI
        )
        async with client:
            resp = await client.get_document("32008R1272", accept_languages="any")
            info_client, _ = capture_client(INFO, client_cls=AsyncLexAPI)
            async with info_client:
                info = await info_client.get_info()
        return resp, info, seen

    resp, info, seen = asyncio.run(run())
    assert seen["body"]["acceptLanguages"] == "any"
    assert resp.text_available is False
    assert isinstance(info, InfoResponse)
    assert info.subscription_info.tier == "PROFESSIONAL"
