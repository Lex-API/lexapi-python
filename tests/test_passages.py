"""lexapi.passages: cited passages from search, document and as-of responses.

Every text below is synthetic. No test opens a socket: clients are built by
``offline_client``, which only accepts a loopback base URL and a mock
transport, and the real httpx transports are patched to fail.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import dataclasses
import datetime as dt
import pathlib
import random
import re
import subprocess
import sys

import httpx
import pytest

from lexapi import AsyncLexAPI, LexAPI, passages
from lexapi.models import (
    DocumentResponse,
    LegislationAsOfResponse,
    SemanticLegislationResponse,
    SemanticSearchResponse,
)
from lexapi.passages import Passage, PassageResult

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOPBACK = "http://127.0.0.1:9/api/v1"

SYN = "[SYNTHETIC FIXTURE - not the text of the act] "
NOTE = (
    "The act as amended, consolidated as of 2099-01-01; not the authentic text. "
    "Only the act as published in the Official Journal is authentic (32099R0001)."
)
URL_CONS = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02099R0001-20990101"
URL_BASE = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32099R0001"
CREDITS = {"operation_weight": 9, "remaining": 991}


# ── offline harness ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def offline_client(body, cls=LexAPI, base_url=LOOPBACK):
    """A client that answers every request with ``body`` from a mock
    transport. Refuses any base URL that is not loopback."""
    if not base_url.startswith("http://127.0.0.1:"):
        raise RuntimeError(f"Offline tests refuse the base URL {base_url!r}")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, json=body)

    client = cls(
        "lex_offline_test_key",
        base_url=base_url,
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    assert str(client._http.base_url).startswith("http://127.0.0.1:")
    return client


def test_offline_client_refuses_a_non_loopback_base_url():
    with pytest.raises(RuntimeError, match="refuse the base URL"):
        offline_client({}, base_url="https://lex-api.com/api/v1")
    with pytest.raises(RuntimeError, match="refuse the base URL"):
        offline_client({}, base_url="http://127.0.0.1.example.com/api/v1")


def test_real_transport_is_blocked():
    with LexAPI("lex_offline_test_key", base_url=LOOPBACK, max_retries=0) as client:
        with pytest.raises(RuntimeError, match="Offline tests blocked"):
            client.get_info()


# ── fixture bodies ──────────────────────────────────────────────────────


def hit_consolidated():
    """Resolved; text swapped to the consolidated version listed for the date."""
    return {
        "id": "asof:02099R0001-20990101:art_17",
        "law_id": "02099R0001-20990101",
        "law_title": "Regulation (EU) 2099/1 (synthetic)",
        "article_ref": "art_17",
        "language": "en",
        "score": 0.91,
        "text": SYN + "consolidated article seventeen",
        "snippet": SYN + "consolidated article",
        "consolidated": True,
        "consolidatedAsOf": "2099-01-01",
        "baseCelex": "32099R0001",
        "consolidatedNote": NOTE,
        "asOf": "2099-06-01",
        "asOfResolved": True,
        "asOfVersion": {
            "celex": "02099R0001-20990101",
            "date": "2099-01-01",
            "kind": "consolidated",
        },
        "provenance": {"sourceUrl": URL_CONS, "retrievedAt": None},
    }


def hit_as_published():
    """Resolved as the act as published."""
    return {
        "id": "o-32099R0002-art_5",
        "law_id": "32099R0002",
        "law_title": "Regulation (EU) 2099/2 (synthetic)",
        "article_ref": "art_5",
        "language": "en",
        "score": 0.8,
        "text": SYN + "as published article five",
        "snippet": SYN + "as published",
        "asOf": "2099-06-01",
        "asOfResolved": True,
        "asOfVersion": {"celex": "32099R0002", "date": None, "kind": "as_published"},
    }


def hit_unresolved():
    """Unresolved: the API still sends the latest indexed text."""
    return {
        "id": "o-32099L0003-art_12",
        "law_id": "32099L0003",
        "law_title": "Directive (EU) 2099/3 (synthetic)",
        "article_ref": "art_12",
        "language": "en",
        "score": 0.75,
        "text": SYN + "LATEST-TEXT-MUST-NOT-LEAK",
        "snippet": SYN + "LATEST-SNIPPET-MUST-NOT-LEAK",
        "asOf": "2099-06-01",
        "asOfResolved": False,
        "asOfReason": "fetch_timeout",
    }


def legislation_body(*hits, as_of="2099-06-01", **extra):
    body = {
        "success": True,
        "searchType": "semantic-legislation",
        "query": "synthetic",
        "resultCount": len(hits),
        "results": list(hits),
        "credits": dict(CREDITS),
    }
    if as_of is not None:
        body["asOf"] = as_of
        body["asOfSummary"] = {"liveFetches": 1, "unresolved": 1}
    body.update(extra)
    return body


def leg(*hits, **extra):
    return SemanticLegislationResponse(legislation_body(*hits, **extra))


def without_as_of(hit):
    return {k: v for k, v in hit.items() if not k.startswith("asOf")}


CASE_HIT = {
    "case_id": "snapshot-1",
    "score": 0.83,
    "case_name": "Synthetic v Fixture",
    "case_number": "C-999/99",
    "ecli": "ECLI:EU:C:2099:1",
    "court": "Court of Justice",
    "decision_date": "2099-05-13",
    "language": "en",
    "summary": SYN + "MACHINE-WRITTEN-SUMMARY",
    "snippet": SYN + "case snippet",
    "text": SYN + "full text of the decision",
    "metadata": {"celex_id": "62099CJ9901", "document_type": "judgment", "paragraph_number": 93},
    "provenance": {
        "sourceUrl": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:62099CJ9901"
    },
}


def case_body(*hits, **extra):
    body = {"success": True, "searchType": "semantic", "results": list(hits)}
    body.update(extra)
    return body


def document_body(celex="32099R0001", **doc):
    document = {
        "celex": celex,
        "title": "Regulation (EU) 2099/1 (synthetic)",
        "language": "en",
        "eli": "http://data.europa.eu/eli/reg/2099/1/oj",
        "content": {
            "articles": [
                {
                    "id": "art_1",
                    "number": "Article 1",
                    "title": "Subject matter",
                    "content": SYN + "article one",
                },
                {"id": "art_6a", "number": "Article 6a", "content": SYN + "article six a"},
            ]
        },
        "urls": {"html": "https://eur-lex.europa.eu/html-page", "pdf": "https://eur-lex/pdf"},
        "provenance": {"sourceUrl": URL_BASE},
    }
    document.update(doc)
    return {"success": True, "document": document, "credits": {"operation_weight": 1}}


def as_of_body(**overrides):
    body = {
        "success": True,
        "document": "32099R0001",
        "language": "en",
        "asOf": "2099-06-01",
        "asOfResolved": True,
        "asOfVersion": {
            "celex": "02099R0001-20990101",
            "kind": "consolidated",
            "date": "2099-01-01",
            "validUntil": "2099-12-31",
        },
        "provision": {
            "id": "art_17",
            "number": "Article 17",
            "title": "Synthetic title",
            "text": SYN + "provision text",
        },
        "consolidated": True,
        "consolidatedAsOf": "2099-01-01",
        "baseCelex": "32099R0001",
        "consolidatedNote": NOTE,
        "provenance": {"sourceUrl": URL_CONS},
        "credits": {"operation_weight": 4, "remaining": 96},
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not ...}


def unresolved_as_of_body(**overrides):
    fields = {
        "asOfResolved": False,
        "asOfReason": "amendment_not_consolidated",
        "asOfVersion": ...,
        "provision": ...,
        "consolidated": ...,
        "consolidatedAsOf": ...,
        "baseCelex": ...,
        "consolidatedNote": ...,
    }
    fields.update(overrides)
    return as_of_body(**fields)


def strings_of(obj):
    """Every string a passage or a result can hand on."""
    if isinstance(obj, PassageResult):
        out = [repr(obj)]
        for passage in obj.passages:
            out.extend(strings_of(passage))
        return out
    values = [v for v in dataclasses.asdict(obj).values() if isinstance(v, str)]
    return [*values, obj.content(), passages.format_citation(obj), repr(obj), str(obj)]


def assert_no_leak(result, *needles):
    for value in strings_of(result):
        for needle in needles:
            assert needle not in value


# ── legislation search: resolved ────────────────────────────────────────


def test_resolved_consolidated_hit_carries_text_and_the_act_to_cite():
    result = passages.from_legislation_search(leg(hit_consolidated()))
    (p,) = result.passages
    assert p.kind == "legislation"
    assert p.text == SYN + "consolidated article seventeen"
    assert p.snippet == SYN + "consolidated article"
    assert p.text_status == "provided"
    assert p.text_withheld is False
    assert p.celex == "02099R0001-20990101"
    assert p.consolidated is True
    assert p.base_celex == "32099R0001"
    assert p.cite_celex == "32099R0001"
    assert p.consolidated_as_of == "2099-01-01"
    assert p.consolidated_note == NOTE
    assert p.article_ref == "art_17"
    assert p.title == "Regulation (EU) 2099/1 (synthetic)"
    assert p.language == "en"
    assert p.url == URL_CONS
    assert p.score == 0.91
    assert (p.as_of, p.as_of_resolved, p.as_of_reason) == ("2099-06-01", True, None)
    assert (p.version_celex, p.version_kind, p.version_date) == (
        "02099R0001-20990101",
        "consolidated",
        "2099-01-01",
    )
    assert p.content() == SYN + "consolidated article seventeen"
    assert (result.withheld, result.kept_labelled, result.dropped) == (0, 0, 0)
    assert result.as_of == "2099-06-01"


def test_resolved_as_published_hit():
    (p,) = passages.from_legislation_search(leg(hit_as_published())).passages
    assert p.text == SYN + "as published article five"
    assert p.consolidated is False
    assert p.base_celex is None
    assert p.cite_celex == p.celex == "32099R0002"
    assert (p.version_celex, p.version_kind, p.version_date) == ("32099R0002", "as_published", None)
    assert p.as_of_resolved is True
    assert p.url is None  # the API sent none; none is built


def test_search_without_a_date_has_no_as_of_fields():
    hit = without_as_of(hit_consolidated())
    result = passages.from_legislation_search(leg(hit, as_of=None))
    (p,) = result.passages
    assert p.text == SYN + "consolidated article seventeen"
    assert (p.as_of, p.as_of_resolved, p.as_of_reason) == (None, None, None)
    assert (p.version_celex, p.version_kind, p.version_date) == (None, None, None)
    assert p.text_status == "provided"
    assert result.as_of is None


def test_consolidated_fields_are_none_on_an_act_as_published():
    hit = without_as_of(hit_as_published())
    hit["consolidatedNote"] = NOTE  # not a consolidated hit: not carried
    hit["baseCelex"] = "32099R0009"
    (p,) = passages.from_legislation_search(leg(hit, as_of=None)).passages
    assert p.consolidated is False
    assert (p.base_celex, p.consolidated_as_of, p.consolidated_note) == (None, None, None)
    assert p.cite_celex == "32099R0002"


def test_consolidated_celex_is_consolidated_even_without_the_label():
    hit = without_as_of(hit_consolidated())
    for key in ("consolidated", "consolidatedAsOf", "baseCelex", "consolidatedNote"):
        del hit[key]
    (p,) = passages.from_legislation_search(leg(hit, as_of=None)).passages
    assert p.consolidated is True
    assert p.base_celex is None  # not sent, so not derived from the number
    assert p.cite_celex == "02099R0001-20990101"
    assert passages.format_citation(p) == (
        "CELEX 02099R0001-20990101, art_17, "
        f"consolidated text 02099R0001-20990101 (no legal force), {URL_CONS}"
    )


def test_version_kind_consolidated_marks_the_passage_consolidated():
    hit = hit_as_published()
    hit["asOfVersion"] = {"celex": "32099R0002", "date": "2099-01-01", "kind": "consolidated"}
    (p,) = passages.from_legislation_search(leg(hit)).passages
    assert p.consolidated is True


@pytest.mark.parametrize("ref", ["anx_III", "anx_A", "rct_26-28", "rct_26", "art_6a"])
def test_non_article_refs_pass_through(ref):
    hit = hit_as_published()
    hit["article_ref"] = ref
    (p,) = passages.from_legislation_search(leg(hit)).passages
    assert p.article_ref == ref
    assert passages.format_citation(p) == f"CELEX 32099R0002, {ref}"


def test_missing_optional_fields_are_none():
    result = passages.from_legislation_search(
        SemanticLegislationResponse({"results": [{"law_id": "32099R0002"}]})
    )
    (p,) = result.passages
    assert p == Passage(kind="legislation", celex="32099R0002", text=None, text_status="absent")
    assert p.content() == ""
    assert passages.format_citation(p) == "CELEX 32099R0002"
    assert result == PassageResult(passages=(p,))
    assert result.units_charged is None
    assert result.credits_remaining is None


def test_hit_without_celex_is_not_a_passage():
    hit = hit_as_published()
    del hit["law_id"]
    result = passages.from_legislation_search(leg(hit, hit_consolidated()))
    assert [p.celex for p in result.passages] == ["02099R0001-20990101"]
    assert result.uncitable == 1


@pytest.mark.parametrize("body", [{}, {"results": []}, {"results": None}, None, []])
def test_empty_legislation_results(body):
    result = passages.from_legislation_search(SemanticLegislationResponse(body))
    assert result == PassageResult(passages=())


# ── legislation search: unresolved ──────────────────────────────────────


def test_unresolved_hit_is_kept_without_its_text_by_default():
    result = passages.from_legislation_search(leg(hit_consolidated(), hit_unresolved()))
    assert len(result.passages) == 2
    p = result.passages[1]
    assert p.text is None
    assert p.snippet is None
    assert p.text_status == "withheld"
    assert p.text_withheld is True
    assert p.celex == "32099L0003"
    assert p.article_ref == "art_12"
    assert p.title == "Directive (EU) 2099/3 (synthetic)"
    assert (p.as_of, p.as_of_resolved, p.as_of_reason) == ("2099-06-01", False, "fetch_timeout")
    assert p.content() == (
        "[Text withheld: not established as the text in force on 2099-06-01 "
        "(reason: fetch_timeout).]"
    )
    assert (result.withheld, result.kept_labelled, result.dropped) == (1, 0, 0)
    assert_no_leak(result, "MUST-NOT-LEAK")
    # the resolved hit next to it is untouched
    assert result.passages[0].text == SYN + "consolidated article seventeen"


def test_default_policy_is_withhold():
    response = leg(hit_unresolved())
    assert passages.from_legislation_search(response) == passages.from_legislation_search(
        response, unresolved="withhold"
    )


def test_unresolved_hit_dropped_on_request_and_counted():
    result = passages.from_legislation_search(
        leg(hit_consolidated(), hit_unresolved(), hit_unresolved()), unresolved="drop"
    )
    assert [p.celex for p in result.passages] == ["02099R0001-20990101"]
    assert (result.withheld, result.kept_labelled, result.dropped) == (0, 0, 2)
    assert_no_leak(result, "MUST-NOT-LEAK")


def test_unresolved_text_kept_with_a_label_only_on_request():
    result = passages.from_legislation_search(
        leg(hit_consolidated(), hit_unresolved()), unresolved="keep_labelled"
    )
    p = result.passages[1]
    assert p.text == SYN + "LATEST-TEXT-MUST-NOT-LEAK"
    assert p.snippet == SYN + "LATEST-SNIPPET-MUST-NOT-LEAK"
    assert p.text_status == "not_established_for_date"
    assert p.text_withheld is False
    assert (p.as_of_resolved, p.as_of_reason) == (False, "fetch_timeout")
    assert p.content() == (
        "[Latest indexed text; not established as the text in force on 2099-06-01 "
        "(reason: fetch_timeout).]\n" + SYN + "LATEST-TEXT-MUST-NOT-LEAK"
    )
    assert (result.withheld, result.kept_labelled, result.dropped) == (0, 1, 0)
    assert result.passages[0].text_status == "provided"


def test_keep_labelled_marks_a_hit_that_has_only_a_snippet():
    hit = hit_unresolved()
    del hit["text"]
    result = passages.from_legislation_search(leg(hit), unresolved="keep_labelled")
    (p,) = result.passages
    assert p.text_status == "not_established_for_date"
    assert p.snippet == SYN + "LATEST-SNIPPET-MUST-NOT-LEAK"
    assert p.content() == ""
    assert result.kept_labelled == 1


def test_unknown_policy_is_refused():
    with pytest.raises(ValueError, match="unresolved must be one of"):
        passages.from_legislation_search(leg(), unresolved="keep")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unresolved must be one of"):
        passages.from_legislation_as_of(
            LegislationAsOfResponse(as_of_body()),
            unresolved="latest",  # type: ignore[arg-type]
        )


def test_notice_without_a_reason_or_a_date():
    hit = hit_unresolved()
    del hit["asOfReason"]
    (p,) = passages.from_legislation_search(leg(hit)).passages
    assert p.content() == ("[Text withheld: not established as the text in force on 2099-06-01.]")
    hit = {"law_id": "32099L0003", "text": "LATEST-TEXT-MUST-NOT-LEAK", "asOfResolved": False}
    (p,) = passages.from_legislation_search(leg(hit, as_of=None)).passages
    assert p.as_of is None
    assert p.content() == (
        "[Text withheld: not established as the text in force on the date asked.]"
    )


@pytest.mark.parametrize(
    "resolved",
    [False, None, "true", "false", 1, 0, "yes", [], {}],
    ids=repr,
)
def test_only_a_true_as_of_resolved_passes_text_on(resolved):
    hit = hit_unresolved()
    hit["asOfResolved"] = resolved
    result = passages.from_legislation_search(leg(hit))
    (p,) = result.passages
    assert p.text is None
    assert p.as_of_resolved is False
    assert result.withheld == 1
    assert_no_leak(result, "MUST-NOT-LEAK")


def test_hit_without_as_of_labels_on_a_dated_response_is_withheld():
    hit = without_as_of(hit_unresolved())
    result = passages.from_legislation_search(leg(hit))  # response echoes asOf
    assert result.passages[0].text is None
    assert result.passages[0].as_of == "2099-06-01"
    assert_no_leak(result, "MUST-NOT-LEAK")


def test_reason_alone_marks_a_hit_unresolved():
    hit = without_as_of(hit_unresolved())
    hit["asOfReason"] = "fetch_failed"
    result = passages.from_legislation_search(leg(hit, as_of=None))
    assert result.passages[0].text_status == "withheld"
    assert_no_leak(result, "MUST-NOT-LEAK")


def test_the_date_sent_is_checked_against_each_hit():
    # The API ignored the date: nothing in the response mentions it.
    plain = without_as_of(hit_unresolved())
    result = passages.from_legislation_search(leg(plain, as_of=None), as_of="2099-06-01")
    (p,) = result.passages
    assert p.text is None
    assert (p.as_of, p.as_of_resolved) == ("2099-06-01", False)
    assert result.as_of == "2099-06-01"
    assert_no_leak(result, "MUST-NOT-LEAK")

    # Resolved, but for another date than the one sent.
    other = hit_unresolved()
    other.update(asOfResolved=True, asOf="2098-01-01")
    result = passages.from_legislation_search(leg(other, as_of="2098-01-01"), as_of="2099-06-01")
    assert result.passages[0].text is None
    assert result.passages[0].as_of_resolved is False
    assert_no_leak(result, "MUST-NOT-LEAK")

    # Resolved for the date sent, given as a date object.
    same = passages.from_legislation_search(leg(hit_as_published()), as_of=dt.date(2099, 6, 1))
    assert same.passages[0].text == SYN + "as published article five"
    assert same.passages[0].as_of == "2099-06-01"
    stamp = dt.datetime(2099, 6, 1, 12, 30)
    same = passages.from_legislation_search(leg(hit_as_published()), as_of=stamp)
    assert same.passages[0].as_of_resolved is True


def _random_hit(rng, index):
    """A hit with a random mix of as-of labels and a text unique to it."""
    token = f"TOKEN-{index}-{rng.randrange(10**9)}"
    hit = {"law_id": rng.choice(["32099R0002", "02099R0001-20990101", "32099L0003"])}
    for key in ("text", "snippet"):
        if rng.random() < 0.8:
            hit[key] = f"{SYN}{key} {token}"
    if rng.random() < 0.7:
        hit["asOfResolved"] = rng.choice([True, True, False, None, "true", 1, 0, "false"])
    if rng.random() < 0.6:
        hit["asOf"] = rng.choice(["2099-06-01", "2098-01-01", None, 20990601])
    if rng.random() < 0.4:
        hit["asOfReason"] = rng.choice(["fetch_timeout", "not_in_language", "new_reason", None])
    if rng.random() < 0.4:
        hit["asOfVersion"] = {"celex": hit["law_id"], "kind": "as_published"}
    if rng.random() < 0.3:
        hit["article_ref"] = rng.choice(["art_1", "anx_III", "rct_1-3"])
    if rng.random() < 0.2:
        hit["metadata"] = {"text": f"{SYN}metadata {token}"}
    return hit, token


def test_unresolved_text_never_leaks_under_the_default_policy_property():
    rng = random.Random(20991118)
    passed_on = withheld = 0
    for _ in range(600):
        response_as_of = rng.choice(["2099-06-01", "2099-06-01", None])
        sent = rng.choice([None, None, "2099-06-01"])
        pairs = [_random_hit(rng, i) for i in range(rng.randrange(0, 7))]
        body = legislation_body(*(hit for hit, _ in pairs), as_of=response_as_of)
        snapshot = copy.deepcopy(body)
        result = passages.from_legislation_search(SemanticLegislationResponse(body), as_of=sent)
        dropped = passages.from_legislation_search(
            SemanticLegislationResponse(body), as_of=sent, unresolved="drop"
        )
        assert body == snapshot  # the response is not modified
        assert len(result.passages) == len(pairs)

        for (hit, token), passage in zip(pairs, result.passages):
            echoed = hit.get("asOf") if isinstance(hit.get("asOf"), str) else response_as_of
            asked = (
                sent is not None
                or echoed is not None
                or isinstance(hit.get("asOfResolved"), bool)
                or isinstance(hit.get("asOfReason"), str)
            )
            established = not asked or (
                hit.get("asOfResolved") is True and (sent is None or echoed == sent)
            )
            leaked = any(token in value for value in strings_of(passage))
            if established:
                assert passage.text == hit.get("text")
                assert passage.snippet == hit.get("snippet")
                assert leaked == ("text" in hit or "snippet" in hit)
                assert any(token in s for s in strings_of(dropped)) == leaked
                passed_on += leaked
            else:
                assert passage.text is None and passage.snippet is None
                assert passage.text_status == "withheld"
                assert passage.as_of_resolved is False
                assert not leaked
                assert not any(token in value for value in strings_of(dropped))
                withheld += 1
        assert result.withheld == sum(p.text_withheld for p in result.passages)
        assert dropped.dropped == result.withheld
        assert len(dropped.passages) == len(pairs) - result.withheld
    # both branches were exercised many times
    assert passed_on > 300 and withheld > 300


# ── case-law search ─────────────────────────────────────────────────────


def test_case_law_hit():
    result = passages.from_case_law_search(
        SemanticSearchResponse(case_body(CASE_HIT, credits={"operation_weight": 5}, hint="low"))
    )
    (p,) = result.passages
    assert p.kind == "case_law"
    assert p.celex == p.cite_celex == "62099CJ9901"
    assert p.text == SYN + "full text of the decision"
    assert p.snippet == SYN + "case snippet"
    assert p.text_status == "provided"
    assert p.title == "Synthetic v Fixture"
    assert (p.ecli, p.case_number, p.court) == ("ECLI:EU:C:2099:1", "C-999/99", "Court of Justice")
    assert (p.decision_date, p.document_type, p.paragraph_number) == ("2099-05-13", "judgment", 93)
    assert p.language == "en"
    assert p.score == 0.83
    assert p.url.endswith("CELEX:62099CJ9901")
    assert (p.consolidated, p.base_celex, p.article_ref) == (False, None, None)
    assert (p.as_of, p.as_of_resolved) == (None, None)
    assert result.units_charged == 5
    assert result.hint == "low"
    # the machine-written summary is not carried anywhere
    assert_no_leak(result, "MACHINE-WRITTEN-SUMMARY")


def test_case_law_snippet_is_never_promoted_to_text():
    hit = {k: v for k, v in CASE_HIT.items() if k != "text"}
    (p,) = passages.from_case_law_search(SemanticSearchResponse(case_body(hit))).passages
    assert p.text is None
    assert p.text_status == "absent"
    assert p.snippet == SYN + "case snippet"
    assert p.content() == ""


def test_case_law_missing_fields_and_uncitable_hits():
    body = case_body({"metadata": {"celex_id": "62099CJ9902"}}, {"case_name": "No CELEX"})
    result = passages.from_case_law_search(SemanticSearchResponse(body))
    assert result.passages == (
        Passage(kind="case_law", celex="62099CJ9902", text=None, text_status="absent"),
    )
    assert result.uncitable == 1
    assert result.units_charged is None


@pytest.mark.parametrize("body", [{}, {"results": []}, None])
def test_empty_case_law_results(body):
    assert passages.from_case_law_search(SemanticSearchResponse(body)) == PassageResult(passages=())


# ── document read ───────────────────────────────────────────────────────


def test_document_gives_one_passage_per_article():
    result = passages.from_document(DocumentResponse(document_body()))
    first, second = result.passages
    assert first == Passage(
        kind="document",
        celex="32099R0001",
        text=SYN + "article one",
        text_status="provided",
        article_ref="art_1",
        article_number="Article 1",
        article_title="Subject matter",
        title="Regulation (EU) 2099/1 (synthetic)",
        language="en",
        url=URL_BASE,
        eli="http://data.europa.eu/eli/reg/2099/1/oj",
    )
    assert (second.article_ref, second.article_number, second.article_title) == (
        "art_6a",
        "Article 6a",
        None,
    )
    assert second.text == SYN + "article six a"
    assert (first.as_of, first.as_of_resolved, first.consolidated) == (None, None, False)
    assert result.units_charged == 1


def test_document_single_article_read():
    body = document_body()
    body["document"]["content"]["articles"] = body["document"]["content"]["articles"][1:]
    (p,) = passages.from_document(DocumentResponse(body)).passages
    assert p.article_ref == "art_6a"


def test_consolidated_document_is_marked_and_names_no_base_act():
    result = passages.from_document(DocumentResponse(document_body("02099R0001-20990101")))
    for p in result.passages:
        assert p.consolidated is True
        assert p.base_celex is None
        assert p.cite_celex == "02099R0001-20990101"
    assert passages.format_citation(result.passages[0]) == (
        "CELEX 02099R0001-20990101, art_1, "
        f"consolidated text 02099R0001-20990101 (no legal force), {URL_BASE}"
    )


@pytest.mark.parametrize(
    "celex,expected",
    [
        ("02099R0001-20990101", True),
        ("02099L0003-20991231", True),
        ("02099D0003(01)-20990101", True),
        ("32099R0001", False),
        ("32099R0001R(02)", False),
        ("02099R0001", False),
        ("62099CJ9901", False),
    ],
)
def test_consolidated_is_read_off_the_celex_number(celex, expected):
    (p, _) = passages.from_document(DocumentResponse(document_body(celex))).passages
    assert p.consolidated is expected


def test_document_without_articles_gives_its_full_text():
    body = document_body(
        "62099CJ9901",
        content={"fullText": SYN + "judgment", "articles": []},
        ecli="ECLI:EU:C:2099:1",
        caseNumber="C-999/99",
        eli=None,
        provenance=None,
    )
    (p,) = passages.from_document(DocumentResponse(body)).passages
    assert p.text == SYN + "judgment"
    assert p.article_ref is None
    assert (p.ecli, p.case_number, p.eli) == ("ECLI:EU:C:2099:1", "C-999/99", None)
    assert p.url == "https://eur-lex.europa.eu/html-page"  # urls.html when no sourceUrl


def test_document_url_is_none_when_not_sent():
    body = document_body(provenance={"sourceUrl": None}, urls={})
    assert passages.from_document(DocumentResponse(body)).passages[0].url is None


def test_document_article_without_content():
    body = document_body(content={"articles": [{"id": "art_1"}]})
    (p,) = passages.from_document(DocumentResponse(body)).passages
    assert (p.text, p.text_status, p.article_number) == (None, "absent", None)


@pytest.mark.parametrize(
    "doc",
    [
        {"content": None, "textAvailability": {"available": False, "reason": "too_large"}},
        {"content": {}},
        {"content": {"articles": [], "recitals": [{"number": 1, "text": "r"}]}},
    ],
)
def test_document_without_text_gives_no_passage(doc):
    result = passages.from_document(DocumentResponse(document_body(**doc)))
    assert result.passages == ()
    assert result.uncitable == 0
    assert result.units_charged == 1


def test_document_without_celex_is_uncitable():
    body = document_body()
    del body["document"]["celex"]
    result = passages.from_document(DocumentResponse(body))
    assert (result.passages, result.uncitable) == ((), 1)
    assert passages.from_document(DocumentResponse({})).uncitable == 1


# ── legislation_as_of ───────────────────────────────────────────────────


def test_as_of_resolved_consolidated_answer():
    result = passages.from_legislation_as_of(LegislationAsOfResponse(as_of_body()))
    (p,) = result.passages
    assert p == Passage(
        kind="legislation",
        celex="02099R0001-20990101",
        text=SYN + "provision text",
        text_status="provided",
        article_ref="art_17",
        article_number="Article 17",
        article_title="Synthetic title",
        language="en",
        url=URL_CONS,
        consolidated=True,
        base_celex="32099R0001",
        consolidated_as_of="2099-01-01",
        consolidated_note=NOTE,
        as_of="2099-06-01",
        as_of_resolved=True,
        version_celex="02099R0001-20990101",
        version_kind="consolidated",
        version_date="2099-01-01",
        version_valid_until="2099-12-31",
    )
    assert p.cite_celex == "32099R0001"
    assert result == PassageResult(
        passages=(p,), as_of="2099-06-01", units_charged=4, credits_remaining=96
    )


def test_as_of_resolved_as_published_answer():
    body = as_of_body(
        asOfVersion={"celex": "32099R0001", "kind": "as_published"},
        consolidated=...,
        consolidatedAsOf=...,
        baseCelex=...,
        consolidatedNote=...,
    )
    (p,) = passages.from_legislation_as_of(LegislationAsOfResponse(body)).passages
    assert p.celex == p.cite_celex == "32099R0001"
    assert (p.consolidated, p.base_celex, p.consolidated_note) == (False, None, None)
    assert (p.version_kind, p.version_date, p.version_valid_until) == ("as_published", None, None)
    assert p.text == SYN + "provision text"


@pytest.mark.parametrize("ref", ["anx_III", "rct_26-28"])
def test_as_of_annex_and_recital_provisions(ref):
    body = as_of_body(provision={"id": ref, "text": SYN + "part"})
    (p,) = passages.from_legislation_as_of(LegislationAsOfResponse(body)).passages
    assert (p.article_ref, p.article_number, p.text) == (ref, None, SYN + "part")


def test_as_of_version_only_answer_has_no_text():
    body = as_of_body(provision=...)
    result = passages.from_legislation_as_of(LegislationAsOfResponse(body))
    (p,) = result.passages
    assert (p.text, p.text_status, p.article_ref) == (None, "absent", None)
    assert p.as_of_resolved is True
    assert p.version_celex == "02099R0001-20990101"
    assert result.withheld == 0


def test_as_of_unresolved_answer_is_a_withheld_passage():
    result = passages.from_legislation_as_of(LegislationAsOfResponse(unresolved_as_of_body()))
    (p,) = result.passages
    assert p.text is None
    assert p.text_status == "withheld"
    assert p.celex == "32099R0001"
    assert (p.as_of, p.as_of_resolved, p.as_of_reason) == (
        "2099-06-01",
        False,
        "amendment_not_consolidated",
    )
    assert p.version_celex is None
    assert p.content() == (
        "[Text withheld: not established as the text in force on 2099-06-01 "
        "(reason: amendment_not_consolidated).]"
    )
    assert (result.withheld, result.dropped, result.kept_labelled) == (1, 0, 0)
    assert result.units_charged == 4


def test_as_of_unresolved_answer_keeps_the_version_it_names():
    body = unresolved_as_of_body(
        asOfReason="article_not_in_version",
        asOfVersion={"celex": "02099R0001-20990101", "kind": "consolidated", "date": "2099-01-01"},
    )
    (p,) = passages.from_legislation_as_of(LegislationAsOfResponse(body)).passages
    assert p.celex == "32099R0001"  # no text comes from the version
    assert p.version_celex == "02099R0001-20990101"
    assert p.text_status == "withheld"


@pytest.mark.parametrize("policy", ["withhold", "keep_labelled", "drop"])
@pytest.mark.parametrize("resolved", [False, None, "true", 1, ...])
def test_as_of_unresolved_answer_with_text_never_passes_it_on(policy, resolved):
    body = unresolved_as_of_body(
        asOfResolved=resolved,
        provision={"id": "art_17", "text": "LATEST-TEXT-MUST-NOT-LEAK"},
    )
    result = passages.from_legislation_as_of(LegislationAsOfResponse(body), unresolved=policy)
    assert_no_leak(result, "MUST-NOT-LEAK")
    if policy == "drop":
        assert (result.passages, result.dropped, result.withheld) == ((), 1, 0)
    else:
        (p,) = result.passages
        assert (p.text, p.text_status, p.article_ref) == (None, "withheld", None)
        assert p.as_of_resolved is False
        assert (result.withheld, result.kept_labelled) == (1, 0)


def test_as_of_answer_without_a_document_is_uncitable():
    body = as_of_body(document=...)
    result = passages.from_legislation_as_of(LegislationAsOfResponse(body))
    assert (result.passages, result.uncitable, result.as_of) == ((), 1, "2099-06-01")


# ── citation line ───────────────────────────────────────────────────────


def test_citation_format_is_pinned():
    cons, pub = passages.from_legislation_search(
        leg(hit_consolidated(), hit_as_published())
    ).passages
    assert passages.format_citation(cons) == (
        "CELEX 32099R0001, art_17, "
        f"consolidated text 02099R0001-20990101 (no legal force), {URL_CONS}"
    )
    assert passages.format_citation(pub) == "CELEX 32099R0002, art_5"
    (case,) = passages.from_case_law_search(SemanticSearchResponse(case_body(CASE_HIT))).passages
    assert passages.format_citation(case) == (
        "CELEX 62099CJ9901, ECLI:EU:C:2099:1, para. 93, "
        "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:62099CJ9901"
    )
    (doc, _) = passages.from_document(DocumentResponse(document_body())).passages
    assert passages.format_citation(doc) == f"CELEX 32099R0001, art_1, {URL_BASE}"
    (asof,) = passages.from_legislation_as_of(LegislationAsOfResponse(as_of_body())).passages
    assert passages.format_citation(asof) == passages.format_citation(cons)


def test_citation_of_a_withheld_passage_still_names_the_act():
    (p,) = passages.from_legislation_search(leg(hit_unresolved())).passages
    assert passages.format_citation(p) == "CELEX 32099L0003, art_12"


def test_citation_parts_keep_one_order():
    p = Passage(
        kind="document",
        celex="02099R0001-20990101",
        text=None,
        text_status="absent",
        article_ref="art_1",
        ecli="ECLI:EU:C:2099:1",
        paragraph_number=4,
        consolidated=True,
        base_celex="32099R0001",
        url="https://example.invalid/page",
    )
    assert passages.format_citation(p) == (
        "CELEX 32099R0001, art_1, ECLI:EU:C:2099:1, para. 4, "
        "consolidated text 02099R0001-20990101 (no legal force), https://example.invalid/page"
    )


def test_citation_paragraph_zero_is_kept():
    p = Passage(
        kind="case_law", celex="62099CJ9901", text=None, text_status="absent", paragraph_number=0
    )
    assert passages.format_citation(p) == "CELEX 62099CJ9901, para. 0"


# ── restrict_to_celex ───────────────────────────────────────────────────


def test_restrict_to_celex_keeps_an_act_and_its_consolidated_texts():
    result = passages.from_legislation_search(
        leg(hit_consolidated(), hit_as_published(), hit_unresolved())
    )
    only = passages.restrict_to_celex(result, "32099R0001")
    assert [p.celex for p in only.passages] == ["02099R0001-20990101"]  # matched on base_celex
    assert only.filtered_out == 2
    assert only.withheld == 0  # the withheld passage was filtered out
    assert (only.as_of, only.units_charged, only.credits_remaining) == ("2099-06-01", 9, 991)
    assert result.filtered_out == 0 and len(result.passages) == 3  # input untouched

    by_version = passages.restrict_to_celex(result, "02099r0001-20990101 ")
    assert [p.celex for p in by_version.passages] == ["02099R0001-20990101"]

    several = passages.restrict_to_celex(result, ["32099R0002", "32099L0003"])
    assert [p.celex for p in several.passages] == ["32099R0002", "32099L0003"]
    assert (several.filtered_out, several.withheld) == (1, 1)

    again = passages.restrict_to_celex(several, {"32099L0003"})
    assert (len(again.passages), again.filtered_out, again.withheld) == (1, 2, 1)


def test_restrict_to_celex_counts_labelled_passages_and_matches_nothing_on_empty():
    result = passages.from_legislation_search(leg(hit_unresolved()), unresolved="keep_labelled")
    assert result.kept_labelled == 1
    none = passages.restrict_to_celex(result, "32099R0001")
    assert (none.passages, none.filtered_out, none.kept_labelled) == ((), 1, 0)
    assert passages.restrict_to_celex(result, []).passages == ()
    assert passages.restrict_to_celex(result, "").passages == ()
    # a number is matched whole, never as a prefix
    assert passages.restrict_to_celex(result, "32099L000").passages == ()


def test_legislation_index_filter_claim_matches_the_docstring():
    doc = passages.restrict_to_celex.__doc__ or ""
    assert "no ``filters`` key except ``language``" in doc


# ── credits ─────────────────────────────────────────────────────────────


def test_credits_pass_through():
    result = passages.from_legislation_search(leg(hit_as_published()))
    assert (result.units_charged, result.credits_remaining) == (9, 991)
    free = passages.from_legislation_search(leg(credits={"operation_weight": 0}))
    assert free.units_charged == 0  # reported as free, not as unknown


def test_credits_not_reported_is_none_not_zero():
    body = legislation_body(hit_as_published())
    del body["credits"]
    body["usage"] = {"remaining": 7, "unit": "calls"}
    response = SemanticLegislationResponse(body)
    assert response.units_charged == 0
    result = passages.from_legislation_search(response)
    assert result.units_charged is None
    assert result.credits_remaining == 7


def test_hint_passes_through():
    assert passages.from_legislation_search(leg(hint="rephrase")).hint == "rephrase"
    assert passages.from_legislation_search(leg()).hint is None


# ── immutability ────────────────────────────────────────────────────────


def test_passages_and_results_are_immutable():
    result = passages.from_legislation_search(leg(hit_unresolved()))
    (p,) = result.passages
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.text = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.withheld = 0  # type: ignore[misc]
    assert isinstance(result.passages, tuple)
    assert hash(p) == hash(dataclasses.replace(p))
    assert not hasattr(p, "raw")  # no way back to the withheld text
    assert not hasattr(p, "__dict__") or "raw" not in vars(p)


def test_conversion_does_not_modify_the_response():
    body = legislation_body(hit_consolidated(), hit_unresolved())
    snapshot = copy.deepcopy(body)
    response = SemanticLegislationResponse(body)
    for policy in ("withhold", "drop", "keep_labelled"):
        passages.from_legislation_search(response, unresolved=policy)
    assert body == snapshot
    assert response.results[1].text == SYN + "LATEST-TEXT-MUST-NOT-LEAK"


# ── through the clients (mock transport) ────────────────────────────────


def _call(cls, body, method, *args, **kwargs):
    if cls is LexAPI:
        with offline_client(body) as client:
            return getattr(client, method)(*args, **kwargs)

    async def run():
        async with offline_client(body, cls=AsyncLexAPI) as client:
            return await getattr(client, method)(*args, **kwargs)

    return asyncio.run(run())


@pytest.mark.parametrize("cls", [LexAPI, AsyncLexAPI])
def test_client_legislation_search_to_passages(cls):
    body = legislation_body(hit_consolidated(), hit_unresolved())
    response = _call(
        cls, body, "semantic_legislation_search", "synthetic", as_of="2099-06-01", include_text=True
    )
    result = passages.from_legislation_search(response, as_of="2099-06-01")
    assert [p.text_status for p in result.passages] == ["provided", "withheld"]
    assert (result.withheld, result.units_charged) == (1, 9)
    assert_no_leak(result, "MUST-NOT-LEAK")


@pytest.mark.parametrize("cls", [LexAPI, AsyncLexAPI])
def test_client_case_law_search_to_passages(cls):
    response = _call(cls, case_body(CASE_HIT), "semantic_search", "synthetic", include_text=True)
    (p,) = passages.from_case_law_search(response).passages
    assert (p.celex, p.ecli) == ("62099CJ9901", "ECLI:EU:C:2099:1")


@pytest.mark.parametrize("cls", [LexAPI, AsyncLexAPI])
def test_client_document_to_passages(cls):
    response = _call(cls, document_body(), "get_document", "32099R0001", article_id="art_1")
    result = passages.from_document(response)
    assert [p.article_ref for p in result.passages] == ["art_1", "art_6a"]


@pytest.mark.parametrize("cls", [LexAPI, AsyncLexAPI])
def test_client_legislation_as_of_to_passages(cls):
    args = ("32099R0001", "2099-06-01")
    response = _call(cls, as_of_body(), "legislation_as_of", *args, provision="art_17")
    assert passages.from_legislation_as_of(response).passages[0].text == SYN + "provision text"
    response = _call(cls, unresolved_as_of_body(), "legislation_as_of", *args, provision="art_17")
    result = passages.from_legislation_as_of(response)
    assert (result.passages[0].text, result.withheld) == (None, 1)


# ── packaging ───────────────────────────────────────────────────────────


def test_public_names():
    assert passages.__all__ == [
        "Passage",
        "PassageKind",
        "PassageResult",
        "TextStatus",
        "UnresolvedPolicy",
        "format_citation",
        "from_case_law_search",
        "from_document",
        "from_legislation_as_of",
        "from_legislation_search",
        "restrict_to_celex",
    ]
    for name in passages.__all__:
        assert hasattr(passages, name)
    import lexapi

    assert "Passage" not in lexapi.__all__  # reached as lexapi.passages only


def test_module_imports_only_the_standard_library_and_the_sdk():
    source = (ROOT / "src" / "lexapi" / "passages.py").read_text(encoding="utf-8")
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            roots.add((node.module or "").split(".")[0])
    assert roots == {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "re",
        "typing",
        "lexapi",
    }


def test_importing_the_module_loads_no_framework_and_nothing_new():
    code = (
        "import sys\n"
        "import lexapi\n"
        "before = set(sys.modules)\n"
        "import lexapi.passages\n"
        "new = sorted(set(sys.modules) - before)\n"
        "frameworks = sorted(m for m in sys.modules\n"
        "    if m.split('.')[0] in ('langchain', 'langchain_core', 'llama_index')\n"
        "    or m.startswith(('langchain', 'llama_index', 'llama-index')))\n"
        "third_party = sorted(m for m in new if 'site-packages' in\n"
        "    (getattr(sys.modules[m], '__file__', None) or ''))\n"
        "print('NEW', new)\n"
        "print('FRAMEWORKS', frameworks)\n"
        "print('THIRD_PARTY', third_party)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=str(ROOT)
    ).stdout.splitlines()
    assert out[0] == "NEW ['lexapi.passages']"
    assert out[1] == "FRAMEWORKS []"
    assert out[2] == "THIRD_PARTY []"


def test_no_dependency_was_added():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.findall(r"^dependencies = (.*)$", text, flags=re.M) == ['["httpx>=0.27,<1"]']
    assert re.findall(r"^dev = (.*)$", text, flags=re.M) == [
        '["pytest>=8", "ruff>=0.4", "mypy>=1.10"]'
    ]
    assert 'requires-python = ">=3.9"' in text
    assert "langchain" not in text.lower() and "llama" not in text.lower()
