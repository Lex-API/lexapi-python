"""POST /legislation/as-of: request shape, typed answer, unresolved answers
and errors, on the sync and the async client."""

import asyncio
import datetime as dt
import json

import httpx
import pytest

from lexapi import (
    AS_OF_LOOKUP_REASONS,
    AsyncLexAPI,
    InvalidCelexError,
    InvalidParamsError,
    LegislationAsOfResponse,
    LexAPI,
    NotFoundError,
    __version__,
)

# Spec order, copied from openapi.yaml AsOfLookupReason.
SPEC_AS_OF_LOOKUP_REASONS = [
    "not_yet_adopted",
    "not_yet_in_force",
    "no_longer_in_force",
    "in_force_dates_unknown",
    "act_type_not_supported",
    "version_list_unavailable",
    "version_lookup_cap",
    "not_in_language",
    "amendment_not_consolidated",
    "text_unavailable",
    "article_not_in_version",
    "provision_ambiguous",
    "version_not_parsed",
    "fetch_budget_exhausted",
    "fetch_pending",
    "fetch_capacity",
    "fetch_failed",
    "fetch_timeout",
    "request_deadline",
    "insufficient_credits",
    "credit_check_failed",
]

NOTE = (
    "The act as amended, consolidated as of 2016-05-04; not the authentic text. "
    "Only the act as published in the Official Journal is authentic (32016R0679)."
)

ACT = {
    "celex": "32016R0679",
    "dateOfDocument": "2016-04-27",
    "entryIntoForce": "2016-05-24",
    "inForceToday": True,
}

CREDITS = {"operation_weight": 1, "current": 337, "limit": 150000, "remaining": 149663}


def provenance(celex, rendition="TXT/HTML"):
    return {
        "publisher": "Publications Office of the European Union",
        "sourceUrl": f"https://eur-lex.europa.eu/legal-content/EN/{rendition}/?uri=CELEX:{celex}",
        "sourceIdentifier": celex,
        "retrievedAt": None,
        "contentHash": None,
        "versionIdentifier": celex,
        "language": "en",
        "licence": "EUR-Lex legal notice",
    }


RESOLVED_BODY = {
    "success": True,
    "document": "32016R0679",
    "language": "en",
    "asOf": "2019-06-01",
    "asOfResolved": True,
    "asOfVersion": {
        "celex": "02016R0679-20160504",
        "kind": "consolidated",
        "date": "2016-05-04",
        "validUntil": "2025-12-31",
        "checkedAt": "2026-11-18T04:43:00.000Z",
    },
    "act": ACT,
    "provision": {
        "id": "art_17",
        "number": "Article 17",
        "title": "Right to erasure (‘right to be forgotten’)",
        "text": "1. The data subject shall have the right to obtain from the controller ...",
    },
    "consolidated": True,
    "consolidatedAsOf": "2016-05-04",
    "baseCelex": "32016R0679",
    "consolidatedNote": NOTE,
    "provenance": provenance("02016R0679-20160504"),
    "credits": CREDITS,
}

VERSION_ONLY_BODY = {key: value for key, value in RESOLVED_BODY.items() if key != "provision"}
VERSION_ONLY_BODY["asOfVersion"] = {
    "celex": "02016R0679-20160504",
    "kind": "consolidated",
    "date": "2016-05-04",
}
VERSION_ONLY_BODY["provenance"] = provenance("02016R0679-20160504", "ALL")

AS_PUBLISHED_BODY = {
    "success": True,
    "document": "32022R2065",
    "language": "en",
    "asOf": "2023-03-01",
    "asOfResolved": True,
    "asOfVersion": {"celex": "32022R2065", "kind": "as_published"},
    "act": {"celex": "32022R2065", "dateOfDocument": "2022-10-19", "inForceToday": True},
    "provision": {"id": "rct_1-2", "text": "(1) Information society services ..."},
    "provenance": provenance("32022R2065"),
    "credits": {**CREDITS, "operation_weight": 4},
}


def unresolved_body(reason, **extra):
    return {
        "success": True,
        "document": "32016R0679",
        "language": "en",
        "asOf": "2016-05-10",
        "asOfResolved": False,
        "asOfReason": reason,
        "act": ACT,
        "provenance": provenance("32016R0679", "ALL"),
        "credits": CREDITS,
        **extra,
    }


def error_envelope(code, message):
    return {"success": False, "error": {"code": code, "message": message}, "credits": CREDITS}


def capture_client(response_json, status=200, client_cls=LexAPI):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["calls"] = seen.get("calls", 0) + 1
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json)

    client = client_cls(api_key="lex_test_key", transport=httpx.MockTransport(handler))
    return client, seen


def call(response_json, *args, status=200, **kwargs):
    """Run the same call on the sync and the async client; both must agree."""
    client, seen = capture_client(response_json, status=status)
    with client:
        sync_result = client.legislation_as_of(*args, **kwargs)

    async def run():
        async_client, async_seen = capture_client(
            response_json, status=status, client_cls=AsyncLexAPI
        )
        async with async_client:
            return await async_client.legislation_as_of(*args, **kwargs), async_seen

    async_result, async_seen = asyncio.run(run())
    assert async_seen["body"] == seen["body"]
    assert async_seen["url"] == seen["url"]
    assert dict(async_result) == dict(sync_result)
    return sync_result, async_result, seen


def call_raises(error_cls, response_json, status, *args, **kwargs):
    client, seen = capture_client(response_json, status=status)
    with client:
        with pytest.raises(error_cls) as info:
            client.legislation_as_of(*args, **kwargs)

    async def run():
        async_client, async_seen = capture_client(
            response_json, status=status, client_cls=AsyncLexAPI
        )
        async with async_client:
            with pytest.raises(error_cls) as async_info:
                await async_client.legislation_as_of(*args, **kwargs)
        return async_info, async_seen

    async_info, async_seen = asyncio.run(run())
    assert type(async_info.value) is type(info.value)
    assert async_info.value.code == info.value.code
    # Never retried: one request each.
    assert seen["calls"] == 1 and async_seen["calls"] == 1
    return info.value


def test_lookup_reasons_match_spec():
    assert list(AS_OF_LOOKUP_REASONS) == SPEC_AS_OF_LOOKUP_REASONS
    assert len(AS_OF_LOOKUP_REASONS) == 21


# ── Request ─────────────────────────────────────────────────────────────


def test_request_body_and_headers_exactly_as_sent():
    _, _, seen = call(RESOLVED_BODY, "32016R0679", "2019-06-01", provision="art_17", language="en")
    assert seen["method"] == "POST"
    assert seen["url"] == "https://lex-api.com/api/v1/legislation/as-of"
    assert seen["body"] == {
        "document": "32016R0679",
        "asOf": "2019-06-01",
        "provision": "art_17",
        "language": "en",
    }
    headers = seen["headers"]
    assert headers["x-api-key"] == "lex_test_key"
    assert headers["user-agent"] == f"lexapi-python/{__version__}"
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "application/json"


def test_optional_fields_are_omitted():
    _, _, seen = call(VERSION_ONLY_BODY, "32016R0679", "2019-06-01")
    assert seen["body"] == {"document": "32016R0679", "asOf": "2019-06-01"}


def test_as_of_accepts_date_and_datetime():
    for value in (dt.date(2019, 6, 1), dt.datetime(2019, 6, 1, 23, 59)):
        _, _, seen = call(VERSION_ONLY_BODY, "32016R0679", value)
        assert seen["body"]["asOf"] == "2019-06-01"


@pytest.mark.parametrize(
    ("document", "provision"),
    [
        ("32016r0679", "Art. 5"),
        ("CELEX:32016R0679", "Annex III"),
        ("02016R0679-20160504", "17"),
        ("12016E/TXT", "rct_26-28"),
        (" 32016R0679 ", "Article 6a"),
    ],
)
def test_inputs_are_sent_unchanged(document, provision):
    # Validation is the API's: the SDK neither rewrites nor rejects an id.
    _, _, seen = call(RESOLVED_BODY, document, "2019-06-01", provision=provision)
    assert seen["body"]["document"] == document
    assert seen["body"]["provision"] == provision


# ── Resolved ────────────────────────────────────────────────────────────


def test_resolved_with_provision():
    for resp in call(RESOLVED_BODY, "32016R0679", "2019-06-01", provision="art_17")[:2]:
        assert isinstance(resp, LegislationAsOfResponse)
        assert resp.document == "32016R0679"
        assert resp.language == "en"
        assert resp.as_of == "2019-06-01"
        assert resp.as_of_resolved is True
        assert resp.as_of_reason is None

        version = resp.as_of_version
        assert version.celex == "02016R0679-20160504"
        assert version.kind == "consolidated"
        assert version.date == "2016-05-04"
        assert version.valid_until == "2025-12-31"
        assert version.checked_at == "2026-11-18T04:43:00.000Z"
        assert version.raw == RESOLVED_BODY["asOfVersion"]

        assert resp.act.celex == "32016R0679"
        assert resp.act.date_of_document == "2016-04-27"
        assert resp.act.entry_into_force == "2016-05-24"
        assert resp.act.end_of_validity is None
        assert resp.act.in_force_today is True
        assert resp.act.raw == ACT

        assert resp.provision.id == "art_17"
        assert resp.provision.number == "Article 17"
        assert resp.provision.title == "Right to erasure (‘right to be forgotten’)"
        assert resp.provision.text.startswith("1. The data subject")
        assert resp.provision.raw == RESOLVED_BODY["provision"]

        assert resp.consolidated is True
        assert resp.consolidated_as_of == "2016-05-04"
        assert resp.base_celex == "32016R0679"
        assert resp.consolidated_note == NOTE
        assert resp.provenance["sourceIdentifier"] == "02016R0679-20160504"
        assert resp.units_charged == 1
        assert resp.credits_remaining == 149663
        assert resp["asOfVersion"]["celex"] == "02016R0679-20160504"  # raw body kept


def test_resolved_version_only():
    for resp in call(VERSION_ONLY_BODY, "32016R0679", "2019-06-01")[:2]:
        assert resp.as_of_resolved is True
        assert resp.provision is None
        assert resp.as_of_version.kind == "consolidated"
        assert resp.as_of_version.valid_until is None
        assert resp.as_of_version.checked_at is None
        assert resp.consolidated is True
        assert resp.base_celex == "32016R0679"


def test_resolved_as_published():
    for resp in call(AS_PUBLISHED_BODY, "32022R2065", "2023-03-01", provision="rct_1-2")[:2]:
        assert resp.as_of_resolved is True
        assert resp.as_of_version.celex == "32022R2065"
        assert resp.as_of_version.kind == "as_published"
        assert resp.as_of_version.date is None
        # The act as published is the authentic text: no consolidated labels.
        assert resp.consolidated is False
        assert resp.consolidated_as_of is None
        assert resp.base_celex is None
        assert resp.consolidated_note is None
        assert resp.provision.id == "rct_1-2"
        assert resp.provision.number is None
        assert resp.provision.title is None
        assert resp.provision.text.startswith("(1)")
        assert resp.act.entry_into_force is None
        assert resp.units_charged == 4


# ── Unresolved: a normal return ─────────────────────────────────────────


@pytest.mark.parametrize(
    "reason", ["not_yet_in_force", "amendment_not_consolidated", "act_type_not_supported"]
)
def test_unresolved_is_returned_not_raised(reason):
    for resp in call(unresolved_body(reason), "32016R0679", "2016-05-10")[:2]:
        assert isinstance(resp, LegislationAsOfResponse)
        assert resp.as_of_resolved is False
        assert resp.as_of_reason == reason
        assert resp.as_of_reason in AS_OF_LOOKUP_REASONS
        assert resp.as_of_version is None
        assert resp.provision is None
        assert resp.consolidated is False
        assert resp.base_celex is None
        assert resp.act.celex == "32016R0679"
        assert resp.act.in_force_today is True
        assert resp.provenance["sourceIdentifier"] == "32016R0679"
        assert resp.units_charged == 1


def test_unresolved_with_version_present():
    body = unresolved_body(
        "article_not_in_version",
        asOfVersion={"celex": "02016R0679-20160504", "kind": "consolidated", "date": "2016-05-04"},
    )
    for resp in call(body, "32016R0679", "2019-06-01", provision="art_999")[:2]:
        assert resp.as_of_resolved is False
        assert resp.as_of_reason == "article_not_in_version"
        assert resp.as_of_version.celex == "02016R0679-20160504"
        assert resp.provision is None
        assert resp.consolidated is False  # labels come with a resolved answer only


def test_unknown_future_reason_passes_through():
    for resp in call(unresolved_body("brand_new_reason"), "32016R0679", "2016-05-10")[:2]:
        assert resp.as_of_resolved is False
        assert resp.as_of_reason == "brand_new_reason"
        assert resp.as_of_reason not in AS_OF_LOOKUP_REASONS


@pytest.mark.parametrize(
    "body",
    [
        {"success": True},
        {"success": True, "asOfResolved": False, "asOfVersion": None, "provision": None},
        {"success": True, "asOfResolved": True, "act": None, "provenance": None},
        {"success": True, "asOfResolved": "true", "asOfVersion": "x", "provision": [], "act": 3},
        {"success": True, "asOfResolved": True, "asOfVersion": {}, "provision": {}, "act": {}},
    ],
)
def test_missing_or_null_objects_do_not_crash(body):
    for resp in call(body, "32016R0679", "2019-06-01")[:2]:
        # Resolved only on a literal true.
        assert resp.as_of_resolved is (body.get("asOfResolved") is True)
        assert resp.as_of_reason is None
        assert resp.document is None
        assert resp.consolidated is False
        assert resp.units_charged == 0
        if isinstance(body.get("asOfVersion"), dict):
            assert resp.as_of_version.celex is None
            assert resp.as_of_version.valid_until is None
            assert resp.provision.text is None
            assert resp.act.in_force_today is None
        else:
            assert resp.as_of_version is None
            assert resp.provision is None
            assert resp.act is None
        assert resp.provenance is None


# ── Errors ──────────────────────────────────────────────────────────────


def test_unknown_act_is_not_found():
    err = call_raises(
        NotFoundError,
        error_envelope("NOT_FOUND", "No EU act with the CELEX number 32016R9999 was found."),
        404,
        "32016R9999",
        "2019-06-01",
    )
    assert err.status == 404
    assert err.code == "NOT_FOUND"
    assert "32016R9999" in err.message


def test_invalid_as_of_is_invalid_params():
    err = call_raises(
        InvalidParamsError,
        error_envelope("INVALID_PARAMS", "asOf cannot be in the future"),
        400,
        "32016R0679",
        "2999-01-01",
    )
    assert err.status == 400
    assert err.code == "INVALID_PARAMS"
    assert err.message == "asOf cannot be in the future"


def test_invalid_document_is_invalid_celex():
    err = call_raises(
        InvalidCelexError,
        error_envelope("INVALID_CELEX", '"62014CJ0362" is case law.'),
        400,
        "62014CJ0362",
        "2019-06-01",
    )
    assert err.status == 400
    assert err.code == "INVALID_CELEX"


def test_unknown_route_on_an_older_api_is_not_found():
    # An API older than 2.26.0 has no such route; it answers like any other
    # unknown path, and the SDK raises what it raises for every 404.
    err = call_raises(
        NotFoundError,
        error_envelope(
            "NOT_FOUND",
            "Unknown API endpoint: POST /api/v1/legislation/as-of. "
            "See https://lex-api.com/docs for the endpoint reference.",
        ),
        404,
        "32016R0679",
        "2019-06-01",
    )
    assert err.status == 404
    assert err.message.startswith("Unknown API endpoint")
