"""Citation graph: multi-value filters, provenance / resolution, node
identity, unresolved references, countsSource, transposition, counterparts,
national coverage ``citationGraph`` and the related error fields."""

import asyncio

import httpx
import pytest

from conftest import FAST_RETRY
from lexapi import (
    CITATION_PROVENANCES,
    CITATION_RESOLUTIONS,
    CITATION_TYPES,
    AsyncLexAPI,
    InvalidParamsError,
    LexAPI,
    NationalTermsNotAcceptedError,
    NotFoundError,
    RetryConfig,
    TierForbiddenError,
    TimeoutError,
)

APP_HEADERS = {"X-LexAPI-Version": "2.13.0", "RateLimit-Limit": "60"}


def capture_client(responder, client_cls=LexAPI, **kwargs):
    seen = {"requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["requests"].append(request)
        return responder(request)

    kwargs.setdefault("retry_config", FAST_RETRY)
    client = client_cls(api_key="lex_test_key", transport=httpx.MockTransport(handler), **kwargs)
    return client, seen


def json_responder(body, status=200, headers=None):
    return lambda request: httpx.Response(status, json=body, headers=headers or APP_HEADERS)


def params_of(seen, index=0):
    return dict(seen["requests"][index].url.params)


# ── enums ──────────────────────────────────────────────────────────────


def test_documented_enum_values():
    assert "transposition" in CITATION_TYPES
    assert len(CITATION_TYPES) == 8
    assert CITATION_PROVENANCES == (
        "notice",
        "cellar",
        "notice+cellar",
        "cellar-nim",
        "source",
        "resolver",
    )
    assert CITATION_RESOLUTIONS == ("id", "key", "abbreviation", "text-pattern")


# ── filter serialisation ───────────────────────────────────────────────


def test_cited_by_list_filters_are_comma_separated():
    client, seen = capture_client(json_responder({"success": True, "citedBy": []}))
    with client:
        client.citations.cited_by(
            "32016R0679",
            citation_type=["amendment", "repeal"],
            provenance=("notice", "notice+cellar"),
            jurisdictions=["eu", "at"],
            resolution=["id", "key"],
            include_national=True,
            language="de",
            limit=50,
            offset=100,
        )
    request = seen["requests"][0]
    assert request.url.path == "/api/v1/citations/cited-by/32016R0679"
    assert params_of(seen) == {
        "citationType": "amendment,repeal",
        "provenance": "notice,notice+cellar",
        "jurisdictions": "eu,at",
        "resolution": "id,key",
        "includeNational": "true",
        "language": "de",
        "limit": "50",
        "offset": "100",
    }
    # one key each, never repeated
    assert len(request.url.params.multi_items()) == 8


def test_single_string_filter_passes_through_and_empty_list_is_omitted():
    client, seen = capture_client(json_responder({"success": True, "cites": []}))
    with client:
        client.citations.cites("32016R0679", citation_type="reference", provenance=[])
    assert params_of(seen) == {"citationType": "reference"}


def test_non_string_filter_values_raise_before_any_request():
    client, seen = capture_client(json_responder({"success": True}))
    with client:
        with pytest.raises(TypeError):
            client.citations.network("32016R0679", jurisdictions=["eu", 1])
        with pytest.raises(TypeError):
            client.citations.related("32016R0679", provenance=5)
    assert seen["requests"] == []


def test_path_and_related_forward_the_filters():
    client, seen = capture_client(json_responder({"success": True, "found": False, "path": []}))
    with client:
        client.citations.path(
            "AT-NOR-10001597",
            "32016L0680",
            max_depth=4,
            citation_type=["transposition", "implementation"],
            provenance="source",
            jurisdictions=["eu", "at"],
            resolution=["id"],
            language="en",
        )
        client.citations.related("32016R0679", limit=5, provenance=["cellar"], resolution="key")
    assert params_of(seen, 0) == {
        "maxDepth": "4",
        "citationType": "transposition,implementation",
        "provenance": "source",
        "jurisdictions": "eu,at",
        "resolution": "id",
        "language": "en",
    }
    assert params_of(seen, 1) == {"limit": "5", "provenance": "cellar", "resolution": "key"}


def test_unresolved_flag_on_cites_and_network():
    client, seen = capture_client(json_responder({"success": True, "cites": []}))
    with client:
        client.citations.cites("AT-NOR-10001597", unresolved=True)
        client.citations.network("AT-NOR-10001597", unresolved=True, limit=10)
    assert params_of(seen, 0) == {"unresolved": "true"}
    assert params_of(seen, 1) == {"unresolved": "true", "limit": "10"}


# ── response parsing ───────────────────────────────────────────────────


CITED_BY_BODY = {
    "success": True,
    "includeNational": True,
    "targetDocument": "32016L0680",
    "totalCitations": 2,
    "totalEdges": 3,
    "countsSource": "stats",
    "uniqueDocuments": 2,
    "limit": 100,
    "offset": 0,
    "citedBy": [
        {
            "id": "62018CJ0311",
            "celexNumber": "62018CJ0311",
            "jurisdiction": "EU",
            "documentKind": "decision",
            "title": "Schrems II",
            "titleSource": "corpus",
            "hasResume": True,
            "hasSummary": False,
            "citationCount": 2,
            "citations": [
                {
                    "citationType": "reference",
                    "contextSnippet": None,
                    "extractedAt": "2026-11-01T00:00:00.000Z",
                    "provenance": "notice+cellar",
                    "resolution": None,
                },
                {
                    "citationType": "reference",
                    "contextSnippet": "…",
                    "extractedAt": "2026-11-01T00:00:00.000Z",
                    "provenance": "cellar",
                    "resolution": None,
                },
            ],
        },
        {
            "id": "AT-NOR-10001597",
            "celexNumber": "AT-NOR-10001597",
            "jurisdiction": "AT",
            "documentKind": "act",
            "title": "Datenschutzgesetz",
            "titleSource": "edge",
            "citationCount": 1,
            "citations": [
                {
                    "citationType": "transposition",
                    "contextSnippet": None,
                    "extractedAt": "2026-11-02T00:00:00.000Z",
                    "provenance": "source",
                    "resolution": "id",
                }
            ],
        },
    ],
}


def test_cited_by_parses_node_identity_provenance_and_counts():
    client, _ = capture_client(json_responder(CITED_BY_BODY))
    with client:
        resp = client.citations.cited_by("32016L0680", include_national=True)
    assert resp.total_edges == 3
    assert resp.total_citations == 2
    assert resp.counts_source == "stats"
    assert resp.include_national is True
    eu, national = resp.cited_by
    assert (eu.id, eu.jurisdiction, eu.document_kind) == ("62018CJ0311", "EU", "decision")
    assert eu.title_source == "corpus"
    assert eu.has_resume is True and eu.has_summary is False
    assert [e.provenance for e in eu.citations] == ["notice+cellar", "cellar"]
    assert eu.citations[0].resolution is None
    assert national.jurisdiction == "AT"
    assert national.has_resume is None
    edge = national.citations[0]
    assert (edge.citation_type, edge.provenance, edge.resolution) == (
        "transposition",
        "source",
        "id",
    )


def test_old_shape_without_new_fields_still_parses():
    body = {
        "success": True,
        "targetDocument": "32016R0679",
        "citedBy": [
            {
                "celexNumber": "32018R1725",
                "citationCount": 1,
                "citations": [{"citationType": "reference"}],
            }
        ],
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.cited_by("32016R0679")
    group = resp.cited_by[0]
    assert group.id is None and group.jurisdiction is None and group.document_kind is None
    assert group.citations[0].provenance is None
    assert resp.total_edges is None and resp.counts_source is None


def test_cites_unresolved_references():
    body = {
        "success": True,
        "sourceDocument": "AT-NOR-10001597",
        "totalCitations": 0,
        "totalEdges": 0,
        "countsSource": "counted",
        "cites": [],
        "unresolved": [
            {
                "kind": "norm",
                "text": "StGB §302",
                "parsedKey": {"abbreviation": "StGB", "paragraph": "302"},
                "status": "not-held",
                "targetId": None,
            },
            {
                "kind": "eu-act",
                "text": "RL 95/46/EG",
                "parsedKey": None,
                "status": "ambiguous",
                "targetId": None,
            },
        ],
        "unresolvedTruncated": False,
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.cites("AT-NOR-10001597", unresolved=True)
    assert resp.counts_source == "counted"
    first, second = resp.unresolved
    assert first.kind == "norm" and first.status == "not-held"
    assert first.parsed_key == {"abbreviation": "StGB", "paragraph": "302"}
    assert second.parsed_key is None and second.target_id is None
    assert resp.unresolved_truncated is False


def test_unresolved_absent_is_none():
    client, _ = capture_client(json_responder({"success": True, "cites": []}))
    with client:
        resp = client.citations.cites("32016R0679")
    assert resp.unresolved is None
    assert resp.unresolved_truncated is None


def test_network_edge_details_paging_totals_and_unresolved():
    body = {
        "success": True,
        "document": "32016R0679",
        "network": {
            "celexNumber": "32016R0679",
            "citesCount": 1,
            "citedByCount": 0,
            "cites": [
                {
                    "id": "31995L0046",
                    "jurisdiction": "EU",
                    "documentKind": "act",
                    "celexNumber": "31995L0046",
                    "title": "Directive 95/46/EC",
                    "titleSource": "corpus",
                    "count": 2,
                    "types": ["reference", "repeal"],
                    "edgeDetails": [
                        {"citationType": "reference", "provenance": "notice", "resolution": None},
                        {"citationType": "repeal", "provenance": "cellar", "resolution": None},
                    ],
                }
            ],
            "citedBy": [],
        },
        "paging": {
            "limit": 100,
            "offset": 0,
            "citesTotal": 35,
            "citedByTotal": 456,
            "citesEdgeTotal": 36,
            "citedByEdgeTotal": 458,
            "countsSource": "stats",
        },
        "unresolved": [{"kind": "nim", "text": "x", "parsedKey": None, "status": "unresolved"}],
        "unresolvedTruncated": True,
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.network("32016R0679")
    neighbour = resp.cites[0]
    assert neighbour.id == "31995L0046" and neighbour.document_kind == "act"
    assert neighbour.title_source == "corpus"
    assert [(d.citation_type, d.provenance) for d in neighbour.edge_details] == [
        ("reference", "notice"),
        ("repeal", "cellar"),
    ]
    assert resp.cites_edge_total == 36
    assert resp.cited_by_edge_total == 458
    assert resp.counts_source == "stats"
    assert resp.unresolved[0].kind == "nim"
    assert resp.unresolved_truncated is True


def test_partial_network_has_null_counts_source():
    body = {
        "success": True,
        "document": "62018CJ0311",
        "network": {"cites": [], "citedBy": []},
        "paging": {
            "limit": 100,
            "offset": 0,
            "citesTotal": None,
            "citedByTotal": None,
            "citesEdgeTotal": None,
            "citedByEdgeTotal": None,
            "countsSource": None,
        },
        "partial": True,
        "partialReason": "TIMEOUT",
        "timeoutMs": 30000,
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.network("62018CJ0311")
    assert resp.partial is True
    assert resp.counts_source is None
    assert resp.cites_edge_total is None


def test_path_and_related_node_identity():
    path_body = {
        "success": True,
        "from": "AT-NOR-10001597",
        "to": "32016L0680",
        "found": True,
        "pathLength": 1,
        "maxDepth": 3,
        "path": [
            {
                "id": "AT-NOR-10001597",
                "celexNumber": "AT-NOR-10001597",
                "jurisdiction": "AT",
                "documentKind": "act",
                "title": "Datenschutzgesetz",
            },
            {
                "id": "32016L0680",
                "celexNumber": "32016L0680",
                "jurisdiction": "EU",
                "documentKind": "act",
                "title": "Directive (EU) 2016/680",
            },
        ],
    }
    related_body = {
        "success": True,
        "celexNumber": "32016R0679",
        "method": "bibliographic-coupling",
        "count": 1,
        "related": [
            {
                "id": "32018R1725",
                "celexNumber": "32018R1725",
                "jurisdiction": "EU",
                "documentKind": None,
                "sharedCount": 4,
                "sharedTargets": ["31995L0046"],
            }
        ],
    }
    bodies = iter([path_body, related_body])
    client, _ = capture_client(lambda r: httpx.Response(200, json=next(bodies)))
    with client:
        path = client.citations.path("AT-NOR-10001597", "32016L0680")
        related = client.citations.related("32016R0679")
    assert [n.jurisdiction for n in path.path] == ["AT", "EU"]
    assert path.path[0].id == "AT-NOR-10001597"
    assert related.related[0].id == "32018R1725"
    assert related.related[0].document_kind is None


def test_stats_national_breakdowns():
    body = {
        "success": True,
        "includeNational": True,
        "cachedAt": "2026-11-10T10:00:00.000Z",
        "cacheTtlSeconds": 300,
        "stats": {
            "totalCitations": 100,
            "uniqueSourceDocuments": 10,
            "uniqueTargetDocuments": 20,
            "mostCited": [],
            "mostCiting": [],
            "byJurisdiction": {
                "EU": {"asSource": 61234, "asTarget": 70321},
                "AT": {"asSource": 9123, "asTarget": 41},
            },
            "byProvenance": {"notice": 50, "source": 7, "unstamped": 2},
            "transposition": {
                "measures": 1200,
                "resolved": 300,
                "byCountry": {"AT": {"measures": 100, "resolved": 80}},
            },
        },
    }
    client, seen = capture_client(json_responder(body))
    with client:
        resp = client.citations.stats(include_national=True)
    assert params_of(seen) == {"includeNational": "true"}
    assert resp.cached_at == "2026-11-10T10:00:00.000Z"
    assert resp.cache_ttl_seconds == 300
    assert resp.by_jurisdiction["AT"].as_source == 9123
    assert resp.by_jurisdiction["EU"].as_target == 70321
    assert resp.by_provenance == {"notice": 50, "source": 7, "unstamped": 2}
    assert resp.transposition.measures == 1200
    assert resp.transposition.resolved == 300
    assert resp.transposition.by_country["AT"].resolved == 80


def test_stats_without_national_has_no_breakdowns():
    body = {"success": True, "stats": {"totalCitations": 1, "mostCited": [], "mostCiting": []}}
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.stats()
    assert resp.by_jurisdiction is None
    assert resp.by_provenance is None
    assert resp.transposition is None


# ── iterators ──────────────────────────────────────────────────────────


def test_iterator_stops_at_total_edges_and_forwards_filters():
    def responder(request):
        offset = int(request.url.params["offset"])
        groups = [
            {"id": f"D{offset + i}", "celexNumber": f"D{offset + i}", "citationCount": 1}
            for i in range(2)
        ]
        body = {"success": True, "citedBy": groups, "totalEdges": 4, "limit": 2, "offset": offset}
        return httpx.Response(200, json=body)

    client, seen = capture_client(responder)
    with client:
        groups = list(
            client.citations.iter_cited_by(
                "32016R0679",
                page_size=2,
                citation_type=["amendment", "repeal"],
                provenance="cellar",
                jurisdictions=["eu"],
                language="fr",
            )
        )
    # 4 rows in two full pages: total_edges ends the walk, no third empty page
    assert [int(r.url.params["offset"]) for r in seen["requests"]] == [0, 2]
    assert [g.id for g in groups] == ["D0", "D1", "D2", "D3"]
    first = params_of(seen)
    assert first["citationType"] == "amendment,repeal"
    assert first["provenance"] == "cellar"
    assert first["jurisdictions"] == "eu"
    assert first["language"] == "fr"


def test_async_iter_cites_forwards_resolution():
    async def run():
        client, seen = capture_client(
            json_responder({"success": True, "cites": [], "totalEdges": 0}), client_cls=AsyncLexAPI
        )
        async with client:
            groups = [
                g async for g in client.citations.iter_cites("AT-NOR-1", resolution=["id", "key"])
            ]
        return groups, seen

    groups, seen = asyncio.run(run())
    assert groups == []
    assert params_of(seen)["resolution"] == "id,key"


# ── transposition ──────────────────────────────────────────────────────

NOTE = (
    "Measures are those the member state notified to the Commission for this act. "
    "A notification is not a finding that the measure transposes the act, or transposes "
    "it completely."
)

TRANSPOSITION_BODY = {
    "success": True,
    "includeNational": True,
    "document": {
        "id": "32016L0680",
        "celexNumber": "32016L0680",
        "jurisdiction": "EU",
        "documentKind": "act",
        "title": "Directive (EU) 2016/680",
    },
    "source": {
        "cellar": {
            "syncedAt": "2026-12-01T03:10:00.000Z",
            "measures": 333,
            "attribution": "Source: Publications Office of the European Union",
        },
        "lanes": ["AT"],
    },
    "note": NOTE,
    "jurisdictions": [
        {
            "jurisdiction": "AT",
            "live": True,
            "counts": {"measures": 2, "resolved": 1},
            "measures": [
                {
                    "id": "AT-NOR-10001597",
                    "resolved": True,
                    "resolution": "key",
                    "resolutionBasis": {"keyKind": "bgbl", "key": "BGBl. I Nr. 24/2018"},
                    "provenance": "cellar-nim",
                    "citationType": "transposition",
                    "title": "Datenschutz-Anpassungsgesetz 2018",
                    "typeAct": "Bundesgesetz",
                    "officialJournal": {
                        "name": "BGBl",
                        "number": "I 24/2018",
                        "date": "2018-05-15",
                    },
                    "reference": None,
                    "eli": "https://www.ris.bka.gv.at/eli/bgbl/I/2018/24",
                    "link": None,
                    "cellar": {
                        "id": "c1",
                        "celex": "72016L0680AUT_201800001",
                        "commissionReference": None,
                        "dateDocument": "2018-05-15",
                        "dateNotification": "2018-06-01",
                        "entryIntoForce": "2018-05-25",
                    },
                },
                {
                    "id": "AT-NOR-20000001",
                    "resolved": True,
                    "resolution": "id",
                    "resolutionBasis": None,
                    "provenance": "source",
                    "citationType": "implementation",
                    "title": "Polizeiliche Datenverarbeitung",
                    "typeAct": None,
                    "officialJournal": {"name": None, "number": None, "date": None},
                    "reference": None,
                    "eli": None,
                    "link": None,
                    "cellar": None,
                },
            ],
        },
        {
            "jurisdiction": "ES",
            "live": False,
            "counts": {"measures": 1, "resolved": None},
            "measures": [
                {
                    "id": None,
                    "resolved": None,
                    "resolution": None,
                    "resolutionBasis": None,
                    "provenance": "cellar-nim",
                    "citationType": "transposition",
                    "title": "Ley Orgánica 7/2021",
                    "typeAct": "Ley Orgánica",
                    "officialJournal": {
                        "name": "Boletín Oficial del Estado",
                        "number": "126/2021",
                        "date": "2021-05-27",
                    },
                    "reference": "BOE-A-2021-8806",
                    "eli": None,
                    "link": "https://www.boe.es/boe/dias/2021/05/27/pdfs/BOE-A-2021-8806.pdf",
                    "cellar": {
                        "id": "c2",
                        "celex": "72016L0680ESP_202101234",
                        "commissionReference": None,
                        "dateDocument": "2021-05-27",
                        "dateNotification": "2021-06-02",
                        "entryIntoForce": "2021-06-16",
                    },
                }
            ],
        },
    ],
    "truncated": False,
}


def test_transposition_request_shape():
    client, seen = capture_client(json_responder(TRANSPOSITION_BODY))
    with client:
        client.citations.transposition(
            "32016L0680", jurisdictions=["at", "es"], resolved_only=True, language="de"
        )
        client.citations.transposition("32016L0680")
    request = seen["requests"][0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/citations/transposition/32016L0680"
    assert params_of(seen, 0) == {
        "jurisdictions": "at,es",
        "resolvedOnly": "true",
        "language": "de",
    }
    assert params_of(seen, 1) == {}


def test_transposition_parses_every_field():
    client, _ = capture_client(json_responder(TRANSPOSITION_BODY))
    with client:
        resp = client.citations.transposition("32016L0680")
    assert resp.note == NOTE
    assert resp.include_national is True
    assert resp.terms_required is False
    assert resp.truncated is False
    assert resp.document.id == "32016L0680"
    assert resp.document.document_kind == "act"
    assert resp.source_cellar.measures == 333
    assert resp.source_cellar.synced_at == "2026-12-01T03:10:00.000Z"
    assert resp.source_cellar.attribution.startswith("Source:")
    assert resp.source_lanes == ["AT"]

    at, es = resp.jurisdictions
    assert at.jurisdiction == "AT" and at.live is True
    assert (at.counts.measures, at.counts.resolved) == (2, 1)
    notified, lane = at.measures
    assert notified.id == "AT-NOR-10001597"
    assert notified.resolved is True
    assert notified.resolution == "key"
    assert notified.resolution_basis.key_kind == "bgbl"
    assert notified.resolution_basis.key == "BGBl. I Nr. 24/2018"
    assert notified.provenance == "cellar-nim"
    assert notified.citation_type == "transposition"
    assert notified.type_act == "Bundesgesetz"
    assert notified.official_journal.number == "I 24/2018"
    assert notified.eli.endswith("/24")
    assert notified.cellar.celex == "72016L0680AUT_201800001"
    assert notified.cellar.commission_reference is None
    assert notified.cellar.date_notification == "2018-06-01"
    assert notified.cellar.entry_into_force == "2018-05-25"
    assert lane.provenance == "source"
    assert lane.cellar is None
    assert lane.citation_type == "implementation"

    assert es.live is False
    assert es.counts.resolved is None
    measure = es.measures[0]
    assert measure.id is None and measure.resolved is None and measure.resolution is None
    assert measure.resolution_basis is None
    assert measure.reference == "BOE-A-2021-8806"
    assert measure.official_journal.name == "Boletín Oficial del Estado"


def test_transposition_terms_required_and_dark_shape():
    body = {
        "success": True,
        "includeNational": False,
        "termsRequired": True,
        "document": {"id": "32016L0680", "celexNumber": "32016L0680"},
        "source": {"cellar": {"syncedAt": None, "measures": 0}, "lanes": None},
        "note": NOTE,
        "jurisdictions": [],
        "truncated": True,
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.transposition("32016L0680")
    assert resp.terms_required is True
    assert resp.include_national is False
    assert resp.source_lanes is None
    assert resp.source_cellar.synced_at is None
    assert resp.truncated is True
    assert resp.jurisdictions == []


def test_transposition_not_an_eu_act_is_not_found():
    body = {"success": False, "code": "NOT_FOUND", "error": "Not an EU act: 32016R0679"}
    client, _ = capture_client(json_responder(body, status=404))
    with client:
        with pytest.raises(NotFoundError):
            client.citations.transposition("32016R0679")


def test_transposition_unknown_jurisdiction_is_invalid_params():
    body = {
        "success": False,
        "code": "INVALID_PARAMS",
        "error": "Invalid jurisdictions",
        "param": "jurisdictions",
        "allowed": ["AT", "BE"],
    }
    client, _ = capture_client(json_responder(body, status=400))
    with client:
        with pytest.raises(InvalidParamsError) as info:
            client.citations.transposition("32016L0680", jurisdictions="xx")
    assert info.value.param == "jurisdictions"
    assert info.value.allowed == ["AT", "BE"]


# ── counterparts ───────────────────────────────────────────────────────

COUNTERPARTS_BODY = {
    "success": True,
    "includeNational": True,
    "document": {
        "id": "AT-NOR-10001597",
        "jurisdiction": "AT",
        "documentKind": "act",
        "title": "Datenschutzgesetz",
    },
    "via": [
        {
            "euAct": {
                "id": "32016L0680",
                "celexNumber": "32016L0680",
                "jurisdiction": "EU",
                "documentKind": "act",
                "title": "Directive (EU) 2016/680",
                "notifiedMeasures": 333,
            },
            "edge": {"citationType": "transposition", "provenance": "source", "resolution": "id"},
            "counterparts": [
                {
                    "jurisdiction": "SI",
                    "id": "SI-ZAKO7959",
                    "resolved": True,
                    "title": "ZVOPOKD",
                    "citationType": "transposition",
                    "provenance": "source",
                    "rank": 1,
                },
                {
                    "jurisdiction": "CZ",
                    "id": None,
                    "resolved": False,
                    "title": "Zákon č. 110/2019 Sb.",
                    "citationType": "transposition",
                    "provenance": "cellar-nim",
                    "rank": 3,
                },
            ],
            "truncated": False,
        }
    ],
    "truncated": False,
}


def test_counterparts_request_shape_and_parsing():
    client, seen = capture_client(json_responder(COUNTERPARTS_BODY))
    with client:
        resp = client.citations.counterparts(
            "AT-NOR-10001597", jurisdictions=["si", "cz"], language="en"
        )
    request = seen["requests"][0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/citations/counterparts/AT-NOR-10001597"
    assert params_of(seen) == {"jurisdictions": "si,cz", "language": "en"}

    assert resp.include_national is True
    assert resp.truncated is False
    assert resp.document.id == "AT-NOR-10001597"
    assert resp.document.jurisdiction == "AT"
    via = resp.via[0]
    assert via.eu_act.id == "32016L0680"
    assert via.eu_act.notified_measures == 333
    assert via.edge.provenance == "source"
    assert via.edge.resolution == "id"
    assert via.truncated is False
    first, third = via.counterparts
    assert (first.jurisdiction, first.id, first.rank, first.resolved) == (
        "SI",
        "SI-ZAKO7959",
        1,
        True,
    )
    assert third.id is None and third.rank == 3 and third.provenance == "cellar-nim"


def test_counterparts_for_an_eu_act_has_no_edge():
    body = {
        "success": True,
        "document": {"id": "32016L0680", "jurisdiction": "EU", "documentKind": "act"},
        "via": [{"euAct": {"id": "32016L0680"}, "edge": None, "counterparts": []}],
        "truncated": False,
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.citations.counterparts("32016L0680")
    assert resp.via[0].edge is None
    assert resp.via[0].counterparts == []
    assert resp.include_national is None


def test_counterparts_timeout_is_a_typed_timeout_error():
    body = {
        "success": False,
        "code": "TIMEOUT",
        "error": "Counterpart lookup timed out",
        "partial": True,
        "partialReason": "TIMEOUT",
        "timeoutMs": 20000,
    }
    client, seen = capture_client(
        json_responder(body, status=503), retry_config=RetryConfig(max_retries=0)
    )
    with client:
        with pytest.raises(TimeoutError) as info:
            client.citations.counterparts("AT-NOR-10001597")
    err = info.value
    assert err.status == 503
    assert err.partial is True
    assert err.partial_reason == "TIMEOUT"
    assert err.timeout_ms == 20000
    assert len(seen["requests"]) == 1


BUDGET_TIMEOUT_BODY = {
    "success": False,
    "code": "TIMEOUT",
    "error": "Counterpart lookup timed out",
    "partial": True,
    "partialReason": "TIMEOUT",
    "timeoutMs": 20000,
}


def test_budget_timeout_503_is_never_retried():
    responses = iter(
        [
            httpx.Response(503, json=BUDGET_TIMEOUT_BODY, headers=APP_HEADERS),
            httpx.Response(200, json=COUNTERPARTS_BODY, headers=APP_HEADERS),
        ]
    )
    client, seen = capture_client(lambda r: next(responses))
    with client:
        with pytest.raises(TimeoutError) as info:
            client.citations.counterparts("AT-NOR-10001597")
    assert info.value.partial_reason == "TIMEOUT"
    assert len(seen["requests"]) == 1


def test_async_budget_timeout_503_is_never_retried():
    async def run():
        responses = iter(
            [
                httpx.Response(503, json=BUDGET_TIMEOUT_BODY, headers=APP_HEADERS),
                httpx.Response(200, json=COUNTERPARTS_BODY, headers=APP_HEADERS),
            ]
        )
        client, seen = capture_client(lambda r: next(responses), client_cls=AsyncLexAPI)
        async with client:
            with pytest.raises(TimeoutError):
                await client.citations.counterparts("AT-NOR-10001597")
        return seen

    seen = asyncio.run(run())
    assert len(seen["requests"]) == 1


def test_other_app_503_is_still_retried_on_get():
    responses = iter(
        [
            httpx.Response(
                503,
                json={"success": False, "code": "SERVICE_UNAVAILABLE", "error": "draining"},
                headers=APP_HEADERS,
            ),
            httpx.Response(200, json=COUNTERPARTS_BODY, headers=APP_HEADERS),
        ]
    )
    client, seen = capture_client(lambda r: next(responses))
    with client:
        resp = client.citations.counterparts("AT-NOR-10001597")
    assert len(seen["requests"]) == 2
    assert resp.via[0].eu_act.id == "32016L0680"


def test_counterparts_below_business_is_tier_forbidden():
    body = {
        "success": False,
        "code": "TIER_FORBIDDEN",
        "error": "Requires BUSINESS",
        "upgradeUrl": "/pricing",
    }
    client, _ = capture_client(json_responder(body, status=403))
    with client:
        with pytest.raises(TierForbiddenError) as info:
            client.citations.counterparts("32016L0680")
    assert info.value.upgrade_url == "/pricing"


def test_national_terms_not_accepted_error():
    body = {
        "success": False,
        "code": "NATIONAL_TERMS_NOT_ACCEPTED",
        "error": "National-source terms not accepted",
        "termsVersion": "2026-10-16",
        "acceptUrl": "https://lex-api.com/dashboard",
        "termsUrl": "https://lex-api.com/terms",
    }
    client, _ = capture_client(json_responder(body, status=403))
    with client:
        with pytest.raises(NationalTermsNotAcceptedError) as info:
            client.citations.counterparts("AT-NOR-10001597")
    err = info.value
    assert err.code == "NATIONAL_TERMS_NOT_ACCEPTED"
    assert err.terms_version == "2026-10-16"
    assert err.accept_url == "https://lex-api.com/dashboard"
    assert err.terms_url == "https://lex-api.com/terms"


def test_async_transposition_and_counterparts():
    async def run():
        bodies = iter([TRANSPOSITION_BODY, COUNTERPARTS_BODY])
        client, seen = capture_client(
            lambda r: httpx.Response(200, json=next(bodies)), client_cls=AsyncLexAPI
        )
        async with client:
            trans = await client.citations.transposition("32016L0680", resolved_only=False)
            counter = await client.citations.counterparts("AT-NOR-10001597", jurisdictions="si")
        return trans, counter, seen

    trans, counter, seen = asyncio.run(run())
    assert params_of(seen, 0) == {"resolvedOnly": "false"}
    assert params_of(seen, 1) == {"jurisdictions": "si"}
    assert trans.jurisdictions[0].measures[0].id == "AT-NOR-10001597"
    assert counter.via[0].counterparts[0].rank == 1


# ── invalid filter values ──────────────────────────────────────────────


def test_invalid_filter_legacy_shape_exposes_param_and_allowed():
    body = {
        "success": False,
        "code": "INVALID_PARAMS",
        "error": "Invalid provenance",
        "param": "provenance",
        "allowed": ["notice", "cellar", "notice+cellar"],
    }
    client, _ = capture_client(json_responder(body, status=400))
    with client:
        with pytest.raises(InvalidParamsError) as info:
            client.citations.cited_by("32016R0679", provenance="source")
    assert info.value.param == "provenance"
    assert info.value.allowed == ["notice", "cellar", "notice+cellar"]


def test_invalid_filter_typed_shape_reads_details():
    body = {
        "success": False,
        "error": {
            "code": "INVALID_PARAMS",
            "message": "Unknown resolution",
            "details": {"param": "resolution", "allowed": []},
        },
    }
    client, _ = capture_client(json_responder(body, status=400))
    with client:
        with pytest.raises(InvalidParamsError) as info:
            client.citations.related("32016R0679", resolution="id")
    assert info.value.param == "resolution"
    assert info.value.allowed == []


def test_invalid_params_without_param_fields():
    body = {"success": False, "code": "INVALID_PARAMS", "error": "bad", "details": ["x"]}
    client, _ = capture_client(json_responder(body, status=400))
    with client:
        with pytest.raises(InvalidParamsError) as info:
            client.citations.cited_by("32016R0679")
    assert info.value.param is None
    assert info.value.allowed is None


# ── national coverage: citationGraph ───────────────────────────────────


def test_coverage_citation_graph():
    body = {
        "updatedAt": "2026-11-10T00:00:00.000Z",
        "jurisdictions": [
            {
                "code": "NL",
                "name": "Netherlands",
                "live": True,
                "citationGraph": {
                    "methods": {
                        "decisions": "id",
                        "acts": ["id", "key"],
                        "eu": ["text-pattern"],
                        "unmeasured": {"decisionsToActs": "not-collected"},
                    },
                    "edges": 12345,
                    "decisionResolutionRate": None,
                    "actEuRate": None,
                    "textPatternRate": 0.91,
                    "transposition": {"measures": 400, "resolved": 350},
                    "precision": {
                        "resolver": "key",
                        "resolverVersion": 2,
                        "provenance": "resolver",
                        "labelled": 200,
                        "errorUpperBound": 0.015,
                    },
                    "measuredAt": "2026-11-09T00:00:00.000Z",
                    "nimResolvedRate": 0.875,
                    "nimMeasuredAt": "2026-11-08T00:00:00.000Z",
                    "floors": {
                        "decisionsToActs": None,
                        "actsToEu": None,
                        "textPattern": 0.81,
                        "nimResolved": 0.775,
                        "derivedAt": "2026-11-09T12:00:00.000Z",
                        "measuredAt": {
                            "rebuild": "2026-11-09T00:00:00.000Z",
                            "nim": "2026-11-08T00:00:00.000Z",
                        },
                    },
                },
            },
            {"code": "FR", "name": "France", "live": False, "citationGraph": None},
            {"code": "AT", "name": "Austria", "live": True},
        ],
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.national.coverage()
    nl, fr, at = resp.jurisdictions
    graph = nl.citation_graph
    assert graph.edges == 12345
    assert graph.methods.decisions == "id"
    assert graph.methods.acts == ["id", "key"]
    assert graph.methods.eu == ["text-pattern"]
    assert graph.methods.unmeasured == {"decisionsToActs": "not-collected"}
    assert graph.decision_resolution_rate is None
    assert graph.act_eu_rate is None
    assert graph.text_pattern_rate == 0.91
    assert (graph.transposition.measures, graph.transposition.resolved) == (400, 350)
    assert graph.precision.resolver == "key"
    assert graph.precision.resolver_version == 2
    assert graph.precision.provenance == "resolver"
    assert graph.precision.labelled == 200
    assert graph.precision.error_upper_bound == 0.015
    assert graph.measured_at == "2026-11-09T00:00:00.000Z"
    assert graph.nim_resolved_rate == 0.875
    assert graph.nim_measured_at == "2026-11-08T00:00:00.000Z"
    floors = graph.floors
    assert floors.decisions_to_acts is None and floors.acts_to_eu is None
    assert floors.text_pattern == 0.81
    assert floors.nim_resolved == 0.775
    assert floors.derived_at == "2026-11-09T12:00:00.000Z"
    assert floors.measured_at_rebuild == "2026-11-09T00:00:00.000Z"
    assert floors.measured_at_nim == "2026-11-08T00:00:00.000Z"
    assert fr.citation_graph is None
    assert at.citation_graph is None


def test_coverage_citation_graph_without_unmeasured_or_floors():
    body = {
        "jurisdictions": [
            {
                "code": "AT",
                "live": True,
                "citationGraph": {
                    "methods": {"decisions": "abbreviation", "acts": [], "eu": ["id"]},
                    "edges": 5,
                    "floors": None,
                    "precision": {"resolver": None, "errorUpperBound": None},
                },
            }
        ]
    }
    client, _ = capture_client(json_responder(body))
    with client:
        resp = client.national.coverage()
    graph = resp.jurisdictions[0].citation_graph
    assert graph.methods.unmeasured is None
    assert graph.floors is None
    assert graph.transposition is None
    assert graph.precision.error_upper_bound is None
    assert graph.nim_resolved_rate is None
