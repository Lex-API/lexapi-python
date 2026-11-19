"""In-force labels (API 2.19.0), provenance (API 2.20.0) and the semantic
result caps on /info (API 2.24.0) on the answers the SDK already models."""

import pytest

from lexapi import (
    Document,
    DocumentBatchResponse,
    DocumentMetadataResponse,
    DocumentResponse,
    FieldProvenance,
    InfoResponse,
    Provenance,
    SemanticLegislationResponse,
    SemanticSearchResponse,
    SubscriptionInfo,
)

PROVENANCE = {
    "publisher": "Publications Office of the European Union",
    "sourceUrl": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:31995L0046",
    "sourceIdentifier": "31995L0046",
    "retrievedAt": "2026-11-01T08:00:00.000Z",
    "contentHash": "9f2b7c0e",
    "versionIdentifier": "31995L0046",
    "language": "en",
    "licence": "EUR-Lex legal notice",
}
HIT_PROVENANCE = dict(PROVENANCE, retrievedAt=None, contentHash=None)
FIELD_PROVENANCE = {
    "official_source_text": ["title", "content.fullText", "content.articles[].content"],
    "machine_extracted_structure": ["documentType", "dateOfDocument", "content.articles"],
}

# Directive 95/46/EC, as the docs describe its label.
REPEALED = {
    "celex": "31995L0046",
    "title": "Directive 95/46/EC",
    "inForce": False,
    "notInForceReason": "no_longer_in_force",
    "endOfValidity": "2018-05-24",
    "version": 4,
    "fetchedAt": "2026-11-01T08:00:00.000Z",
    "provenance": PROVENANCE,
    "fieldProvenance": FIELD_PROVENANCE,
}


def test_document_in_force_label_and_provenance():
    doc = Document.from_dict(REPEALED)
    assert doc.in_force is False
    assert doc.not_in_force_reason == "no_longer_in_force"
    assert doc.end_of_validity == "2018-05-24"
    assert doc.entry_into_force is None
    assert doc.version == 4
    assert doc.provenance == Provenance(
        publisher="Publications Office of the European Union",
        source_url=PROVENANCE["sourceUrl"],
        source_identifier="31995L0046",
        retrieved_at="2026-11-01T08:00:00.000Z",
        content_hash="9f2b7c0e",
        version_identifier="31995L0046",
        language="en",
        licence="EUR-Lex legal notice",
        raw=PROVENANCE,
    )
    assert doc.field_provenance is not None
    assert doc.field_provenance.official_source_text == FIELD_PROVENANCE["official_source_text"]
    assert doc.field_provenance.machine_extracted_structure == [
        "documentType",
        "dateOfDocument",
        "content.articles",
    ]
    assert doc.field_provenance.editorial_enrichment == []


def test_not_yet_in_force_carries_entry_into_force():
    doc = Document.from_dict(
        {
            "celex": "32024R1689",
            "inForce": False,
            "notInForceReason": "not_yet_in_force",
            "entryIntoForce": "2030-01-01",
        }
    )
    assert (doc.in_force, doc.not_in_force_reason, doc.entry_into_force) == (
        False,
        "not_yet_in_force",
        "2030-01-01",
    )


def test_unknown_status_and_older_api_leave_everything_none():
    doc = Document.from_dict({"celex": "62018CJ0311"})
    assert (doc.in_force, doc.not_in_force_reason, doc.end_of_validity) == (None, None, None)
    assert (doc.entry_into_force, doc.version, doc.provenance, doc.field_provenance) == (
        None,
        None,
        None,
        None,
    )


def test_provenance_nulls_stay_none():
    prov = Provenance.from_dict({key: None for key in PROVENANCE})
    assert all(
        getattr(prov, name) is None
        for name in (
            "publisher",
            "source_url",
            "source_identifier",
            "retrieved_at",
            "content_hash",
            "version_identifier",
            "language",
            "licence",
        )
    )


def test_mistyped_fields_degrade_to_none():
    doc = Document.from_dict(
        {
            "inForce": "yes",
            "notInForceReason": 1,
            "endOfValidity": False,
            "version": "4",
            "provenance": "EUR-Lex",
            "fieldProvenance": ["title"],
        }
    )
    assert (doc.in_force, doc.not_in_force_reason, doc.end_of_validity, doc.version) == (
        None,
        None,
        None,
        None,
    )
    assert (doc.provenance, doc.field_provenance) == (None, None)
    mixed = FieldProvenance.from_dict({"official_source_text": ["title", 3, None]})
    assert mixed.official_source_text == ["title"]


@pytest.mark.parametrize(
    "wrap, cls, read",
    [
        (lambda d: {"success": True, "document": d}, DocumentResponse, lambda r: r.document),
        (
            lambda d: {"success": True, "metadata": d},
            DocumentMetadataResponse,
            lambda r: r.metadata,
        ),
        (
            lambda d: {"success": True, "documents": [d], "errors": []},
            DocumentBatchResponse,
            lambda r: r.documents[0],
        ),
    ],
)
def test_every_document_answer_carries_the_label(wrap, cls, read):
    doc = read(cls(wrap(REPEALED)))
    assert (doc.in_force, doc.end_of_validity) == (False, "2018-05-24")
    assert doc.provenance is not None and doc.provenance.licence == "EUR-Lex legal notice"


def legislation_body(**extra):
    body = {
        "success": True,
        "searchType": "semantic-legislation",
        "results": [
            {
                "law_id": "32016R0679",
                "article_ref": "art_17",
                "score": 0.9,
                "inForce": True,
                "provenance": dict(HIT_PROVENANCE, sourceIdentifier="32016R0679"),
                "fieldProvenance": {"official_source_text": ["snippet"]},
            },
            {
                "law_id": "31995L0046",
                "article_ref": "art_12",
                "score": 0.8,
                "inForce": False,
                "notInForceReason": "no_longer_in_force",
                "endOfValidity": "2018-05-24",
            },
            {"law_id": "11992M", "article_ref": "art_1"},
        ],
    }
    body.update(extra)
    return body


def test_legislation_hits_carry_the_label_and_provenance():
    in_force, repealed, unknown = SemanticLegislationResponse(legislation_body()).results
    assert in_force.in_force is True and in_force.not_in_force_reason is None
    assert in_force.provenance is not None
    assert (in_force.provenance.retrieved_at, in_force.provenance.content_hash) == (None, None)
    assert in_force.provenance.source_identifier == "32016R0679"
    assert in_force.field_provenance is not None
    assert in_force.field_provenance.official_source_text == ["snippet"]
    assert (repealed.in_force, repealed.not_in_force_reason, repealed.end_of_validity) == (
        False,
        "no_longer_in_force",
        "2018-05-24",
    )
    assert (unknown.in_force, unknown.provenance, unknown.field_provenance) == (None, None, None)


def test_in_force_filter_counts():
    resp = SemanticLegislationResponse(
        legislation_body(inForceFilter={"removedNotInForce": 2, "removedUnknown": 1}, hint="…")
    )
    counts = resp.in_force_filter
    assert counts is not None
    assert (counts.removed_not_in_force, counts.removed_unknown) == (2, 1)
    assert SemanticLegislationResponse(legislation_body()).in_force_filter is None


def test_case_law_hits_and_hyde_field_provenance():
    body = {
        "success": True,
        "hyde": True,
        "hypotheticalDocument": "A drafted passage",
        "fieldProvenance": {"machine_generated_interpretation": ["hypotheticalDocument"]},
        "results": [
            {
                "score": 0.7,
                "metadata": {"celex_id": "62018CJ0311", "document_type": "judgment"},
                "provenance": dict(HIT_PROVENANCE, sourceIdentifier="62018CJ0311"),
                "fieldProvenance": {
                    "official_source_text": ["snippet"],
                    "machine_generated_interpretation": ["score", "summary"],
                },
            },
            {"score": 0.6, "metadata": {"celex_id": "62014CJ0362"}},
        ],
    }
    resp = SemanticSearchResponse(body)
    assert resp.field_provenance is not None
    assert resp.field_provenance.machine_generated_interpretation == ["hypotheticalDocument"]
    first, second = resp.results
    assert first.provenance is not None and first.provenance.source_identifier == "62018CJ0311"
    assert first.field_provenance is not None
    assert first.field_provenance.machine_generated_interpretation == ["score", "summary"]
    assert (second.provenance, second.field_provenance) == (None, None)
    assert SemanticSearchResponse({"success": True, "results": []}).field_provenance is None


def test_info_semantic_result_caps():
    resp = InfoResponse(
        {
            "subscription": {
                "tier": "BUSINESS",
                "maxSemanticResults": 100,
                "maxSemanticLegislationResults": 50,
            }
        }
    )
    sub = resp.subscription_info
    assert sub is not None
    assert (sub.max_semantic_results, sub.max_semantic_legislation_results) == (100, 50)


def test_info_caps_zero_and_absent():
    zero = SubscriptionInfo.from_dict({"maxSemanticResults": 0, "maxSemanticLegislationResults": 0})
    assert zero is not None
    assert (zero.max_semantic_results, zero.max_semantic_legislation_results) == (0, 0)
    older = SubscriptionInfo.from_dict({"tier": "FREE"})
    assert older is not None
    assert (older.max_semantic_results, older.max_semantic_legislation_results) == (None, None)
