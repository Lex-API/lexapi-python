"""Citations (relationSync, extract document, titleSource, edge-page
iterators), export (includeStubs, _done counters), case-law hit fields, and
client.with_options / default timeouts."""

import asyncio
import json

import httpx
import pytest

from lexapi import AsyncLexAPI, LexAPI, UpstreamError
from lexapi._base import DEFAULT_TIMEOUT


def capture_client(responder, client_cls=LexAPI, **kwargs):
    seen = {"requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["requests"].append(request)
        return responder(request)

    client = client_cls(api_key="lex_test_key", transport=httpx.MockTransport(handler), **kwargs)
    return client, seen


def json_responder(body, status=200, headers=None):
    return lambda request: httpx.Response(status, json=body, headers=headers or {})


# ── citations ──────────────────────────────────────────────────────────


def test_cited_by_relation_sync_and_page_counts():
    body = {
        "success": True,
        "targetDocument": "32008R1272",
        "totalCitations": 37,
        "uniqueDocuments": 2,
        "limit": 100,
        "offset": 0,
        "citedBy": [
            {
                "celexNumber": "32008R1272R(01)",
                "citationCount": 1,
                "citations": [{"citationType": "corrigendum"}],
            },
            {"celexNumber": "32009R0790", "citationCount": 2, "citations": [{}, {}]},
        ],
        "relationSync": {"source": "cellar", "status": "pending", "syncedAt": None},
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.cited_by("32008R1272", citation_type="corrigendum")
    sync = resp.relation_sync
    assert (sync.source, sync.status, sync.synced_at) == ("cellar", "pending", None)
    assert resp.edge_count == 3
    assert resp.cited_by[0].citations[0].citation_type == "corrigendum"


def test_network_relation_sync_skipped_reason():
    body = {
        "success": True,
        "document": "62018CJ0311",
        "network": {"cites": [], "citedBy": []},
        "paging": {"limit": 100, "offset": 0, "citesTotal": 0, "citedByTotal": 0},
        "relationSync": {
            "source": "cellar",
            "status": "skipped",
            "reason": "not-applicable",
            "syncedAt": None,
        },
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.network("62018CJ0311")
    assert resp.relation_sync.status == "skipped"
    assert resp.relation_sync.reason == "not-applicable"


def test_extract_document_and_title_sources():
    client, _ = capture_client(
        json_responder(
            {
                "success": True,
                "message": "Citations extracted successfully",
                "document": {"celexNumber": "32016R0679", "title": "GDPR"},
                "citationCount": 1,
                "citations": [{"targetCelex": "31995L0046", "citationType": "repeal"}],
            }
        )
    )
    with client:
        resp = client.citations.extract("32016R0679")
    assert resp.document.celex_number == "32016R0679"
    assert resp.document.title == "GDPR"

    client, _ = capture_client(
        json_responder({"success": True, "alreadyExtracted": True, "citations": []})
    )
    with client:
        assert client.citations.extract("32016R0679").document is None

    client, _ = capture_client(
        json_responder(
            {
                "success": True,
                "found": True,
                "path": [{"celexNumber": "32016R0679", "title": "GDPR", "titleSource": "corpus"}],
            }
        )
    )
    with client:
        assert client.citations.path("32016R0679", "31995L0046").path[0].title_source == "corpus"

    client, _ = capture_client(
        json_responder(
            {
                "success": True,
                "related": [{"celexNumber": "32016L0680", "sharedCount": 9, "titleSource": "edge"}],
            }
        )
    )
    with client:
        assert client.citations.related("32016R0679").related[0].title_source == "edge"


def _edge_pages(total_rows, page_key="citedBy"):
    """Responder serving `total_rows` edge rows, one group per row pair."""

    def responder(request):
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        rows = max(0, min(limit, total_rows - offset))
        groups, left = [], rows
        idx = offset
        while left:
            n = min(2, left)
            groups.append({"celexNumber": f"DOC{idx}", "citationCount": n, "citations": [{}] * n})
            idx += n
            left -= n
        return httpx.Response(
            200, json={"success": True, page_key: groups, "limit": limit, "offset": offset}
        )

    return responder


def test_iter_cited_by_walks_edge_pages():
    client, seen = capture_client(_edge_pages(7))
    with client:
        groups = list(client.citations.iter_cited_by("32008R1272", page_size=3))
    offsets = [int(r.url.params["offset"]) for r in seen["requests"]]
    assert offsets == [0, 3, 6]  # stops on the short page
    assert sum(g.citation_count for g in groups) == 7


def test_iter_cites_exact_multiple_needs_one_empty_page():
    client, seen = capture_client(_edge_pages(4, page_key="cites"))
    with client:
        groups = list(
            client.citations.iter_cites("32016R0679", page_size=2, citation_type="reference")
        )
    assert [int(r.url.params["offset"]) for r in seen["requests"]] == [0, 2, 4]
    assert seen["requests"][0].url.params["citationType"] == "reference"
    assert sum(g.citation_count for g in groups) == 4


def test_async_iter_cited_by():
    async def run():
        client, seen = capture_client(_edge_pages(5), client_cls=AsyncLexAPI)
        async with client:
            groups = [g async for g in client.citations.iter_cited_by("X", page_size=2)]
        return groups, seen

    groups, seen = asyncio.run(run())
    assert len(seen["requests"]) == 3
    assert sum(g.citation_count for g in groups) == 5


def test_citation_path_segments_keep_corrigendum_parens():
    client, seen = capture_client(json_responder({"success": True, "citedBy": []}))
    with client:
        client.citations.cited_by("32008R1272R(01)")
    assert (
        seen["requests"][0]
        .url.raw_path.decode()
        .startswith("/api/v1/citations/cited-by/32008R1272R(01)")
    )


# ── export ─────────────────────────────────────────────────────────────


def test_export_include_stubs_and_done_counters():
    lines = [
        {"_meta": {"totalMatching": 3}},
        {"celex": "32016R0679"},
        {"_done": {"written": 1, "truncated": False, "skippedStubs": 2}},
    ]
    content = ("\n".join(json.dumps(line) for line in lines) + "\n").encode()
    client, seen = capture_client(
        lambda request: httpx.Response(200, content=content, headers={"X-Export-Total": "3"})
    )
    with client:
        with client.export(
            include_stubs=False, include_html=True, document_type=["regulation", "directive"]
        ) as stream:
            assert stream.written is None  # trailer not read yet
            rows = list(stream)
        assert rows == [{"celex": "32016R0679"}]
        assert stream.written == 1
        assert stream.skipped_stubs == 2
    params = seen["requests"][0].url.params
    assert params["includeStubs"] == "false"
    assert params["includeHtml"] == "true"
    assert params["documentType"] == "regulation,directive"


def test_export_include_stubs_omitted_by_default():
    client, seen = capture_client(lambda request: httpx.Response(200, content=b""))
    with client:
        with client.export():
            pass
    assert "includeStubs" not in seen["requests"][0].url.params


# ── semantic case-law hit fields ───────────────────────────────────────


def test_case_law_hit_snippet_and_record_fields():
    body = {
        "success": True,
        "searchType": "semantic-case-law",
        "results": [
            {
                "case_id": "ECLI_EU_C_2024_785_en_c0007",
                "score": 0.764,
                "jurisdiction": "EU",
                "language": "en",
                "case_name": "Land Hessen",
                "court": "Court of Justice",
                "decision_date": "2024-09-26",
                "case_number": "C-768/21",
                "ecli": "ECLI:EU:C:2024:785",
                "parties": "Land Hessen v TR",
                "summary": "The Court examined whether...",
                "snippet": "The Court examined whether, under the GDPR...",
                "outcome": "dismissed",
                "metadata": {
                    "celex_id": "62021CJ0768",
                    "document_type": "judgment",
                    "paragraph_number": 42,
                },
            }
        ],
        "metadata": {"processing_time_ms": 12},
        "subscription": {"tier": "PROFESSIONAL", "maxResults": 50},
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.semantic_search("data protection breach notification")
    hit = resp.results[0]
    assert hit.text is None
    assert hit.snippet.startswith("The Court examined")
    assert hit.summary == "The Court examined whether..."
    assert hit.parties == "Land Hessen v TR"
    assert hit.decision_date == "2024-09-26"
    assert hit.jurisdiction == "EU"
    assert hit.language == "en"
    assert hit.paragraph_number == 42
    assert hit.raw["outcome"] == "dismissed"
    assert resp.max_results == 50
    assert resp.upstream_metadata == {"processing_time_ms": 12}


# ── client options ─────────────────────────────────────────────────────


def test_default_timeout_above_server_budget():
    client, seen = capture_client(json_responder({"service": "LexAPI"}))
    with client:
        client.get_info()
    timeout = seen["requests"][0].extensions["timeout"]
    assert DEFAULT_TIMEOUT == 95.0
    assert timeout["read"] == 95.0
    assert timeout["connect"] == 10.0


def test_with_options_overrides_timeout_and_retries_and_shares_pool():
    calls = {"count": 0}

    def responder(request):
        calls["count"] += 1
        return httpx.Response(503, json={"error": "busy"})

    client, seen = capture_client(responder)
    with client:
        fast = client.with_options(timeout=180, max_retries=0)
        assert fast._http is client._http
        assert fast.citations._client is fast and client.citations._client is client
        with pytest.raises(UpstreamError):
            fast.get_info()
        assert calls["count"] == 1  # no retries on the view
        assert seen["requests"][0].extensions["timeout"]["read"] == 180
        assert client._retry.max_retries == 3  # the original keeps its policy
    client2, seen2 = capture_client(json_responder({"service": "LexAPI"}))
    with client2:
        view = client2.with_options(timeout=httpx.Timeout(5.0))
        view.webhooks.list()
        assert seen2["requests"][0].extensions["timeout"]["read"] == 5.0
        client2.get_info()
        assert seen2["requests"][1].extensions["timeout"]["read"] == 95.0


def test_with_options_applies_to_export_stream():
    client, seen = capture_client(lambda request: httpx.Response(200, content=b""))
    with client:
        with client.with_options(timeout=300).export():
            pass
    assert seen["requests"][0].extensions["timeout"]["read"] == 300


def test_async_with_options():
    async def run():
        client, seen = capture_client(json_responder({"service": "LexAPI"}), client_cls=AsyncLexAPI)
        async with client:
            await client.with_options(timeout=120).get_info()
        return seen

    seen = asyncio.run(run())
    assert seen["requests"][0].extensions["timeout"]["read"] == 120
