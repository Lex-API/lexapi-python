"""Version snapshots: when a version began and ended, the snapshot-on-a-date
answer, and the reasons on its 404 (API 2.28.0)."""

import asyncio
import copy
import pickle

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    DocumentVersionResponse,
    DocumentVersionsResponse,
    FieldProvenance,
    InForceLookup,
    LexAPI,
    NotFoundError,
    Provenance,
    VersionCandidate,
    VersionEntry,
)

LOOPBACK = "http://127.0.0.1:9/api/v1"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def _handler(status, body, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        seen.append(request)
        return httpx.Response(status, json=body)

    return handler


def sync_client(status, body, seen):
    return LexAPI(
        "lex_test_key",
        base_url=LOOPBACK,
        max_retries=0,
        transport=httpx.MockTransport(_handler(status, body, seen)),
    )


def async_client(status, body, seen):
    return AsyncLexAPI(
        "lex_test_key",
        base_url=LOOPBACK,
        max_retries=0,
        transport=httpx.MockTransport(_handler(status, body, seen)),
    )


PROVENANCE = {
    "publisher": "Publications Office of the European Union",
    "sourceUrl": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679",
    "sourceIdentifier": "32016R0679",
    "retrievedAt": "2026-03-15T08:00:00.000Z",
    "contentHash": "hash-v2",
    "versionIdentifier": "32016R0679",
    "language": "en",
    "licence": "EUR-Lex legal notice",
}

FIELD_PROVENANCE = {
    "official_source_text": ["title", "parsedContent.fullText"],
    "machine_extracted_structure": ["documentType", "parsedContent.articles"],
}

# The docs' /versions example.
VERSIONS_BODY = {
    "success": True,
    "celex": "32016R0679",
    "language": "en",
    "currentVersion": 3,
    "trackedSince": "2026-01-10T08:00:00.000Z",
    "versions": [
        {
            "version": 3,
            "isCurrent": True,
            "contentHash": "h3",
            "fetchedAt": "2026-06-01T09:00:00.000Z",
            "observedFrom": "2026-06-01T09:00:00.000Z",
        },
        {
            "version": 2,
            "isCurrent": False,
            "contentHash": "h2",
            "fetchedAt": "2026-03-15T08:00:00.000Z",
            "observedFrom": None,
        },
        {"version": 1, "isCurrent": False, "contentHash": "h1", "fetchedAt": "2026-01-10"},
    ],
}

# The docs' /at/{date} example.
AT_DATE_BODY = {
    "success": True,
    "celex": "32016R0679",
    "language": "en",
    "kind": "observation_snapshot",
    "asOf": "2026-04-01",
    "semantics": "Versions are LexAPI observation snapshots…",
    "inForceLookup": {"method": "POST", "path": "/api/v1/legislation/as-of"},
    "document": {
        "version": 2,
        "isCurrent": False,
        "contentHash": "hash-v2",
        "fetchedAt": "2026-03-15T08:00:00.000Z",
        "observedFrom": "2026-03-15T08:00:00.000Z",
        "observedUntil": "2026-06-01T09:00:00.000Z",
        "title": "GDPR",
        "parsedContent": {"fullText": "…"},
        "provenance": PROVENANCE,
        "fieldProvenance": FIELD_PROVENANCE,
    },
}


def before_tracking_body(with_lookup=True):
    details = {"reason": "before_tracking", "trackedSince": "2026-01-10T08:00:00.000Z"}
    if with_lookup:
        details["inForceLookup"] = {"method": "POST", "path": "/api/v1/legislation/as-of"}
    return {
        "success": False,
        "error": {
            "code": "NOT_FOUND",
            "message": "No snapshot before the document entered the corpus",
            "details": details,
        },
    }


BOUNDARY_BODY = {
    "success": False,
    "error": {
        "code": "NOT_FOUND",
        "message": "Cannot tell which snapshot was held on this date",
        "details": {
            "reason": "boundary_unknown",
            "candidates": [
                {"version": 1, "fetchedAt": "2026-05-02T10:00:00.000Z", "observedFrom": None},
                {
                    "version": 2,
                    "fetchedAt": "2026-05-20T10:00:00.000Z",
                    "observedFrom": "2026-05-20T10:00:00.000Z",
                },
                "not-an-object",
            ],
            "candidatesTruncated": True,
        },
    },
}


# ── /versions and /versions/{version} ────────────────────────────────────


def test_observed_from_on_every_version_present_null_and_absent():
    resp = DocumentVersionsResponse(VERSIONS_BODY)
    v3, v2, v1 = resp.versions
    assert v3.observed_from == "2026-06-01T09:00:00.000Z"
    assert v3.fetched_at == "2026-06-01T09:00:00.000Z"
    assert v2.observed_from is None  # null: no record of when it began
    assert v2.fetched_at == "2026-03-15T08:00:00.000Z"
    assert v1.observed_from is None  # absent: an API before 2.28.0
    assert v3.observed_until is None  # only /at/{date} sends it


def test_version_snapshot_carries_observed_from_and_provenance():
    body = copy.deepcopy(AT_DATE_BODY)
    for key in ("kind", "asOf", "inForceLookup"):
        del body[key]
    del body["document"]["observedUntil"]
    resp = DocumentVersionResponse(body)
    doc = resp.document
    assert doc.observed_from == "2026-03-15T08:00:00.000Z"
    assert doc.observed_until is None
    assert isinstance(doc.provenance, Provenance)
    assert doc.provenance.content_hash == "hash-v2"
    assert doc.provenance.retrieved_at == "2026-03-15T08:00:00.000Z"
    assert doc.provenance.licence == "EUR-Lex legal notice"
    assert doc.field_provenance == FieldProvenance(
        official_source_text=["title", "parsedContent.fullText"],
        machine_extracted_structure=["documentType", "parsedContent.articles"],
        machine_generated_translation=[],
        machine_generated_interpretation=[],
        editorial_enrichment=[],
        raw=FIELD_PROVENANCE,
    )
    # Not /at/{date}: no snapshot-answer fields.
    assert (resp.kind, resp.date_in_future, resp.in_force_lookup) == (None, False, None)


def test_version_entry_without_new_fields_keeps_defaults():
    entry = VersionEntry.from_dict({"version": 1})
    assert (entry.observed_from, entry.observed_until) == (None, None)
    assert (entry.provenance, entry.field_provenance) == (None, None)


def test_mistyped_timing_fields_are_none():
    entry = VersionEntry.from_dict(
        {"observedFrom": 5, "observedUntil": {}, "provenance": [], "fieldProvenance": "x"}
    )
    assert (entry.observed_from, entry.observed_until) == (None, None)
    assert (entry.provenance, entry.field_provenance) == (None, None)


# ── /at/{date} ───────────────────────────────────────────────────────────


def test_at_date_answer_is_typed():
    resp = DocumentVersionResponse(AT_DATE_BODY)
    assert resp.kind == "observation_snapshot"
    assert resp.as_of == "2026-04-01"
    assert resp.date_in_future is False  # absent
    assert resp.in_force_lookup == InForceLookup(
        method="POST", path="/api/v1/legislation/as-of", raw=AT_DATE_BODY["inForceLookup"]
    )
    doc = resp.document
    assert doc.observed_from == "2026-03-15T08:00:00.000Z"
    assert doc.observed_until == "2026-06-01T09:00:00.000Z"
    assert doc.fetched_at == "2026-03-15T08:00:00.000Z"


def test_at_date_current_version_and_future_date():
    body = copy.deepcopy(AT_DATE_BODY)
    body["document"]["observedUntil"] = None
    body["dateInFuture"] = True
    del body["inForceLookup"]  # case law, treaties, corrigenda
    resp = DocumentVersionResponse(body)
    assert resp.document.observed_until is None
    assert resp.date_in_future is True
    assert resp.in_force_lookup is None


def test_in_force_lookup_that_is_not_an_object_is_none():
    body = dict(AT_DATE_BODY, inForceLookup="POST /api/v1/legislation/as-of")
    assert DocumentVersionResponse(body).in_force_lookup is None


def test_get_document_at_date_sync_and_async():
    seen: list[httpx.Request] = []
    with sync_client(200, AT_DATE_BODY, seen) as client:
        resp = client.get_document_at_date("32016R0679", "2026-04-01")
    assert resp.kind == "observation_snapshot"
    assert resp.document.observed_until == "2026-06-01T09:00:00.000Z"

    async def run():
        async with async_client(200, AT_DATE_BODY, seen) as client:
            return await client.get_document_at_date("32016R0679", "2026-04-01")

    aresp = asyncio.run(run())
    assert aresp.document.observed_from == "2026-03-15T08:00:00.000Z"
    assert [r.url.path for r in seen] == ["/api/v1/documents/32016R0679/at/2026-04-01"] * 2


# ── the 404 reasons ──────────────────────────────────────────────────────


def _raise_at_date(body, mode="sync"):
    seen: list[httpx.Request] = []
    if mode == "sync":
        with sync_client(404, body, seen) as client, pytest.raises(NotFoundError) as info:
            client.get_document_at_date("32016R0679", "2020-01-01")
        return info.value

    async def run():
        async with async_client(404, body, seen) as client:
            await client.get_document_at_date("32016R0679", "2020-01-01")

    with pytest.raises(NotFoundError) as info:
        asyncio.run(run())
    return info.value


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_before_tracking(mode):
    err = _raise_at_date(before_tracking_body(), mode)
    assert err.reason == "before_tracking"
    assert err.before_tracking is True
    assert err.boundary_unknown is False
    assert err.unknown_endpoint is False
    assert err.tracked_since == "2026-01-10T08:00:00.000Z"
    assert err.in_force_lookup is not None
    assert (err.in_force_lookup.method, err.in_force_lookup.path) == (
        "POST",
        "/api/v1/legislation/as-of",
    )
    assert err.candidates == []
    assert err.candidates_truncated is False
    assert err.status == 404 and err.code == "NOT_FOUND"


def test_before_tracking_without_lookup():
    err = _raise_at_date(before_tracking_body(with_lookup=False))
    assert err.before_tracking is True
    assert err.in_force_lookup is None


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_boundary_unknown(mode):
    err = _raise_at_date(BOUNDARY_BODY, mode)
    assert err.reason == "boundary_unknown"
    assert err.boundary_unknown is True
    assert err.before_tracking is False
    assert err.tracked_since is None
    assert err.in_force_lookup is None
    first, second = err.candidates  # the non-object entry is skipped
    assert first == VersionCandidate(
        version=1,
        fetched_at="2026-05-02T10:00:00.000Z",
        observed_from=None,
        raw=BOUNDARY_BODY["error"]["details"]["candidates"][0],
    )
    assert (second.version, second.observed_from) == (2, "2026-05-20T10:00:00.000Z")
    assert err.candidates_truncated is True


def test_boundary_unknown_with_no_candidates():
    body = copy.deepcopy(BOUNDARY_BODY)
    body["error"]["details"] = {"reason": "boundary_unknown", "candidates": []}
    err = _raise_at_date(body)
    assert err.boundary_unknown is True
    assert err.candidates == []
    assert err.candidates_truncated is False


def test_not_found_without_details_has_no_reason():
    err = _raise_at_date({"success": False, "error": {"code": "NOT_FOUND", "message": "No such"}})
    assert err.reason is None
    assert (err.before_tracking, err.boundary_unknown, err.unknown_endpoint) == (
        False,
        False,
        False,
    )
    assert (err.tracked_since, err.in_force_lookup) == (None, None)
    assert (err.candidates, err.candidates_truncated) == ([], False)


@pytest.mark.parametrize(
    "details",
    [
        ["reason", "before_tracking"],
        "before_tracking",
        {"reason": 1, "trackedSince": 2, "candidates": "x", "candidatesTruncated": "true"},
    ],
)
def test_malformed_details_never_raise(details):
    err = NotFoundError("x", details=details)
    assert err.reason is None
    assert err.before_tracking is False and err.boundary_unknown is False
    assert err.tracked_since is None and err.in_force_lookup is None
    assert err.candidates == [] and err.candidates_truncated is False


def test_unknown_endpoint_is_unchanged():
    err = NotFoundError("Unknown API endpoint", details={"reason": "unknown_endpoint"})
    assert err.unknown_endpoint is True
    assert err.reason == "unknown_endpoint"
    assert err.before_tracking is False and err.boundary_unknown is False


def test_reason_from_a_newer_api_is_passed_through():
    err = NotFoundError("x", details={"reason": "some_new_reason"})
    assert err.reason == "some_new_reason"
    assert not (err.unknown_endpoint or err.before_tracking or err.boundary_unknown)


def test_not_found_details_survive_pickle():
    err = _raise_at_date(BOUNDARY_BODY)
    again = pickle.loads(pickle.dumps(err))
    assert again.boundary_unknown is True
    assert [c.version for c in again.candidates] == [1, 2]
