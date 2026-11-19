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

from lexapi import AS_OF_LOOKUP_REASONS, AS_OF_REASONS, AsyncLexAPI, LexAPI, passages
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
AS_OF_KEYS = ("asOf", "asOfResolved", "asOfReason")


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
        "act": {"celex": "32099R0001"},
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
    meta = [str(v) for v in obj.metadata().values()]
    rendered = [obj.content(), passages.format_citation(obj), passages.format_passage(obj), obj.id]
    return [*values, *meta, *rendered, repr(obj), str(obj)]


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
    assert p.cite_celex is None  # the act to cite is not named
    assert passages.format_citation(p) == (
        f"consolidated text 02099R0001-20990101 (no legal force; act not named), art_17, {URL_CONS}"
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
    assert passages.format_citation(p) == (
        f"CELEX 32099R0002, {ref}, version for 2099-06-01: 32099R0002"
    )


def test_missing_optional_fields_are_none():
    result = passages.from_legislation_search(
        SemanticLegislationResponse({"results": [{"law_id": "32099R0002"}]})
    )
    (p,) = result.passages
    assert p == Passage(kind="legislation", celex="32099R0002", text=None, text_status="absent")
    assert p.content() == "[No text returned for CELEX 32099R0002.]"
    assert p.has_text is False
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
        "[No text: CELEX 32099L0003 art_12 is not established as the text for 2099-06-01 "
        "(reason: fetch_timeout).]"
    )
    assert p.has_text is False
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
        "[Latest indexed text of CELEX 32099L0003 art_12; not established as the text for "
        "2099-06-01 (reason: fetch_timeout).]\n" + SYN + "LATEST-TEXT-MUST-NOT-LEAK"
    )
    assert p.has_text is True
    assert (result.withheld, result.kept_labelled, result.dropped) == (0, 1, 0)
    assert result.passages[0].text_status == "provided"


def test_keep_labelled_marks_a_hit_that_has_only_a_snippet():
    hit = hit_unresolved()
    del hit["text"]
    result = passages.from_legislation_search(leg(hit), unresolved="keep_labelled")
    (p,) = result.passages
    assert p.text_status == "not_established_for_date"
    assert p.snippet == SYN + "LATEST-SNIPPET-MUST-NOT-LEAK"
    assert p.content() == "[No text returned for CELEX 32099L0003 art_12.]"  # never the snippet
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
    assert p.content() == (
        "[No text: CELEX 32099L0003 art_12 is not established as the text for 2099-06-01.]"
    )
    hit = {"law_id": "32099L0003", "text": "LATEST-TEXT-MUST-NOT-LEAK", "asOfResolved": False}
    (p,) = passages.from_legislation_search(leg(hit, as_of=None)).passages
    assert p.as_of is None
    assert p.content() == (
        "[No text: CELEX 32099L0003 is not established as the text for the date asked.]"
    )
    assert passages.format_citation(p) == (
        "CELEX 32099L0003, text for the date asked not established"
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
                or response_as_of is not None
                or any(hit.get(key) is not None for key in AS_OF_KEYS)
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
    assert p.content() == "[No text returned for CELEX 62099CJ9901.]"  # never the snippet
    assert p.has_text is False


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
        kind="document_read",
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
        assert p.cite_celex is None
        assert "cite_celex" not in p.metadata()
    assert passages.format_citation(result.passages[0]) == (
        f"consolidated text 02099R0001-20990101 (no legal force; act not named), art_1, {URL_BASE}"
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
        as_of_echoed="2099-06-01",
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
        "[No text: CELEX 32099R0001 is not established as the text for 2099-06-01 "
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
        "CELEX 32099R0001, art_17, consolidated text 02099R0001-20990101 (no legal force), "
        f"version for 2099-06-01: 02099R0001-20990101, {URL_CONS}"
    )
    assert passages.format_citation(pub) == (
        "CELEX 32099R0002, art_5, version for 2099-06-01: 32099R0002"
    )
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
    assert passages.format_citation(p) == (
        "CELEX 32099L0003, art_12, text for 2099-06-01 not established"
    )


def test_citation_parts_keep_one_order():
    p = Passage(
        kind="document_read",
        celex="02099R0001-20990101",
        text=None,
        text_status="absent",
        article_ref="art_1",
        ecli="ECLI:EU:C:2099:1",
        paragraph_number=4,
        consolidated=True,
        base_celex="32099R0001",
        as_of="2099-06-01",
        as_of_resolved=True,
        version_celex="02099R0001-20990101",
        url="https://example.invalid/page",
    )
    assert passages.format_citation(p) == (
        "CELEX 32099R0001, art_1, ECLI:EU:C:2099:1, para. 4, "
        "consolidated text 02099R0001-20990101 (no legal force), "
        "version for 2099-06-01: 02099R0001-20990101, https://example.invalid/page"
    )
    unnamed = dataclasses.replace(p, base_celex=None, as_of_resolved=False, version_celex=None)
    assert passages.format_citation(unnamed) == (
        "consolidated text 02099R0001-20990101 (no legal force; act not named), art_1, "
        "ECLI:EU:C:2099:1, para. 4, text for 2099-06-01 not established, "
        "https://example.invalid/page"
    )
    no_version = dataclasses.replace(p, version_celex=None, url=None)
    assert passages.format_citation(no_version).endswith("(no legal force), version for 2099-06-01")


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
    for empty in ([], "", "  ", ["", " "], "CELEX:"):
        with pytest.raises(ValueError, match="at least one CELEX number"):
            passages.restrict_to_celex(result, empty)
    for bad in ([None], ["32099L0003", 5], [b"32099L0003"], [["32099L0003"]]):
        with pytest.raises(ValueError, match="as strings"):
            passages.restrict_to_celex(result, bad)  # type: ignore[arg-type]
    for prefixed in ("CELEX:32099L0003", " celex: 32099l0003 ", ["CELEX:32099L0003"]):
        assert len(passages.restrict_to_celex(result, prefixed).passages) == 1
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


# ── metadata, id, format_passage ────────────────────────────────────────

ALWAYS = {"kind", "celex", "text_status", "text_withheld", "consolidated", "citation", "id"}
EVERY_KEY = ALWAYS | {
    "cite_celex",
    "article_ref",
    "article_number",
    "article_title",
    "title",
    "language",
    "url",
    "eli",
    "score",
    "base_celex",
    "consolidated_as_of",
    "consolidated_note",
    "as_of",
    "as_of_resolved",
    "as_of_reason",
    "version_celex",
    "version_kind",
    "version_date",
    "version_valid_until",
    "ecli",
    "case_number",
    "court",
    "decision_date",
    "document_type",
    "paragraph_number",
}
HIT_KEYS = {"cite_celex", "article_ref", "title", "language", "score", "as_of", "as_of_resolved"}


def _one(result):
    (p,) = result.passages
    return p


def _metadata_cases():
    unlabelled = without_as_of(hit_consolidated())
    for key in ("consolidated", "consolidatedAsOf", "baseCelex", "consolidatedNote"):
        del unlabelled[key]
    consolidated = {"base_celex", "consolidated_as_of", "consolidated_note"}
    version = {"version_celex", "version_kind", "version_date"}
    search = passages.from_legislation_search
    as_of = passages.from_legislation_as_of
    return {
        "resolved consolidated": (
            _one(search(leg(hit_consolidated()))),
            ALWAYS | HIT_KEYS | consolidated | version | {"url"},
        ),
        "resolved as published": (
            _one(search(leg(hit_as_published()))),
            ALWAYS | HIT_KEYS | {"version_celex", "version_kind"},
        ),
        "withheld": (
            _one(search(leg(hit_unresolved()))),
            ALWAYS | HIT_KEYS | {"as_of_reason"},
        ),
        "keep_labelled": (
            _one(search(leg(hit_unresolved()), unresolved="keep_labelled")),
            ALWAYS | HIT_KEYS | {"as_of_reason"},
        ),
        "no date": (
            _one(search(leg(without_as_of(hit_as_published()), as_of=None))),
            ALWAYS | {"cite_celex", "article_ref", "title", "language", "score"},
        ),
        "consolidated without base": (
            _one(search(leg(unlabelled, as_of=None))),
            ALWAYS | {"article_ref", "title", "language", "score", "url"},
        ),
        "bare": (
            _one(search(leg({"law_id": "32099R0002"}, as_of=None))),
            ALWAYS | {"cite_celex"},
        ),
        "case law": (
            _one(passages.from_case_law_search(SemanticSearchResponse(case_body(CASE_HIT)))),
            ALWAYS
            | {"cite_celex", "title", "language", "url", "score", "ecli", "case_number", "court"}
            | {"decision_date", "document_type", "paragraph_number"},
        ),
        "document": (
            passages.from_document(DocumentResponse(document_body())).passages[0],
            ALWAYS
            | {"cite_celex", "article_ref", "article_number", "article_title", "title"}
            | {"language", "url", "eli"},
        ),
        "consolidated document": (
            passages.from_document(DocumentResponse(document_body("02099R0001-20990101"))).passages[
                1
            ],
            ALWAYS | {"article_ref", "article_number", "title", "language", "url", "eli"},
        ),
        "as-of resolved": (
            _one(as_of(LegislationAsOfResponse(as_of_body()))),
            ALWAYS
            | {"cite_celex", "article_ref", "article_number", "article_title", "language", "url"}
            | {"as_of", "as_of_resolved", "version_valid_until"}
            | consolidated
            | version,
        ),
        "as-of unresolved": (
            _one(as_of(LegislationAsOfResponse(unresolved_as_of_body()))),
            ALWAYS | {"cite_celex", "language", "url", "as_of", "as_of_resolved", "as_of_reason"},
        ),
    }


@pytest.mark.parametrize("case", sorted(_metadata_cases()))
def test_metadata_key_set_is_pinned(case):
    passage, keys = _metadata_cases()[case]
    meta = passage.metadata()
    assert set(meta) == keys
    assert ALWAYS <= set(meta) <= EVERY_KEY
    assert "text" not in meta and "snippet" not in meta
    for key, value in meta.items():
        assert type(value) in (str, int, float, bool), key
    assert meta["citation"] == passages.format_citation(passage)
    assert meta["id"] == passage.id
    assert meta["text_withheld"] is passage.text_withheld
    assert meta.get("cite_celex") == passage.cite_celex
    for key in set(meta) - {"citation", "id", "text_withheld", "cite_celex"}:
        assert meta[key] == getattr(passage, key)


def test_metadata_never_holds_text_or_snippet():
    for policy in ("withhold", "keep_labelled"):
        result = passages.from_legislation_search(
            leg(hit_consolidated(), hit_unresolved()), unresolved=policy
        )
        for p in result.passages:
            for value in p.metadata().values():
                assert "MUST-NOT-LEAK" not in str(value)
                assert SYN not in str(value)


def test_metadata_values_of_a_withheld_passage():
    meta = _one(passages.from_legislation_search(leg(hit_unresolved()))).metadata()
    assert meta == {
        "kind": "legislation",
        "celex": "32099L0003",
        "cite_celex": "32099L0003",
        "text_status": "withheld",
        "text_withheld": True,
        "consolidated": False,
        "article_ref": "art_12",
        "title": "Directive (EU) 2099/3 (synthetic)",
        "language": "en",
        "score": 0.75,
        "as_of": "2099-06-01",
        "as_of_resolved": False,
        "as_of_reason": "fetch_timeout",
        "citation": "CELEX 32099L0003, art_12, text for 2099-06-01 not established",
        "id": "lexapi:32099L0003:art_12:2099-06-01:en",
    }


def test_metadata_keeps_false_and_zero():
    p = Passage(
        kind="case_law",
        celex="62099CJ9901",
        text=None,
        text_status="absent",
        score=0.0,
        paragraph_number=0,
        as_of_resolved=False,
    )
    meta = p.metadata()
    assert (meta["score"], meta["paragraph_number"], meta["as_of_resolved"]) == (0.0, 0, False)
    assert meta["consolidated"] is False


def test_id_is_stable_and_differs_by_date():
    first = _one(passages.from_legislation_search(leg(hit_as_published())))
    again = _one(passages.from_legislation_search(leg(hit_as_published())))
    assert first.id == again.id == "lexapi:32099R0002:art_5:2099-06-01:en"
    other = hit_as_published()
    other.update(asOf="2098-01-01", text="another text", score=0.1)
    other_date = _one(passages.from_legislation_search(leg(other, as_of="2098-01-01")))
    assert other_date.id == "lexapi:32099R0002:art_5:2098-01-01:en"
    undated = _one(passages.from_legislation_search(leg(without_as_of(other), as_of=None)))
    assert undated.id == "lexapi:32099R0002:art_5::en"
    # the text plays no part: withheld and labelled passages share the id
    withheld = _one(passages.from_legislation_search(leg(hit_unresolved())))
    labelled = _one(
        passages.from_legislation_search(leg(hit_unresolved()), unresolved="keep_labelled")
    )
    assert withheld.id == labelled.id == "lexapi:32099L0003:art_12:2099-06-01:en"


def test_id_of_case_law_documents_and_bare_passages():
    case = _one(passages.from_case_law_search(SemanticSearchResponse(case_body(CASE_HIT))))
    assert case.id == "lexapi:62099CJ9901:93::en"
    first, second = passages.from_document(DocumentResponse(document_body())).passages
    assert (first.id, second.id) == ("lexapi:32099R0001:art_1::en", "lexapi:32099R0001:art_6a::en")
    bare = Passage(kind="case_law", celex="62099CJ9901", text=None, text_status="absent")
    assert bare.id == "lexapi:62099CJ9901:::"
    assert dataclasses.replace(bare, paragraph_number=0).id == "lexapi:62099CJ9901:0::"


def test_format_passage_is_content_then_source_line():
    cons, unresolved = passages.from_legislation_search(
        leg(hit_consolidated(), hit_unresolved())
    ).passages
    assert passages.format_passage(cons) == (
        SYN + "consolidated article seventeen\n"
        "Source: CELEX 32099R0001, art_17, consolidated text 02099R0001-20990101 "
        f"(no legal force), version for 2099-06-01: 02099R0001-20990101, {URL_CONS}"
    )
    assert passages.format_passage(unresolved) == (
        "[No text: CELEX 32099L0003 art_12 is not established as the text for 2099-06-01 "
        "(reason: fetch_timeout).]\n"
        "Source: CELEX 32099L0003, art_12, text for 2099-06-01 not established"
    )
    for p in (cons, unresolved):
        assert passages.format_passage(p) == (
            p.content() + "\nSource: " + passages.format_citation(p)
        )
    labelled = _one(
        passages.from_legislation_search(leg(hit_unresolved()), unresolved="keep_labelled")
    )
    assert passages.format_passage(labelled) == (
        "[Latest indexed text of CELEX 32099L0003 art_12; not established as the text for "
        "2099-06-01 (reason: fetch_timeout).]\n"
        + SYN
        + "LATEST-TEXT-MUST-NOT-LEAK\n"
        + "Source: CELEX 32099L0003, art_12, text for 2099-06-01 not established"
    )


# ── notices ─────────────────────────────────────────────────────────────

NOT_IN_FORCE = {"not_yet_adopted", "not_yet_in_force", "no_longer_in_force"}
ALL_REASONS = sorted(set(AS_OF_REASONS) | set(AS_OF_LOOKUP_REASONS) | {"a_reason_added_later"})


def test_not_in_force_reasons_are_documented_reasons():
    assert NOT_IN_FORCE <= set(AS_OF_REASONS)
    assert NOT_IN_FORCE <= set(AS_OF_LOOKUP_REASONS)


@pytest.mark.parametrize("reason", ALL_REASONS)
def test_every_reason_renders_one_of_the_two_notices(reason):
    hit = hit_unresolved()
    hit["asOfReason"] = reason
    withheld = _one(passages.from_legislation_search(leg(hit)))
    labelled = _one(passages.from_legislation_search(leg(hit), unresolved="keep_labelled"))
    answer = _one(
        passages.from_legislation_as_of(
            LegislationAsOfResponse(unresolved_as_of_body(asOfReason=reason)), provision="art_12"
        )
    )
    text = SYN + "LATEST-TEXT-MUST-NOT-LEAK"
    tail = f"2099-06-01 (reason: {reason})."
    if reason in NOT_IN_FORCE:
        assert withheld.content() == (
            f"[No text: CELEX 32099L0003 art_12 was not in force on {tail}]"
        )
        assert labelled.content() == (
            "[Latest indexed text of CELEX 32099L0003 art_12; the act was not in force on "
            f"{tail}]\n{text}"
        )
        assert answer.content() == (
            f"[No text: CELEX 32099R0001 art_12 was not in force on {tail}]"
        )
    else:
        assert withheld.content() == (
            f"[No text: CELEX 32099L0003 art_12 is not established as the text for {tail}]"
        )
        assert labelled.content() == (
            "[Latest indexed text of CELEX 32099L0003 art_12; not established as the text for "
            f"{tail}]\n{text}"
        )
        assert answer.content() == (
            f"[No text: CELEX 32099R0001 art_12 is not established as the text for {tail}]"
        )
        for p in (withheld, labelled, answer):
            assert "in force" not in p.content()
    for p in (withheld, labelled, answer):
        assert passages.format_citation(p).count("text for 2099-06-01 not established") == 1


@pytest.mark.parametrize("reason", sorted(NOT_IN_FORCE))
@pytest.mark.parametrize("policy", ["withhold", "keep_labelled"])
def test_not_in_force_wording_only_for_the_date_the_api_judged(policy, reason):
    hit = hit_unresolved()
    hit.update(asOf="2098-01-01", asOfReason=reason)
    response = leg(hit, as_of="2098-01-01")
    # the caller asked for another date than the one the API answered for
    p = _one(passages.from_legislation_search(response, unresolved=policy, as_of="2099-06-01"))
    assert (p.as_of, p.as_of_echoed, p.as_of_reason) == ("2099-06-01", "2098-01-01", reason)
    generic = f"not established as the text for 2099-06-01 (reason: {reason})."
    if policy == "withhold":
        assert p.content() == f"[No text: CELEX 32099L0003 art_12 is {generic}]"
    else:
        assert p.content() == (
            f"[Latest indexed text of CELEX 32099L0003 art_12; {generic}]\n"
            + SYN
            + "LATEST-TEXT-MUST-NOT-LEAK"
        )
    assert "in force" not in p.content()
    assert "2098-01-01" not in p.content()
    assert "as_of_echoed" not in p.metadata()

    # the API echoed no date at all: nothing was judged for the date printed
    silent = without_as_of(hit_unresolved())
    silent["asOfReason"] = reason
    response = leg(silent, as_of=None)
    p = _one(passages.from_legislation_search(response, unresolved=policy, as_of="2099-06-01"))
    assert p.as_of_echoed is None
    assert "in force" not in p.content() and generic in p.content()

    # same date: the not-in-force wording stands
    same = hit_unresolved()
    same["asOfReason"] = reason
    p = _one(passages.from_legislation_search(leg(same), unresolved=policy, as_of="2099-06-01"))
    assert p.as_of_echoed == "2099-06-01"
    assert f"not in force on 2099-06-01 (reason: {reason})." in p.content()


def test_not_in_force_wording_needs_an_echoed_date_on_a_hand_built_passage():
    p = Passage(
        kind="legislation",
        celex="32099L0003",
        text=None,
        text_status="withheld",
        as_of="2099-06-01",
        as_of_resolved=False,
        as_of_reason="no_longer_in_force",
    )
    assert "in force" not in p.content()
    assert (
        "not in force on 2099-06-01" in dataclasses.replace(p, as_of_echoed="2099-06-01").content()
    )
    undated = dataclasses.replace(p, as_of=None)
    assert undated.content() == (
        "[No text: CELEX 32099L0003 is not established as the text for the date asked "
        "(reason: no_longer_in_force).]"
    )


def test_notice_of_a_version_only_answer():
    p = _one(passages.from_legislation_as_of(LegislationAsOfResponse(as_of_body(provision=...))))
    assert p.has_text is False
    assert p.content() == (
        "[Version 02099R0001-20990101 (consolidated) is the version listed for 2099-06-01; "
        "no provision was requested.]"
    )
    no_kind = as_of_body(provision=..., asOfVersion={"celex": "32099R0001"})
    p = _one(passages.from_legislation_as_of(LegislationAsOfResponse(no_kind)))
    assert p.content() == (
        "[Version 32099R0001 is the version listed for 2099-06-01; no provision was requested.]"
    )


def test_notice_of_a_resolved_provision_without_text():
    body = as_of_body(provision={"id": "art_17"})
    p = _one(passages.from_legislation_as_of(LegislationAsOfResponse(body)))
    assert p.text_status == "absent"
    assert p.content() == "[No text returned for CELEX 02099R0001-20990101 art_17.]"


def test_has_text():
    cons, unresolved = passages.from_legislation_search(
        leg(hit_consolidated(), hit_unresolved())
    ).passages
    assert (cons.has_text, unresolved.has_text) == (True, False)
    empty = Passage(kind="legislation", celex="32099R0002", text="", text_status="provided")
    assert empty.has_text is True and empty.content() == ""


# ── the act to cite ─────────────────────────────────────────────────────


def test_cite_celex_is_none_only_for_a_consolidated_text_without_its_act():
    plain = Passage(kind="legislation", celex="32099R0002", text=None, text_status="absent")
    assert plain.cite_celex == "32099R0002"
    # base_celex is ignored on a text that is not consolidated
    assert dataclasses.replace(plain, base_celex="32099R0009").cite_celex == "32099R0002"
    cons = dataclasses.replace(plain, celex="02099R0001-20990101", consolidated=True)
    assert cons.cite_celex is None
    assert dataclasses.replace(cons, base_celex="").cite_celex is None
    assert dataclasses.replace(cons, base_celex="32099R0001").cite_celex == "32099R0001"
    assert passages.format_citation(cons) == (
        "consolidated text 02099R0001-20990101 (no legal force; act not named)"
    )
    assert "CELEX 02099R0001" not in passages.format_citation(cons)


# ── legislation_as_of: the provision asked for ──────────────────────────


def test_as_of_unresolved_answer_carries_the_provision_asked_for():
    response = LegislationAsOfResponse(unresolved_as_of_body())
    p = _one(passages.from_legislation_as_of(response, provision="art_17"))
    assert (p.article_ref, p.text, p.text_status) == ("art_17", None, "withheld")
    assert p.id == "lexapi:32099R0001:art_17:2099-06-01:en"
    assert passages.format_citation(p) == (
        f"CELEX 32099R0001, art_17, text for 2099-06-01 not established, {URL_CONS}"
    )
    assert _one(passages.from_legislation_as_of(response)).article_ref is None


def test_as_of_resolved_answer_keeps_the_provision_the_api_names():
    response = LegislationAsOfResponse(as_of_body())
    p = _one(passages.from_legislation_as_of(response, provision="Article 17"))
    assert p.article_ref == "art_17"
    version_only = LegislationAsOfResponse(as_of_body(provision=...))
    p = _one(passages.from_legislation_as_of(version_only, provision="art_17"))
    assert p.article_ref is None  # the answer is about the version, not a provision


# ── fail closed on any sign of a date ───────────────────────────────────

ABSENT = object()
HIT_AS_OF = [ABSENT, None, "", "2099-06-01", "2098-01-01", 20990601, 0, False, []]
HIT_RESOLVED = [ABSENT, None, True, False, "true", "True", 1, 0, "", [True]]
HIT_REASON = [ABSENT, None, "", "fetch_timeout", 5, False]
RESPONSE_AS_OF = [ABSENT, None, "", "2099-06-01", 0]
RESPONSE_OTHER = [ABSENT, ("asOfResolved", False), ("asOfReason", "fetch_failed")]


def _with(body, key, value):
    if value is not ABSENT:
        body[key] = value


def _enumerated_bodies():
    for hit_as_of in HIT_AS_OF:
        for hit_resolved in HIT_RESOLVED:
            for hit_reason in HIT_REASON:
                hit = {"law_id": "32099L0003", "text": "SECRET-TEXT", "snippet": "SECRET-SNIP"}
                _with(hit, "asOf", hit_as_of)
                _with(hit, "asOfResolved", hit_resolved)
                _with(hit, "asOfReason", hit_reason)
                for response_as_of in RESPONSE_AS_OF:
                    for other in RESPONSE_OTHER:
                        body = {"results": [hit]}
                        _with(body, "asOf", response_as_of)
                        if other is not ABSENT:
                            body[other[0]] = other[1]
                        for sent in (None, "2099-06-01"):
                            yield hit, body, sent


def test_no_text_passes_unless_resolved_is_exactly_true_enumeration():
    combos = passed = blocked = 0
    for hit, body, sent in _enumerated_bodies():
        combos += 1
        date_involved = sent is not None or any(
            source.get(key) is not None for source in (hit, body) for key in AS_OF_KEYS
        )
        response = SemanticLegislationResponse(body)
        for policy in ("withhold", "drop"):
            result = passages.from_legislation_search(response, unresolved=policy, as_of=sent)
            leaked = any("SECRET" in s for s in strings_of(result))
            if date_involved and hit.get("asOfResolved") is not True:
                assert not leaked, (hit, body, sent, policy)
                blocked += 1
            elif not date_involved:
                assert leaked, (hit, body, sent, policy)
                passed += 1
            elif leaked:
                # resolved is exactly True: text passes only for the date sent
                echoed = hit.get("asOf") if isinstance(hit.get("asOf"), str) else None
                if not echoed and isinstance(body.get("asOf"), str):
                    echoed = body["asOf"]
                assert sent is None or echoed == sent, (hit, body, sent, policy)
                passed += 1
    assert combos == 9 * 10 * 6 * 5 * 3 * 2 == 16200
    assert blocked > 20000 and passed > 500


def test_a_hit_level_empty_as_of_falls_back_to_the_response_date():
    hit = hit_as_published()
    hit["asOf"] = ""
    p = _one(passages.from_legislation_search(leg(hit), as_of="2099-06-01"))
    assert (p.as_of, p.as_of_resolved, p.has_text) == ("2099-06-01", True, True)
    # and on its own it is still a sign that a date was asked
    lone = {"law_id": "32099L0003", "text": "LATEST-TEXT-MUST-NOT-LEAK", "asOf": ""}
    result = passages.from_legislation_search(leg(lone, as_of=None))
    assert (result.passages[0].as_of, result.passages[0].text_status) == (None, "withheld")
    assert_no_leak(result, "MUST-NOT-LEAK")


def test_a_response_level_sign_of_a_date_covers_every_hit():
    plain = {"law_id": "32099L0003", "text": "LATEST-TEXT-MUST-NOT-LEAK"}
    for extra in ({"asOfReason": "fetch_failed"}, {"asOfResolved": True}, {"asOf": 0}):
        result = passages.from_legislation_search(leg(plain, as_of=None, **extra))
        assert result.withheld == 1, extra
        assert_no_leak(result, "MUST-NOT-LEAK")


# ── packaging ───────────────────────────────────────────────────────────


def test_public_names():
    assert passages.__all__ == [
        "Passage",
        "PassageKind",
        "PassageResult",
        "TextStatus",
        "UnresolvedPolicy",
        "format_citation",
        "format_passage",
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
    source = pathlib.Path(passages.__file__).read_text(encoding="utf-8")
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            roots.add((node.module or "").split(".")[0])
    assert roots == {"__future__", "collections", "dataclasses", "re", "typing", "lexapi"}


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
        "third_party = sorted(m for m in new if m.split('.')[0] != 'lexapi'\n"
        "    and 'site-packages' in (getattr(sys.modules[m], '__file__', None) or ''))\n"
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


# ── blank text is no text ───────────────────────────────────────────────

BLANKS = ["", "   ", "\n\t ", " 　", "​", "﻿‍⁠", " ‌ ­ "]


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_legislation_text_is_absent(blank):
    hit = without_as_of(hit_as_published())
    hit["text"] = blank
    (p,) = passages.from_legislation_search(leg(hit, as_of=None)).passages
    assert p.text is None
    assert p.text_status == "absent"
    assert p.has_text is False
    assert p.content() == "[No text returned for CELEX 32099R0002 art_5.]"
    assert p.metadata()["text_status"] == "absent"


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_text_of_a_resolved_hit_is_absent(blank):
    hit = hit_as_published()
    hit["text"] = blank
    (p,) = passages.from_legislation_search(leg(hit), as_of="2099-06-01").passages
    assert (p.text, p.text_status, p.has_text) == (None, "absent", False)
    assert p.as_of_resolved is True


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_text_of_an_unresolved_hit_is_treated_as_a_missing_text(blank):
    hit = hit_unresolved()
    hit["text"] = blank
    withheld = passages.from_legislation_search(leg(hit)).passages[0]
    assert (withheld.text, withheld.text_status, withheld.has_text) == (None, "withheld", False)
    kept = passages.from_legislation_search(leg(hit), unresolved="keep_labelled").passages[0]
    assert (kept.text, kept.has_text) == (None, False)
    assert kept.text_status == "not_established_for_date"
    assert kept.content() == "[No text returned for CELEX 32099L0003 art_12.]"


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_case_law_text_is_absent(blank):
    hit = dict(CASE_HIT, text=blank)
    (p,) = passages.from_case_law_search(SemanticSearchResponse(case_body(hit))).passages
    assert (p.text, p.text_status, p.has_text) == (None, "absent", False)


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_article_of_a_document_is_absent(blank):
    body = document_body(
        content={
            "articles": [
                {"id": "art_1", "number": "Article 1", "content": blank},
                {"id": "art_2", "number": "Article 2", "content": SYN + "article two"},
            ]
        }
    )
    first, second = passages.from_document(DocumentResponse(body)).passages
    assert (first.text, first.text_status, first.has_text) == (None, "absent", False)
    assert (second.text_status, second.has_text) == ("provided", True)


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_full_text_of_a_document_gives_no_passage(blank):
    body = document_body(celex="62099CJ9901", content={"fullText": blank})
    assert passages.from_document(DocumentResponse(body)).passages == ()


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_provision_text_of_an_as_of_answer_is_absent(blank):
    body = as_of_body(provision={"id": "art_17", "number": "Article 17", "text": blank})
    (p,) = passages.from_legislation_as_of(LegislationAsOfResponse(body)).passages
    assert (p.text, p.text_status, p.has_text) == (None, "absent", False)


@pytest.mark.parametrize("text", ["a", " x ", "​x", "§ 1"])
def test_text_with_a_visible_character_is_provided_unchanged(text):
    hit = without_as_of(hit_as_published())
    hit["text"] = text
    (p,) = passages.from_legislation_search(leg(hit, as_of=None)).passages
    assert (p.text, p.text_status, p.has_text) == (text, "provided", True)


def _format_code_points():
    import unicodedata

    return [chr(c) for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c)) == "Cf"]


def test_every_format_character_of_this_python_is_blank():
    from lexapi.passages import _present

    chars = _format_code_points()
    assert chars  # the running Python knows Cf characters
    for ch in chars:
        assert _present(ch) is None, f"U+{ord(ch):04X}"
        assert _present(" " + ch + "\n") is None, f"U+{ord(ch):04X}"
    assert _present("".join(chars)) is None


def test_tag_characters_are_blank():
    from lexapi.passages import _present

    for code in range(0xE0020, 0xE0080):
        assert _present(chr(code)) is None, f"U+{code:04X}"
    assert _present(chr(0xE0001)) is None


def test_hand_built_passages_are_not_normalised():
    for blank in ("", "  ", "\u200b"):
        p = Passage(kind="legislation", celex="32099R0002", text=blank, text_status="provided")
        assert (p.text, p.text_status, p.has_text, p.content()) == (blank, "provided", True, blank)
