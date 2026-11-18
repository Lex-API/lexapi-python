"""Cited passages for retrieval-augmented generation (RAG) frameworks.

Turns LexAPI responses into framework-neutral :class:`Passage` objects: a
piece of text together with the citation it must be shown with. The module
makes no request and reads nothing but the response objects it is given.

The rules it applies:

- **Citation.** Every passage names the CELEX number of its document. A hit
  without one is not turned into a passage; it is counted in
  :attr:`PassageResult.uncitable`.
- **Text for a date.** On an ``as_of`` search the API returns a hit it could
  not answer for that date with its latest indexed text. That text is not
  established as the text in force on the date, so by default it is withheld:
  the passage is kept, ``text`` is ``None`` and ``as_of_reason`` says why.
- **Consolidated texts** have no legal force. Such a passage has
  ``consolidated=True`` and, when the API names it, ``base_celex``: the act
  as published in the Official Journal, which is the one to cite.
- **Nothing is guessed.** A field the API did not send is ``None``.

Example::

    from lexapi import LexAPI
    from lexapi import passages

    with LexAPI() as client:
        response = client.semantic_legislation_search(
            "right to erasure", as_of="2019-06-01", include_text=True
        )
    result = passages.from_legislation_search(response)
    for passage in result.passages:
        print(passages.format_citation(passage))
        print(passage.content())
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Literal, Union

from lexapi._response import APIResponse
from lexapi.models import (
    Article,
    AsOfVersion,
    Document,
    DocumentResponse,
    LegislationAsOfResponse,
    SemanticCaseLawResult,
    SemanticLegislationResponse,
    SemanticLegislationResult,
    SemanticSearchResponse,
)

__all__ = [
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

#: What a passage is taken from: a legislation search or ``legislation_as_of``
#: answer, a case-law search, or a document read.
PassageKind = Literal["legislation", "case_law", "document"]

#: What to do with a hit whose text is not established for the date asked:
#: ``"withhold"`` keeps the passage without its text (the default),
#: ``"drop"`` leaves it out and counts it, ``"keep_labelled"`` keeps the
#: latest indexed text and snippet and marks the passage
#: ``not_established_for_date``.
UnresolvedPolicy = Literal["withhold", "drop", "keep_labelled"]

#: State of :attr:`Passage.text`: ``"provided"`` (text from the API),
#: ``"withheld"`` (not established for the date asked, so not passed on),
#: ``"not_established_for_date"`` (the latest indexed text, kept on request;
#: it is not established as the text in force on the date) or ``"absent"``
#: (the API sent no text).
TextStatus = Literal["provided", "withheld", "not_established_for_date", "absent"]

DateLike = Union[str, _dt.date]

_POLICIES = ("withhold", "drop", "keep_labelled")

# A consolidated text's CELEX number: sector 0 and a consolidation date
# (``02016R0679-20160504``).
_CONSOLIDATED_CELEX = re.compile(r"^0\d{4}[A-Z]{1,2}\d{4}.*-\d{8}$")


@dataclass(frozen=True)
class Passage:
    """One piece of text with the citation it must be shown with.

    Immutable. ``celex`` is always set; every other field is ``None`` when
    the API did not send it.

    ``celex`` is the CELEX number of the document the text is from, which can
    be a consolidated text. :attr:`cite_celex` is the number to cite.
    """

    #: See :data:`PassageKind`.
    kind: PassageKind
    #: CELEX number of the document the text is from.
    celex: str
    #: The text, or ``None`` when it is withheld or the API sent none.
    text: str | None
    #: See :data:`TextStatus`.
    text_status: TextStatus
    #: Short preview sent by the API. Withheld together with ``text``. For
    #: case law it can be the start of a machine-written summary.
    snippet: str | None = None
    #: The part matched: an article (``art_17``), an annex (``anx_III``) or
    #: recitals (``rct_26-28``).
    article_ref: str | None = None
    #: The part's heading as printed (``Article 17``).
    article_number: str | None = None
    #: The part's title.
    article_title: str | None = None
    #: Title of the act, or name of the case.
    title: str | None = None
    #: Language of the text (ISO 639-1).
    language: str | None = None
    #: The document's page at its publisher (EUR-Lex).
    url: str | None = None
    #: ELI of the document.
    eli: str | None = None
    #: Similarity score of a search hit.
    score: float | None = None
    #: ``True`` for a consolidated text. Consolidated texts have no legal
    #: force; cite ``base_celex``.
    consolidated: bool = False
    #: The act a consolidated text consolidates, as published in the Official
    #: Journal.
    base_celex: str | None = None
    #: Consolidation date (``YYYY-MM-DD``).
    consolidated_as_of: str | None = None
    #: The API's note that a consolidated text is not the authentic one.
    consolidated_note: str | None = None
    #: The date asked for (``YYYY-MM-DD``); ``None`` when no date was asked.
    as_of: str | None = None
    #: ``True`` when the text is established for ``as_of``, ``False`` when it
    #: is not, ``None`` when no date was asked.
    as_of_resolved: bool | None = None
    #: Why the text is not established for ``as_of``.
    as_of_reason: str | None = None
    #: CELEX number of the version listed for ``as_of``.
    version_celex: str | None = None
    #: ``"consolidated"`` or ``"as_published"``.
    version_kind: str | None = None
    #: The date the consolidated version carries.
    version_date: str | None = None
    #: Last day this version is the one listed.
    version_valid_until: str | None = None
    #: Case law: ECLI.
    ecli: str | None = None
    #: Case law: case number.
    case_number: str | None = None
    #: Case law: court.
    court: str | None = None
    #: Case law: date of the decision.
    decision_date: str | None = None
    #: Case law: ``judgment``, ``order``, ``ag_opinion``, ...
    document_type: str | None = None
    #: Case law: position of the match within the decision.
    paragraph_number: int | None = None

    @property
    def cite_celex(self) -> str:
        """The CELEX number to cite: ``base_celex`` for a consolidated text
        when the API names it, ``celex`` otherwise."""
        if self.consolidated and self.base_celex:
            return self.base_celex
        return self.celex

    @property
    def text_withheld(self) -> bool:
        """``True`` when the text was not passed on because it is not
        established for ``as_of``."""
        return self.text_status == "withheld"

    def content(self) -> str:
        """The text to hand to a model.

        A withheld text gives a one-line notice instead of text. A text kept
        with ``unresolved="keep_labelled"`` is preceded by a line saying it
        is not established for the date. A passage without text gives ``""``;
        the ``snippet`` is never used.
        """
        if self.text_status == "withheld":
            return f"[Text withheld: {_not_established(self)}]"
        if self.text is None:
            return ""
        if self.text_status == "not_established_for_date":
            return f"[Latest indexed text; {_not_established(self)}]\n{self.text}"
        return self.text


@dataclass(frozen=True)
class PassageResult:
    """The passages of one response, with what the call reported.

    ``withheld``, ``kept_labelled`` and ``dropped`` count the hits whose text
    is not established for ``as_of``, by what was done with them.
    """

    passages: tuple[Passage, ...]
    #: The date asked for (``YYYY-MM-DD``); ``None`` when no date was asked.
    as_of: str | None = None
    #: Passages kept without their text.
    withheld: int = 0
    #: Passages kept with the latest indexed text (``"keep_labelled"``).
    kept_labelled: int = 0
    #: Hits left out by ``unresolved="drop"``.
    dropped: int = 0
    #: Hits left out because the API sent no CELEX number for them.
    uncitable: int = 0
    #: Passages removed by :func:`restrict_to_celex`.
    filtered_out: int = 0
    #: Credits the call cost, as the response reports them; ``None`` when the
    #: response reports none.
    units_charged: int | None = None
    #: Credits left, as the response reports them.
    credits_remaining: int | None = None
    #: The response's low-confidence hint, when it has one.
    hint: str | None = None


def from_legislation_search(
    response: SemanticLegislationResponse,
    *,
    unresolved: UnresolvedPolicy = "withhold",
    as_of: DateLike | None = None,
) -> PassageResult:
    """Passages from ``semantic_legislation_search``.

    A hit that is not marked resolved for the date asked never passes its
    text on by default: ``unresolved="withhold"`` keeps the passage with
    ``text=None`` and its ``as_of_reason``. ``"drop"`` leaves such hits out
    and counts them; ``"keep_labelled"`` keeps the latest indexed text with
    ``text_status="not_established_for_date"``.

    Pass the ``as_of`` you sent to have every hit checked against it: a hit
    that does not echo that date as resolved is treated as unresolved. Search
    with ``include_text=True`` to get full texts.
    """
    _check_policy(unresolved)
    asked = _iso(as_of)
    response_as_of = response.as_of
    out: list[Passage] = []
    counts = {"withheld": 0, "kept_labelled": 0, "dropped": 0, "uncitable": 0}
    for hit in response.results:
        if not hit.law_id:
            counts["uncitable"] += 1
            continue
        passage = _legislation_hit(hit, response_as_of, asked, unresolved)
        if passage is None:
            counts["dropped"] += 1
            continue
        if passage.text_status == "withheld":
            counts["withheld"] += 1
        elif passage.text_status == "not_established_for_date":
            counts["kept_labelled"] += 1
        out.append(passage)
    return PassageResult(
        passages=tuple(out),
        as_of=asked or response_as_of,
        hint=response.hint,
        **counts,
        **_credits(response),
    )


def from_case_law_search(response: SemanticSearchResponse) -> PassageResult:
    """Passages from ``semantic_search`` (case law).

    ``text`` is the full text of the decision, present only when the search
    asked for it (``include_text=True``). The ``snippet`` can be the start of
    a machine-written summary and is never copied into ``text``; the summary
    itself is not carried.
    """
    out: list[Passage] = []
    uncitable = 0
    for hit in response.results:
        if not hit.celex:
            uncitable += 1
            continue
        out.append(_case_law_hit(hit))
    return PassageResult(
        passages=tuple(out), uncitable=uncitable, hint=response.hint, **_credits(response)
    )


def from_document(response: DocumentResponse) -> PassageResult:
    """Passages from ``get_document``: one per article returned.

    A document that is not split into articles (case law, for example) gives
    one passage with its full text, when the response carries it. Annexes and
    recitals of a document read are not turned into passages.

    A consolidated text is recognised by its CELEX number
    (``02016R0679-20160504``). A document read does not name the act it
    consolidates, so ``base_celex`` is ``None``.
    """
    document = response.document
    if not document.celex:
        return PassageResult(passages=(), uncitable=1, **_credits(response))
    out: list[Passage] = []
    content = document.content
    if content is not None:
        if content.articles:
            out.extend(_document_passage(document, article) for article in content.articles)
        elif content.full_text is not None:
            out.append(_document_passage(document, None))
    return PassageResult(passages=tuple(out), **_credits(response))


def from_legislation_as_of(
    response: LegislationAsOfResponse,
    *,
    unresolved: UnresolvedPolicy = "withhold",
) -> PassageResult:
    """The passage of a ``legislation_as_of`` answer.

    An answer that is not resolved carries no text: the passage has
    ``text=None``, ``text_status="withheld"`` and the ``as_of_reason``, as an
    unresolved search hit does. ``unresolved="drop"`` leaves it out and
    counts it; ``"keep_labelled"`` has no text to keep and acts as
    ``"withhold"``. A resolved answer without a ``provision`` names the
    version and has no text.
    """
    _check_policy(unresolved)
    credits = _credits(response)
    as_of = response.as_of
    document = response.document
    if not document:
        return PassageResult(passages=(), as_of=as_of, uncitable=1, **credits)
    resolved = response.as_of_resolved
    if not resolved and unresolved == "drop":
        return PassageResult(passages=(), as_of=as_of, dropped=1, **credits)
    version = response.as_of_version
    provision = response.provision if resolved else None
    text = provision.text if provision is not None else None
    celex = version.celex if resolved and version is not None and version.celex else document
    consolidated = response.consolidated or _is_consolidated_celex(celex)
    status: TextStatus = "withheld" if not resolved else ("absent" if text is None else "provided")
    passage = Passage(
        kind="legislation",
        celex=celex,
        text=text,
        text_status=status,
        article_ref=provision.id if provision is not None else None,
        article_number=provision.number if provision is not None else None,
        article_title=provision.title if provision is not None else None,
        language=response.language,
        url=_source_url(response.provenance),
        consolidated=consolidated,
        base_celex=response.base_celex if consolidated else None,
        consolidated_as_of=response.consolidated_as_of if consolidated else None,
        consolidated_note=response.consolidated_note if consolidated else None,
        as_of=as_of,
        as_of_resolved=resolved,
        as_of_reason=response.as_of_reason,
        **_version_fields(version),
    )
    return PassageResult(passages=(passage,), as_of=as_of, withheld=0 if resolved else 1, **credits)


def restrict_to_celex(result: PassageResult, celex: str | Iterable[str]) -> PassageResult:
    """Keep only the passages of the given act or acts.

    The legislation index reads no ``filters`` key except ``language``, so a
    search cannot be limited to one act; this filters its passages
    afterwards. A passage is kept when its ``celex`` or its ``base_celex`` is
    one of the numbers given, so the consolidated texts of an act are kept
    with it. The number removed is added to ``filtered_out``. The result can
    be shorter than the search's ``limit``.
    """
    wanted = {celex} if isinstance(celex, str) else set(celex)
    wanted = {value.strip().upper() for value in wanted if value.strip()}
    kept: list[Passage] = []
    removed: list[Passage] = []
    for passage in result.passages:
        base = passage.base_celex
        match = passage.celex.upper() in wanted or (base is not None and base.upper() in wanted)
        (kept if match else removed).append(passage)
    return replace(
        result,
        passages=tuple(kept),
        filtered_out=result.filtered_out + len(removed),
        withheld=result.withheld - sum(p.text_status == "withheld" for p in removed),
        kept_labelled=result.kept_labelled
        - sum(p.text_status == "not_established_for_date" for p in removed),
    )


def format_citation(passage: Passage) -> str:
    """The passage's citation as one line for a prompt.

    The parts, in this order and separated by ``", "``, each left out when
    not known::

        CELEX <cite_celex>, <article_ref>, <ECLI>, para. <n>, <note>, <url>

    ``<note>`` is present for a consolidated text only:
    ``consolidated text <celex> (no legal force)``. For example::

        CELEX 32016R0679, art_17, https://eur-lex.europa.eu/...
        CELEX 32016R0679, art_17, consolidated text 02016R0679-20160504 (no legal force)
        CELEX 62012CJ0131, ECLI:EU:C:2014:317, para. 93
    """
    parts = [f"CELEX {passage.cite_celex}"]
    if passage.article_ref:
        parts.append(passage.article_ref)
    if passage.ecli:
        parts.append(passage.ecli)
    if passage.paragraph_number is not None:
        parts.append(f"para. {passage.paragraph_number}")
    if passage.consolidated:
        parts.append(f"consolidated text {passage.celex} (no legal force)")
    if passage.url:
        parts.append(passage.url)
    return ", ".join(parts)


# ── internals ───────────────────────────────────────────────────────────


def _check_policy(unresolved: str) -> None:
    if unresolved not in _POLICIES:
        raise ValueError(f"unresolved must be one of {', '.join(_POLICIES)}; got {unresolved!r}")


def _iso(value: DateLike | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return value


def _credits(response: APIResponse) -> dict[str, Any]:
    credits = response.credits
    return {
        "units_charged": credits.operation_weight if credits is not None else None,
        "credits_remaining": response.credits_remaining,
    }


def _source_url(provenance: Any) -> str | None:
    if not isinstance(provenance, dict):
        return None
    value = provenance.get("sourceUrl")
    return value if isinstance(value, str) and value else None


def _is_consolidated_celex(celex: str) -> bool:
    return _CONSOLIDATED_CELEX.match(celex.upper()) is not None


def _version_fields(version: AsOfVersion | None) -> dict[str, Any]:
    if version is None:
        return {}
    return {
        "version_celex": version.celex,
        "version_kind": version.kind,
        "version_date": version.date,
        "version_valid_until": version.valid_until,
    }


def _not_established(passage: Passage) -> str:
    date = f" on {passage.as_of}" if passage.as_of else " on the date asked"
    reason = f" (reason: {passage.as_of_reason})" if passage.as_of_reason else ""
    return f"not established as the text in force{date}{reason}."


def _legislation_hit(
    hit: SemanticLegislationResult,
    response_as_of: str | None,
    asked: str | None,
    policy: UnresolvedPolicy,
) -> Passage | None:
    celex = hit.law_id or ""
    echoed = hit.as_of or response_as_of
    date_asked = (
        asked is not None
        or echoed is not None
        or hit.as_of_resolved is not None
        or hit.as_of_reason is not None
    )
    # Resolved only when the API says so, for the date that was asked.
    resolved = hit.as_of_resolved is True and (asked is None or echoed == asked)
    not_established = date_asked and not resolved
    if not_established and policy == "drop":
        return None

    text, snippet = hit.text, hit.snippet
    status: TextStatus = "absent" if text is None else "provided"
    if not_established:
        if policy == "keep_labelled":
            status = "not_established_for_date"
        else:
            text, snippet, status = None, None, "withheld"

    version = hit.as_of_version
    consolidated = (
        hit.consolidated
        or (version is not None and version.kind == "consolidated")
        or _is_consolidated_celex(celex)
    )
    return Passage(
        kind="legislation",
        celex=celex,
        text=text,
        text_status=status,
        snippet=snippet,
        article_ref=hit.article_ref,
        title=hit.law_title,
        language=hit.language,
        url=_source_url(hit.raw.get("provenance")),
        score=hit.score,
        consolidated=consolidated,
        base_celex=hit.base_celex if consolidated else None,
        consolidated_as_of=hit.consolidated_as_of if consolidated else None,
        consolidated_note=hit.consolidated_note if consolidated else None,
        as_of=(asked or echoed) if date_asked else None,
        as_of_resolved=resolved if date_asked else None,
        as_of_reason=hit.as_of_reason,
        **_version_fields(version),
    )


def _case_law_hit(hit: SemanticCaseLawResult) -> Passage:
    return Passage(
        kind="case_law",
        celex=hit.celex or "",
        text=hit.text,
        text_status="absent" if hit.text is None else "provided",
        snippet=hit.snippet,
        title=hit.case_name,
        language=hit.language,
        url=_source_url(hit.raw.get("provenance")),
        score=hit.score,
        ecli=hit.ecli,
        case_number=hit.case_number,
        court=hit.court,
        decision_date=hit.decision_date,
        document_type=hit.document_type,
        paragraph_number=hit.paragraph_number,
    )


def _document_passage(document: Document, article: Article | None) -> Passage:
    celex = document.celex or ""
    content = document.content
    text = article.content if article is not None else (content.full_text if content else None)
    html = document.urls.get("html")
    return Passage(
        kind="document",
        celex=celex,
        text=text,
        text_status="absent" if text is None else "provided",
        article_ref=article.id if article is not None else None,
        article_number=article.number if article is not None else None,
        article_title=article.title if article is not None else None,
        title=document.title,
        language=document.language,
        url=_source_url(document.raw.get("provenance"))
        or (html if isinstance(html, str) else None),
        eli=document.eli,
        consolidated=_is_consolidated_celex(celex),
        ecli=document.ecli,
        case_number=document.case_number,
    )
