"""Point-in-time versions surface (requires lex-api PR #72 deployed).

Covers request shapes (paths + language param), typed history/snapshot
models, the /at/{date} response, and the honest pre-ingestion 404 mapping
to NotFoundError.
"""

import json

import httpx

from lexapi import (
    DocumentVersionResponse,
    DocumentVersionsResponse,
    LexAPI,
    NotFoundError,
)

VERSIONS_BODY = {
    "success": True,
    "celex": "32016R0679",
    "language": "en",
    "currentVersion": 3,
    "trackedSince": "2026-01-10T08:00:00.000Z",
    "semantics": "Versions are LexAPI observation snapshots…",
    "versions": [
        {
            "version": 3,
            "isCurrent": True,
            "contentHash": "hash-v3",
            "fetchedAt": "2026-06-01T09:00:00.000Z",
            "title": "GDPR (v3)",
            "documentType": "Regulation",
            "documentTypeCode": "regulation",
        },
        {
            "version": 2,
            "isCurrent": False,
            "contentHash": "hash-v2",
            "fetchedAt": "2026-03-15T08:00:00.000Z",
            "title": "GDPR (v2)",
            "documentType": "Regulation",
            "documentTypeCode": "regulation",
        },
    ],
}

SNAPSHOT_BODY = {
    "success": True,
    "celex": "32016R0679",
    "language": "en",
    "semantics": "Versions are LexAPI observation snapshots…",
    "document": {
        "version": 2,
        "isCurrent": False,
        "contentHash": "hash-v2",
        "fetchedAt": "2026-03-15T08:00:00.000Z",
        "title": "GDPR (v2)",
        "documentType": "Regulation",
        "documentTypeCode": "regulation",
        "parsedContent": {"articles": [{"number": "1", "text": "v2 text"}]},
    },
}


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


def test_list_versions_request_and_typed_history():
    client, seen = capture_client(VERSIONS_BODY)
    with client:
        res = client.list_document_versions("32016R0679", language="en")

    assert seen["method"] == "GET"
    assert seen["path"] == "/api/v1/documents/32016R0679/versions"
    assert seen["params"] == {"language": "en"}
    assert isinstance(res, DocumentVersionsResponse)
    assert res.current_version == 3
    assert res.tracked_since == "2026-01-10T08:00:00.000Z"
    assert "observation snapshots" in res.semantics
    assert [v.version for v in res.versions] == [3, 2]
    assert res.versions[0].is_current is True
    assert res.versions[1].content_hash == "hash-v2"
    # Listing is metadata-only — no content on entries.
    assert res.versions[0].parsed_content is None


def test_list_versions_omits_default_language_param():
    client, seen = capture_client(VERSIONS_BODY)
    with client:
        client.list_document_versions("32016R0679")
    assert seen["params"] == {}


def test_get_version_snapshot_with_content_and_header():
    client, seen = capture_client(SNAPSHOT_BODY, headers={"x-corpus-version": "2"})
    with client:
        res = client.get_document_version("32016R0679", 2)

    assert seen["path"] == "/api/v1/documents/32016R0679/versions/2"
    assert isinstance(res, DocumentVersionResponse)
    assert res.document.version == 2
    assert res.document.is_current is False
    assert res.document.parsed_content["articles"][0]["text"] == "v2 text"
    assert res.corpus_version == 2
    assert res.as_of is None  # not an /at response


def test_get_document_at_date():
    body = dict(SNAPSHOT_BODY, asOf="2026-03-20")
    client, seen = capture_client(body)
    with client:
        res = client.get_document_at_date("32016R0679", "2026-03-20")

    assert seen["path"] == "/api/v1/documents/32016R0679/at/2026-03-20"
    assert res.as_of == "2026-03-20"
    assert res.document.version == 2


def test_pre_ingestion_date_maps_to_not_found():
    error_body = {
        "success": False,
        "error": {
            "code": "NOT_FOUND",
            "message": (
                "32016R0679 (en) was not yet in the corpus on 2025-12-31 — "
                "tracking began 2026-01-10."
            ),
        },
    }
    client, _seen = capture_client(error_body, status=404)
    with client:
        try:
            client.get_document_at_date("32016R0679", "2025-12-31")
        except NotFoundError as exc:
            assert "tracking began 2026-01-10" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected NotFoundError")
