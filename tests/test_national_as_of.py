"""National legislation search with as_of (API 2.29.0): the request option,
the labels on the response and on each hit, and the national version model."""

import asyncio
import datetime as dt
import json

import httpx
import pytest

from lexapi import (
    NATIONAL_AS_OF_REASONS,
    AsOfVersion,
    AsyncLexAPI,
    InvalidParamsError,
    LexAPI,
    NationalAsOfVersion,
    NationalSemanticLegislationResponse,
    NationalSemanticLegislationResult,
    SemanticLegislationResponse,
)

LOOPBACK = "http://127.0.0.1:9/api/v1"

# Spec order, copied from openapi.yaml NationalAsOfReason.
SPEC_NATIONAL_AS_OF_REASONS = [
    "provision_amended_since",
    "no_longer_in_force",
    "provision_dates_unknown",
    "asof_not_available",
]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def hit(**extra):
    base = {
        "id": "chunk-1",
        "score": 0.81,
        "snippet": "…",
        "law_id": "AT-NOR-20000001",
        "documentId": "AT-NOR-20000001",
        "jurisdiction": "AT",
        "law_title": "Datenschutzgesetz",
        "article_ref": "§ 1",
        "section": None,
        "language": "de",
        "in_force": True,
        "valid_from": "2018-05-25",
        "valid_to": None,
        "law_type": "BG",
        "document": {"id": "AT-NOR-20000001", "jurisdiction": "AT", "documentKind": "act"},
        "attribution": "Source: RIS",
        "fieldProvenance": {"official_source_text": ["snippet"]},
    }
    base.update(extra)
    return base


# The docs' example (other hit fields filled with the shape every hit has).
RESOLVED = hit(
    asOf="2020-06-01",
    asOfResolved=True,
    asOfVersion={
        "kind": "current_unit",
        "validFrom": "2018-05-25",
        "validTo": None,
        "sourceId": "NOR40000001",
        "checkedAt": "2026-10-30",
    },
)
UNRESOLVED = hit(
    article_ref="§ 2",
    valid_from="2024-01-01",
    asOf="2020-06-01",
    asOfResolved=False,
    asOfReason="provision_amended_since",
)
AS_OF_BODY = {
    "success": True,
    "searchType": "semantic-legislation",
    "jurisdiction": "AT",
    "query": "Recht auf Auskunft über eigene Daten",
    "resultCount": 2,
    "results": [RESOLVED, UNRESOLVED],
    "asOf": "2020-06-01",
    "asOfSummary": {"liveFetches": 0, "unresolved": 1},
    "attribution": "Source: RIS",
}


def plain_body():
    body = {k: v for k, v in AS_OF_BODY.items() if k not in ("asOf", "asOfSummary")}
    body["results"] = [hit()]
    return body


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


def call(mode, status, body, **kwargs):
    seen: list[httpx.Request] = []
    if mode == "sync":
        with sync_client(status, body, seen) as client:
            resp = client.national.legislation_semantic_search("AT", "Auskunft", **kwargs)
    else:

        async def run():
            async with async_client(status, body, seen) as client:
                return await client.national.legislation_semantic_search("AT", "Auskunft", **kwargs)

        resp = asyncio.run(run())
    (request,) = seen
    return resp, request


# ── the request ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "as_of",
    [
        "2020-06-01",
        dt.date(2020, 6, 1),
        dt.datetime(2020, 6, 1, 23, 30, tzinfo=dt.timezone.utc),
    ],
)
def test_as_of_is_sent_as_asOf(mode, as_of):
    _, request = call(mode, 200, AS_OF_BODY, as_of=as_of, limit=2)
    assert request.method == "POST"
    assert request.url.path == "/api/v1/at/legislation/semantic"
    assert json.loads(request.content) == {
        "query": "Auskunft",
        "limit": 2,
        "asOf": "2020-06-01",
    }


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_without_as_of_the_body_is_unchanged(mode):
    _, request = call(mode, 200, plain_body(), include_text=True)
    assert json.loads(request.content) == {"query": "Auskunft", "include": ["text"]}


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_early_national_dates_are_not_validated_client_side(mode):
    # National dates start at 1800-01-01; the server validates the range.
    _, request = call(mode, 200, AS_OF_BODY, as_of="1811-06-01")
    assert json.loads(request.content)["asOf"] == "1811-06-01"


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_invalid_as_of_is_the_servers_400(mode):
    body = {"error": "Invalid asOf", "code": "INVALID_PARAMS"}
    with pytest.raises(InvalidParamsError):
        call(mode, 400, body, as_of="2020-13-40")


# ── the response ─────────────────────────────────────────────────────────


def test_response_echo_and_summary():
    resp = NationalSemanticLegislationResponse(AS_OF_BODY)
    assert resp.as_of == "2020-06-01"
    summary = resp.as_of_summary
    assert summary is not None
    assert (summary.live_fetches, summary.unresolved) == (0, 1)


def test_response_without_as_of():
    resp = NationalSemanticLegislationResponse(plain_body())
    assert resp.as_of is None and resp.as_of_summary is None
    (only,) = resp.results
    assert (only.as_of, only.as_of_resolved, only.as_of_reason, only.as_of_version) == (
        None,
        None,
        None,
        None,
    )


def test_resolved_hit_gets_the_national_version():
    resolved, _ = NationalSemanticLegislationResponse(AS_OF_BODY).results
    assert resolved.as_of == "2020-06-01"
    assert resolved.as_of_resolved is True
    assert resolved.as_of_reason is None
    version = resolved.as_of_version
    assert isinstance(version, NationalAsOfVersion)
    assert isinstance(version, AsOfVersion)  # existing isinstance checks keep working
    assert version.kind == "current_unit"
    assert version.valid_from == "2018-05-25"
    assert version.valid_to is None
    assert version.source_id == "NOR40000001"
    assert version.checked_at == "2026-10-30"
    # The EU fields do not apply to a national version.
    assert (version.celex, version.date, version.valid_until) == (None, None, None)
    assert version.raw == RESOLVED["asOfVersion"]


def test_unresolved_hit_keeps_todays_text_and_says_why():
    _, unresolved = NationalSemanticLegislationResponse(AS_OF_BODY).results
    assert unresolved.as_of_resolved is False
    assert unresolved.as_of_reason == "provision_amended_since"
    assert unresolved.as_of_version is None
    assert unresolved.valid_from == "2024-01-01"


def test_national_fields_still_parse_next_to_as_of():
    resolved, _ = NationalSemanticLegislationResponse(AS_OF_BODY).results
    assert isinstance(resolved, NationalSemanticLegislationResult)
    assert resolved.in_force is True  # snake_case wire name, about today
    assert resolved.document_id == "AT-NOR-20000001"
    assert resolved.jurisdiction == "AT"
    assert resolved.attribution == "Source: RIS"
    assert resolved.field_provenance is not None
    assert resolved.field_provenance.official_source_text == ["snippet"]


def test_camel_case_in_force_is_not_read_on_a_national_hit():
    (only,) = NationalSemanticLegislationResponse(
        dict(plain_body(), results=[hit(in_force=None, inForce=True)])
    ).results
    assert only.in_force is None


def test_national_version_fields_null_and_absent():
    (only,) = NationalSemanticLegislationResponse(
        dict(
            AS_OF_BODY,
            results=[
                hit(
                    asOfResolved=True,
                    asOfVersion={"kind": "current_unit", "validFrom": None, "sourceId": None},
                )
            ],
        )
    ).results
    version = only.as_of_version
    assert isinstance(version, NationalAsOfVersion)
    assert (version.valid_from, version.valid_to, version.source_id, version.checked_at) == (
        None,
        None,
        None,
        None,
    )


def test_unknown_version_kind_stays_a_plain_version_with_raw():
    raw = {"kind": "future_kind", "validFrom": "2018-05-25", "checkedAt": "2026-10-30"}
    (only,) = NationalSemanticLegislationResponse(
        dict(AS_OF_BODY, results=[hit(asOfResolved=True, asOfVersion=raw)])
    ).results
    version = only.as_of_version
    assert type(version) is AsOfVersion
    assert version.kind == "future_kind"
    assert version.raw == raw


def test_eu_hit_never_gets_the_national_version():
    body = {
        "success": True,
        "results": [
            {
                "law_id": "32016R0679",
                "asOfResolved": True,
                "asOfVersion": {"kind": "current_unit", "validFrom": "2018-05-25"},
            }
        ],
    }
    (only,) = SemanticLegislationResponse(body).results
    assert type(only.as_of_version) is AsOfVersion


def test_reasons_match_the_spec():
    assert list(NATIONAL_AS_OF_REASONS) == SPEC_NATIONAL_AS_OF_REASONS


def test_unknown_reason_is_passed_through():
    (only,) = NationalSemanticLegislationResponse(
        dict(AS_OF_BODY, results=[hit(asOfResolved=False, asOfReason="a_new_reason")])
    ).results
    assert only.as_of_reason == "a_new_reason"
