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
  established as the text for the date, so by default it is withheld: the
  passage is kept, ``text`` is ``None`` and ``as_of_reason`` says why. Only
  the passages are filtered: ``response.results`` still holds the latest text
  of unresolved hits.
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
    result = passages.from_legislation_search(response, as_of="2019-06-01")
    for passage in result.passages:
        print(passages.format_passage(passage))

Building an adapter
-------------------

- **Document text.** An adapter whose framework does not show metadata to
  the model uses :func:`format_passage` as the document text. Otherwise use
  :meth:`Passage.content` and :meth:`Passage.metadata`; add only the
  framework's own aliases to the metadata.
- **Errors** are never turned into an empty list: a failed payment or an
  exhausted credit pool must not read as "no law found". Let the SDK's
  errors propagate.
- **k** is the number of hits requested. A result can hold fewer passages
  with text than hits. Do not ask for more hits to fill ``k``: on an
  ``as_of`` search, fetching versions costs credits.
- **Score** is passed through unchanged. A withheld passage keeps the score
  of the text that was withheld.
- **Credits.** ``units_charged`` is what the call cost, once per call, not
  per passage.
- **Text.** Legislation and case-law searches carry text only when sent with
  ``include_text=True``. A case-law text is the whole decision: chunking and
  truncation are the adapter's job.
- **Documents.** :func:`from_document` gives the articles of a document
  read; its annexes and recitals are left out.
- **id.** :attr:`Passage.id` is stable across calls but not unique within
  a result (two hits of one decision without a paragraph number share it),
  and it does not say whether the passage has text or what its ``kind`` is:
  a withheld passage and a later resolved one for the same article and date
  share it. De-duplicate in the adapter; do not store a notice over a text,
  and do not index withheld passages.
- **kind** says which call produced the passage, not what the document is:
  a ``"document_read"`` passage can be legislation or case law.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from lexapi._payloads import DateLike, iso_date
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
    "format_passage",
    "from_case_law_search",
    "from_document",
    "from_legislation_as_of",
    "from_legislation_search",
    "restrict_to_celex",
]

#: Which call produced a passage: a legislation search or
#: ``legislation_as_of`` answer (``"legislation"``), a case-law search
#: (``"case_law"``), or a document read (``"document_read"``, which can be
#: legislation or case law).
PassageKind = Literal["legislation", "case_law", "document_read"]

#: What to do with a hit whose text is not established for the date asked:
#: ``"withhold"`` keeps the passage without its text (the default),
#: ``"drop"`` leaves it out and counts it, ``"keep_labelled"`` keeps the
#: latest indexed text and snippet and marks the passage
#: ``not_established_for_date``.
UnresolvedPolicy = Literal["withhold", "drop", "keep_labelled"]

#: State of :attr:`Passage.text`: ``"provided"`` (text from the API),
#: ``"withheld"`` (not established as the text for the date asked, so not
#: passed on), ``"not_established_for_date"`` (the latest indexed text, kept
#: on request; it is not established as the text for the date) or
#: ``"absent"`` (the API sent no text).
TextStatus = Literal["provided", "withheld", "not_established_for_date", "absent"]

_POLICIES = ("withhold", "drop", "keep_labelled")

# Reasons that say the act itself was not in force on the date.
_NOT_IN_FORCE_REASONS = ("not_yet_adopted", "not_yet_in_force", "no_longer_in_force")

# A consolidated text's CELEX number: sector 0 and a consolidation date
# (``02016R0679-20160504``).
_CONSOLIDATED_CELEX = re.compile(r"^0\d{4}[A-Z]{1,2}\d{4}.*-\d{8}$")

# Body keys that show a date was asked for.
_AS_OF_KEYS = ("asOf", "asOfResolved", "asOfReason")

# Fields of a passage that go into its metadata as they are.
_METADATA_FIELDS = (
    "kind",
    "celex",
    "text_status",
    "consolidated",
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
)


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
    #: The document's page at its publisher (EUR-Lex). The page shows what
    #: the publisher serves today, also for a withheld passage.
    url: str | None = None
    #: ELI of the document.
    eli: str | None = None
    #: Similarity score of a search hit, unchanged. A withheld passage keeps
    #: the score of the text that was withheld.
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
    #: The date the API answered for, as it echoed it. It differs from
    #: ``as_of`` when the API did not answer for the date asked; the
    #: ``as_of_reason`` is then about this date, not about ``as_of``. Not
    #: part of :meth:`metadata`.
    as_of_echoed: str | None = None

    @property
    def cite_celex(self) -> str | None:
        """The CELEX number to cite: ``celex``, or ``base_celex`` for a
        consolidated text. ``None`` for a consolidated text whose act the API
        did not name: the act to cite is then not known."""
        if self.consolidated:
            return self.base_celex or None
        return self.celex

    @property
    def text_withheld(self) -> bool:
        """``True`` when the text was not passed on because it is not
        established for ``as_of``."""
        return self.text_status == "withheld"

    @property
    def has_text(self) -> bool:
        """``True`` when ``text`` holds a text."""
        return self.text is not None

    @property
    def id(self) -> str:
        """An identifier for the passage:
        ``lexapi:<celex>:<part>:<as_of>:<language>``.

        ``<part>`` is ``article_ref``, else ``paragraph_number``, else empty;
        an unknown ``as_of`` or ``language`` is empty. It is built from the
        citation only, never from the text, so it is the same on every call
        that returns the same passage and differs between dates.

        It is not unique within a result: two hits of one decision without a
        paragraph number share it. It does not say whether the passage has
        text, nor its ``kind``: a withheld passage and a later resolved one
        for the same article and date share it. De-duplicate in the adapter;
        do not store a notice over a text, and do not index withheld
        passages.
        """
        part = self.article_ref
        if not part and self.paragraph_number is not None:
            part = str(self.paragraph_number)
        return f"lexapi:{self.celex}:{part or ''}:{self.as_of or ''}:{self.language or ''}"

    def content(self) -> str:
        """The text to hand to a model, or a one-line notice when there is
        none. The ``snippet`` is never used.

        - Text: the text.
        - Withheld: ``[No text: CELEX <celex> <part> is not established as
          the text for <as_of> (reason: <as_of_reason>).]``, or, when the
          reason says the act was not in force on that date, ``[No text: CELEX <celex>
          <part> was not in force on <as_of> (reason: <as_of_reason>).]``
        - Kept with ``unresolved="keep_labelled"``: ``[Latest indexed text of
          CELEX <celex> <part>; not established as the text for <as_of>
          (reason: <as_of_reason>).]`` (or ``; the act was not in force on
          <as_of> (...)``), a line break, then the text.
        - No text sent: ``[No text returned for CELEX <celex> <part>.]``, or,
          for a ``legislation_as_of`` answer without a provision,
          ``[Version <version_celex> (<version_kind>) is the version listed
          for <as_of>; no provision was requested.]``
        """
        subject = _subject(self)
        if self.text_status == "withheld":
            return f"[No text: {subject} {_date_clause(self, 'is', 'was')}]"
        if self.text is None:
            if self.as_of_resolved is True and self.version_celex and not self.article_ref:
                kind = f" ({self.version_kind})" if self.version_kind else ""
                return (
                    f"[Version {self.version_celex}{kind} is the version listed for "
                    f"{_date(self)}; no provision was requested.]"
                )
            return f"[No text returned for {subject}.]"
        if self.text_status == "not_established_for_date":
            clause = _date_clause(self, "", "the act was")
            return f"[Latest indexed text of {subject}; {clause}]\n{self.text}"
        return self.text

    def metadata(self) -> dict[str, str | int | float | bool]:
        """The passage's citation as a flat dictionary of JSON scalars.

        Never holds ``text`` or ``snippet``. Always present: ``kind``,
        ``celex``, ``text_status``, ``text_withheld``, ``consolidated``,
        ``citation`` (:func:`format_citation`) and ``id``. ``cite_celex``
        and every other field are present when known; a key whose value is
        not known is left out. Adapters add only their framework's aliases.
        """
        out: dict[str, str | int | float | bool] = {}
        for name in _METADATA_FIELDS:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        out["text_withheld"] = self.text_withheld
        cite = self.cite_celex
        if cite is not None:
            out["cite_celex"] = cite
        out["citation"] = format_citation(self)
        out["id"] = self.id
        return out


@dataclass(frozen=True)
class PassageResult:
    """The passages of one response, with what the call reported.

    ``withheld``, ``kept_labelled`` and ``dropped`` count the hits whose text
    is not established for ``as_of``, by what was done with them.
    """

    passages: tuple[Passage, ...]
    #: The date asked for (``YYYY-MM-DD``); ``None`` when no date was asked.
    as_of: str | None = None
    #: Passages without text because they are not resolved for ``as_of``.
    withheld: int = 0
    #: Passages kept with the latest indexed text (``"keep_labelled"``).
    kept_labelled: int = 0
    #: Hits left out by ``unresolved="drop"``.
    dropped: int = 0
    #: Hits left out because the API sent no CELEX number for them.
    uncitable: int = 0
    #: Passages removed by :func:`restrict_to_celex`.
    filtered_out: int = 0
    #: Credits the call cost, as the response reports them, once per call;
    #: ``None`` when the response reports none.
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
    ``text_status="not_established_for_date"``. Only the passages are
    filtered: ``response.results`` still holds the latest text of unresolved
    hits.

    A date counts as asked when ``as_of`` is given here, or the response or
    the hit carries any of ``asOf``, ``asOfResolved`` or ``asOfReason``.
    Pass the ``as_of`` you sent to have every hit checked against it: a hit
    that does not echo that date as resolved is treated as unresolved. Search
    with ``include_text=True`` to get full texts.
    """
    _check_policy(unresolved)
    asked = iso_date(as_of)
    response_as_of = response.as_of
    dated = asked is not None or _has_as_of_keys(response)
    out: list[Passage] = []
    counts = {"withheld": 0, "kept_labelled": 0, "dropped": 0, "uncitable": 0}
    for hit in response.results:
        if not hit.law_id:
            counts["uncitable"] += 1
            continue
        passage = _legislation_hit(hit, response_as_of, asked, dated, unresolved)
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
    consolidates, so ``base_celex`` and ``cite_celex`` are ``None``.
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
    provision: str | None = None,
    unresolved: UnresolvedPolicy = "withhold",
) -> PassageResult:
    """The passage of a ``legislation_as_of`` answer.

    An answer that is not resolved carries no text: the passage has
    ``text=None``, ``text_status="withheld"`` and the ``as_of_reason``, as an
    unresolved search hit does. ``unresolved="drop"`` leaves it out and
    counts it; ``"keep_labelled"`` has no text to keep and acts as
    ``"withhold"``. A resolved answer without a ``provision`` names the
    version and has no text.

    An unresolved answer does not say which provision was asked for. Pass
    the ``provision`` you sent to have it as the passage's ``article_ref``.
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
    answered = response.provision if resolved else None
    text = answered.text if answered is not None else None
    celex = version.celex if resolved and version is not None and version.celex else document
    consolidated = response.consolidated or _is_consolidated_celex(celex)
    status: TextStatus = "withheld" if not resolved else ("absent" if text is None else "provided")
    passage = Passage(
        kind="legislation",
        celex=celex,
        text=text,
        text_status=status,
        article_ref=answered.id if answered is not None else (None if resolved else provision),
        article_number=answered.number if answered is not None else None,
        article_title=answered.title if answered is not None else None,
        language=response.language,
        url=_source_url(response.provenance),
        consolidated=consolidated,
        base_celex=response.base_celex if consolidated else None,
        consolidated_as_of=response.consolidated_as_of if consolidated else None,
        consolidated_note=response.consolidated_note if consolidated else None,
        as_of=as_of,
        as_of_resolved=resolved,
        as_of_reason=response.as_of_reason,
        as_of_echoed=as_of,
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

    Numbers are compared whole, ignoring case, surrounding spaces and a
    leading ``CELEX:``. Giving no number, or a value that is not a string,
    raises ``ValueError``.
    """
    values = [celex] if isinstance(celex, str) else list(celex)
    if not all(isinstance(value, str) for value in values):
        raise ValueError("restrict_to_celex takes CELEX numbers as strings")
    wanted = {_bare_celex(value) for value in values}
    wanted.discard("")
    if not wanted:
        raise ValueError("restrict_to_celex needs at least one CELEX number")
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

        CELEX <cite_celex>, <article_ref>, <ECLI>, para. <n>, <consolidated>, <date>, <url>

    ``<consolidated>`` is ``consolidated text <celex> (no legal force)``.
    When the API did not name the act a consolidated text consolidates, the
    line starts with ``consolidated text <celex> (no legal force; act not
    named)`` instead of a CELEX number to cite. ``<date>`` is ``version for
    <as_of>: <version_celex>`` on a resolved passage and ``text for <as_of>
    not established`` on one that is not. For example::

        CELEX 32016R0679, art_17, https://eur-lex.europa.eu/...
        CELEX 32016R0679, art_17, consolidated text 02016R0679-20160504 (no legal force)
        CELEX 32016R0679, art_17, version for 2019-06-01: 32016R0679
        CELEX 31995L0046, art_12, text for 2019-06-01 not established
        consolidated text 02016R0679-20160504 (no legal force; act not named), art_17
        CELEX 62012CJ0131, ECLI:EU:C:2014:317, para. 93

    No title or heading is added; an adapter may put ``title`` or
    ``article_number`` in front when present.
    """
    parts: list[str] = []
    cite = passage.cite_celex
    if cite is not None:
        parts.append(f"CELEX {cite}")
    else:
        parts.append(f"consolidated text {passage.celex} (no legal force; act not named)")
    if passage.article_ref:
        parts.append(passage.article_ref)
    if passage.ecli:
        parts.append(passage.ecli)
    if passage.paragraph_number is not None:
        parts.append(f"para. {passage.paragraph_number}")
    if passage.consolidated and cite is not None:
        parts.append(f"consolidated text {passage.celex} (no legal force)")
    if passage.as_of_resolved is True:
        version = f": {passage.version_celex}" if passage.version_celex else ""
        parts.append(f"version for {_date(passage)}{version}")
    elif passage.as_of_resolved is False:
        parts.append(f"text for {_date(passage)} not established")
    if passage.url:
        parts.append(passage.url)
    return ", ".join(parts)


def format_passage(passage: Passage) -> str:
    """The passage as one text: :meth:`Passage.content`, a line break, then
    ``Source:`` and :func:`format_citation`.

    An adapter whose framework does not show metadata to the model uses this
    as the document text.
    """
    return f"{passage.content()}\nSource: {format_citation(passage)}"


# ── internals ───────────────────────────────────────────────────────────


def _check_policy(unresolved: str) -> None:
    if unresolved not in _POLICIES:
        raise ValueError(f"unresolved must be one of {', '.join(_POLICIES)}; got {unresolved!r}")


def _has_as_of_keys(body: Mapping[str, Any]) -> bool:
    return any(body.get(key) is not None for key in _AS_OF_KEYS)


def _bare_celex(value: str) -> str:
    value = value.strip().upper()
    if value.startswith("CELEX:"):
        value = value[len("CELEX:") :].strip()
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


def _subject(passage: Passage) -> str:
    part = f" {passage.article_ref}" if passage.article_ref else ""
    return f"CELEX {passage.celex}{part}"


def _date(passage: Passage) -> str:
    return passage.as_of or "the date asked"


def _date_clause(passage: Passage, verb: str, not_in_force_verb: str) -> str:
    reason = f" (reason: {passage.as_of_reason})" if passage.as_of_reason else ""
    # A reason is a statement about the date the API answered for.
    judged = passage.as_of is not None and passage.as_of_echoed == passage.as_of
    if judged and passage.as_of_reason in _NOT_IN_FORCE_REASONS:
        return f"{not_in_force_verb} not in force on {_date(passage)}{reason}."
    lead = f"{verb} " if verb else ""
    return f"{lead}not established as the text for {_date(passage)}{reason}."


def _legislation_hit(
    hit: SemanticLegislationResult,
    response_as_of: str | None,
    asked: str | None,
    dated: bool,
    policy: UnresolvedPolicy,
) -> Passage | None:
    celex = hit.law_id or ""
    echoed = hit.as_of or response_as_of
    date_asked = dated or _has_as_of_keys(hit.raw)
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
        as_of=((asked or echoed) if date_asked else None) or None,
        as_of_resolved=resolved if date_asked else None,
        as_of_reason=hit.as_of_reason,
        as_of_echoed=(echoed if date_asked else None) or None,
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
        kind="document_read",
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
