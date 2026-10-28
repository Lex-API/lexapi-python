"""Typed models over LexAPI response payloads.

Design rules (PLAN.md §3):

- plain dataclasses, no runtime dependencies beyond httpx;
- every model keeps the raw payload on ``.raw`` — the typed layer never
  loses information;
- parsers are tolerant: missing/extra/mis-typed fields degrade to ``None``
  (or empty lists) instead of raising, for forward compatibility.

Typed *response* wrappers subclass :class:`~lexapi._response.APIResponse`,
so the raw body stays accessible as a mapping and the credit-visibility
helpers (``units_charged`` / ``credits_remaining`` / ``resets_at``) are
available on every response. Partial/truncation signals from the API
(``truncated``, ``partial``, ``trimmed``, the ``X-Warning`` header, ...)
are surfaced as attributes — never silently dropped.
"""

from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass, field
from typing import Any, Literal

from lexapi._response import APIResponse, header

#: Why a semantic-legislation hit could not be answered for ``asOf``
#: (``SemanticLegislationResult.as_of_reason``), copied from openapi.yaml.
#: Typed as ``str`` on the model so a reason added by a newer API version
#: never breaks parsing; compare against :data:`AS_OF_REASONS`.
AsOfReason = Literal[
    "not_yet_adopted",
    "not_yet_in_force",
    "no_longer_in_force",
    "in_force_dates_unknown",
    "not_in_language",
    "text_unavailable",
    "article_not_in_version",
    "part_not_resolvable",
    "article_unknown",
    "version_list_unavailable",
    "version_lookup_cap",
    "fetch_budget_exhausted",
    "fetch_pending",
    "fetch_capacity",
    "fetch_failed",
    "fetch_timeout",
    "request_deadline",
    "insufficient_credits",
    "credit_check_failed",
]

#: Every documented ``asOfReason`` value.
AS_OF_REASONS: tuple[str, ...] = AsOfReason.__args__  # type: ignore[attr-defined]

#: ``textAvailability.reason`` values, copied from openapi.yaml.
TextUnavailableReason = Literal[
    "not_in_language",
    "not_published_yet",
    "no_machine_readable_rendition",
    "too_large",
    "unknown",
]


def _s(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _i(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _b(data: dict[str, Any], key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _f(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _opt_dict(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = data.get(key)
    return value if isinstance(value, dict) else None


def _opt_str_list(data: dict[str, Any], key: str) -> list[str] | None:
    """A string list that keeps ``null`` (unknown) distinct from ``[]`` (none)."""
    value = data.get(key)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)]


def _parties(value: Any) -> list[str] | None:
    """Case-law ``parties``: the semantic upstream sends a list of names,
    while the spec documents a string — accept both, return a list."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return [value] if value else []
    return None


def _dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


def _str_list(data: dict[str, Any], key: str) -> list[str]:
    return [item for item in _list(data, key) if isinstance(item, str)]


def _parse_items(data: dict[str, Any], key: str, cls: Any) -> list[Any]:
    return [cls.from_dict(item) for item in _list(data, key) if isinstance(item, dict)]


def _header_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


# ── Document building blocks ────────────────────────────────────────────


@dataclass(frozen=True)
class Article:
    """A top-level article extracted from the document body."""

    id: str | None
    number: str | None
    title: str | None
    content: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Article:
        return cls(
            id=_s(data, "id"),
            number=_s(data, "number"),
            title=_s(data, "title"),
            content=_s(data, "content"),
            raw=data,
        )


@dataclass(frozen=True)
class Recital:
    """A numbered prose block — preamble recital or judgment ground.

    Used for both ``recitals[]`` (legislation) and ``paragraphs[]``
    (case-law grounds / fullText fallback); the two arrays are mutually
    exclusive on a given document.
    """

    number: int | None
    text: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recital:
        return cls(number=_i(data, "number"), text=_s(data, "text"), raw=data)


@dataclass(frozen=True)
class Section:
    """Chapter/section hierarchy node."""

    type: str | None
    number: str | None
    title: str | None
    article_range: list[str] | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Section:
        rng = data.get("articleRange")
        article_range = (
            [item for item in rng if isinstance(item, str)] if isinstance(rng, list) else None
        )
        return cls(
            type=_s(data, "type"),
            number=_s(data, "number"),
            title=_s(data, "title"),
            article_range=article_range,
            raw=data,
        )


@dataclass(frozen=True)
class DocumentContent:
    """Structured document body. Any piece may be absent under ``include``/``fields``."""

    full_text: str | None
    articles: list[Article]
    recitals: list[Recital]
    paragraphs: list[Recital]
    sections: list[Section]
    tables: list[dict[str, Any]]
    annexes: list[dict[str, Any]]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentContent:
        return cls(
            full_text=_s(data, "fullText"),
            articles=_parse_items(data, "articles", Article),
            recitals=_parse_items(data, "recitals", Recital),
            paragraphs=_parse_items(data, "paragraphs", Recital),
            sections=_parse_items(data, "sections", Section),
            tables=[t for t in _list(data, "tables") if isinstance(t, dict)],
            annexes=[a for a in _list(data, "annexes") if isinstance(a, dict)],
            raw=data,
        )


@dataclass(frozen=True)
class ConsolidatedVersionHint:
    """A pointer to a consolidated version (CONSLEG, CELEX sector 0) of a base act.

    ``source`` is ``cellar`` (the Publications Office's full list) or
    ``corpus`` (Cellar unreachable — newest version LexAPI holds, languages
    unknown). ``available_languages`` is set on ``newest_consolidated`` only.
    Consolidated versions are a documentation tool; the authentic text is the
    one published in the Official Journal.
    """

    celex: str | None
    date: str | None
    source: str | None
    available_languages: list[str] | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidatedVersionHint:
        return cls(
            celex=_s(data, "celex"),
            date=_s(data, "date"),
            source=_s(data, "source"),
            available_languages=_opt_str_list(data, "availableLanguages"),
            raw=data,
        )

    @classmethod
    def parse(cls, data: dict[str, Any], key: str) -> ConsolidatedVersionHint | None:
        value = data.get(key)
        return cls.from_dict(value) if isinstance(value, dict) else None


@dataclass(frozen=True)
class TextAvailability:
    """Whether a document response carries the document's text, and if not, why.

    Present on every document response. ``content`` is ``None`` when
    ``available`` is False (the call is then billed 1 credit, not 5).
    ``available_languages`` keeps the API's distinction: ``None`` means
    unknown, ``[]`` means known to be none. ``reason`` is one of
    :data:`TextUnavailableReason` (kept as ``str`` for forward compatibility).
    """

    available: bool | None
    reason: str | None
    retryable: bool | None
    available_languages: list[str] | None
    external_source: str | None
    source: str | None
    checked_at: str | None
    latest_consolidated: ConsolidatedVersionHint | None
    upcoming_consolidated: ConsolidatedVersionHint | None
    newest_consolidated: ConsolidatedVersionHint | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextAvailability:
        return cls(
            available=_b(data, "available"),
            reason=_s(data, "reason"),
            retryable=_b(data, "retryable"),
            available_languages=_opt_str_list(data, "availableLanguages"),
            external_source=_s(data, "externalSource"),
            source=_s(data, "source"),
            checked_at=_s(data, "checkedAt"),
            latest_consolidated=ConsolidatedVersionHint.parse(data, "latestConsolidated"),
            upcoming_consolidated=ConsolidatedVersionHint.parse(data, "upcomingConsolidated"),
            newest_consolidated=ConsolidatedVersionHint.parse(data, "newestConsolidated"),
            raw=data,
        )


@dataclass(frozen=True)
class Document:
    """A parsed document: normalized metadata plus (optionally) content.

    Also used for metadata-only responses, where ``content`` is ``None``.
    ISO date variants (``date_of_document_iso``, ...) are preferred over
    the legacy DD/MM/YYYY strings.

    ``content`` is also ``None`` when EUR-Lex publishes the notice but not
    the text — check ``text_availability``. ``title_language`` is set only
    when ``title`` is *not* in the requested language (its ISO code).
    ``latest_consolidated`` / ``upcoming_consolidated`` /
    ``newest_consolidated`` are the metadata endpoint's consolidated-version
    hints (on document reads they live under ``text_availability``).
    """

    celex: str | None
    title: str | None
    document_type: str | None
    document_type_code: str | None
    author: str | None
    date_of_document: str | None
    date_of_document_iso: str | None
    date_of_effect: str | None
    date_of_effect_iso: str | None
    date_of_publication: str | None
    date_of_publication_iso: str | None
    date_type: str | None
    ecli: str | None
    eli: str | None
    case_number: str | None
    parties: str | None
    keywords: list[str]
    directory_codes: list[str]
    subject: list[str]
    language: str | None
    language_requested: str | None
    language_fallback: str | None
    content: DocumentContent | None
    urls: dict[str, Any]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    celex_number: str | None = None
    title_language: str | None = None
    text_availability: TextAvailability | None = None
    fetched_at: str | None = None
    latest_consolidated: ConsolidatedVersionHint | None = None
    upcoming_consolidated: ConsolidatedVersionHint | None = None
    newest_consolidated: ConsolidatedVersionHint | None = None

    @property
    def text_available(self) -> bool | None:
        """``text_availability.available`` (``None`` when the block is absent)."""
        return self.text_availability.available if self.text_availability else None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        content = data.get("content")
        availability = data.get("textAvailability")
        return cls(
            celex=_s(data, "celex") or _s(data, "celexNumber"),
            title=_s(data, "title"),
            document_type=_s(data, "documentType"),
            document_type_code=_s(data, "documentTypeCode"),
            author=_s(data, "author"),
            date_of_document=_s(data, "dateOfDocument"),
            date_of_document_iso=_s(data, "dateOfDocumentISO"),
            date_of_effect=_s(data, "dateOfEffect"),
            date_of_effect_iso=_s(data, "dateOfEffectISO"),
            date_of_publication=_s(data, "dateOfPublication"),
            date_of_publication_iso=_s(data, "dateOfPublicationISO"),
            date_type=_s(data, "dateType"),
            ecli=_s(data, "ecli"),
            eli=_s(data, "eli"),
            case_number=_s(data, "caseNumber"),
            parties=_s(data, "parties"),
            keywords=_str_list(data, "keywords"),
            directory_codes=_str_list(data, "directoryCodes"),
            subject=_str_list(data, "subject"),
            language=_s(data, "language"),
            language_requested=_s(data, "languageRequested"),
            language_fallback=_s(data, "languageFallback"),
            content=DocumentContent.from_dict(content) if isinstance(content, dict) else None,
            urls=_dict(data, "urls"),
            raw=data,
            celex_number=_s(data, "celexNumber"),
            title_language=_s(data, "titleLanguage"),
            text_availability=(
                TextAvailability.from_dict(availability) if isinstance(availability, dict) else None
            ),
            fetched_at=_s(data, "fetchedAt"),
            latest_consolidated=ConsolidatedVersionHint.parse(data, "latestConsolidated"),
            upcoming_consolidated=ConsolidatedVersionHint.parse(data, "upcomingConsolidated"),
            newest_consolidated=ConsolidatedVersionHint.parse(data, "newestConsolidated"),
        )


@dataclass(frozen=True)
class SearchHit:
    """A single entry in a search results page."""

    title: str | None
    celex: str | None
    document_type: str | None
    document_type_code: str | None
    date: str | None
    date_of_document_iso: str | None
    author: str | None
    available_languages: list[str]
    url: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    celex_number: str | None = None
    form: str | None = None
    date_of_document: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchHit:
        return cls(
            title=_s(data, "title"),
            celex=_s(data, "celex") or _s(data, "celexNumber"),
            document_type=_s(data, "documentType") or _s(data, "form"),
            document_type_code=_s(data, "documentTypeCode"),
            date=_s(data, "date"),
            date_of_document_iso=_s(data, "dateOfDocumentISO"),
            author=_s(data, "author"),
            available_languages=_str_list(data, "availableLanguages"),
            url=_s(data, "url"),
            raw=data,
            celex_number=_s(data, "celexNumber"),
            form=_s(data, "form"),
            date_of_document=_s(data, "dateOfDocument"),
        )


@dataclass(frozen=True)
class BatchDocumentError:
    """One failed CELEX from a batch fetch (does not abort the batch)."""

    celex_number: str | None
    error: str | None
    message: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatchDocumentError:
        return cls(
            celex_number=_s(data, "celexNumber"),
            error=_s(data, "error"),
            message=_s(data, "message"),
            raw=data,
        )


@dataclass(frozen=True)
class PostFilterCount:
    """One ``X-PostFiltered-Kept`` entry: rows kept of rows seen."""

    kept: int
    before: int

    @property
    def dropped(self) -> int:
        return self.before - self.kept


def parse_post_filtered_kept(value: str | None) -> dict[str, PostFilterCount]:
    """Parse ``author:3/10;date:2/3`` into ``{"author": (3, 10), ...}``."""
    result: dict[str, PostFilterCount] = {}
    if not value:
        return result
    for part in value.split(";"):
        name, sep, counts = part.strip().partition(":")
        kept, slash, before = counts.partition("/")
        if not (sep and slash and name):
            continue
        try:
            result[name.strip()] = PostFilterCount(int(kept), int(before))
        except ValueError:
            continue
    return result


# ── Typed responses ─────────────────────────────────────────────────────


class SearchResponse(APIResponse):
    """``POST /search`` result page.

    Truncation/partial signals are attributes, never hidden: ``truncated``
    (tier-capped, more pages exist), ``partial`` (an upstream page fetch
    timed out), ``post_filtered_by`` (controller-side post-filter dropped
    rows), plus the ``X-Warning`` header via ``.warning``.
    """

    @property
    def results(self) -> list[SearchHit]:
        return _parse_items(self._map, "results", SearchHit)

    @property
    def total_results(self) -> int | None:
        return _i(self._map, "totalResults")

    @property
    def total_pages(self) -> int | None:
        return _i(self._map, "totalPages")

    @property
    def pages_fetched(self) -> int | None:
        return _i(self._map, "pagesFetched")

    @property
    def result_count(self) -> int | None:
        return _i(self._map, "resultCount")

    @property
    def truncated(self) -> bool:
        """True when the result set was capped by the tier's maxPages."""
        return _b(self._map, "truncated") or False

    @property
    def truncated_at(self) -> int | None:
        return _i(self._map, "truncatedAt")

    @property
    def truncated_reason(self) -> str | None:
        return _s(self._map, "truncatedReason")

    @property
    def partial(self) -> bool:
        """True when an upstream timeout dropped one of the requested pages."""
        return _b(self._map, "partial") or False

    @property
    def partial_reason(self) -> str | None:
        return _s(self._map, "partialReason")

    @property
    def post_filtered_by(self) -> str | None:
        """Comma-joined dimensions a controller-side post-filter fired on."""
        return _s(self._map, "postFilteredBy")

    @property
    def hint(self) -> str | None:
        """Routing hint present on empty result sets (try semantic search)."""
        return _s(self._map, "hint")

    @property
    def ignored_document_types(self) -> list[str]:
        """Requested documentType values that resolve to no EUR-Lex form code."""
        return _str_list(self._map, "ignoredDocumentTypes")

    @property
    def search_parameters(self) -> dict[str, Any]:
        """Echo of the request filters as the server read them."""
        return _dict(self._map, "searchParameters")

    @property
    def source(self) -> str | None:
        """Which upstream answered: ``soap`` (EUR-Lex web service), ``cellar``
        (Publications Office listing, filter-only date windows) or ``corpus``
        (LexAPI mirror while EUR-Lex is unreachable — then ``partial`` is
        True). ``None`` when fetched from EUR-Lex result pages.

        Read from the ``X-Search-Source`` header, falling back to the body's
        ``source`` field (set on corpus answers).
        """
        return header(self.headers, "x-search-source") or _s(self._map, "source")

    @property
    def cache(self) -> str | None:
        """``hit`` / ``miss`` / ``bypass`` — the 60-minute result cache
        (``X-Search-Cache``). Same price either way; pass ``fresh=True`` to
        bypass."""
        return header(self.headers, "x-search-cache")

    @property
    def post_filtered_kept_raw(self) -> str | None:
        """The raw ``X-PostFiltered-Kept`` header, e.g. ``author:3/10;date:2/3``."""
        return header(self.headers, "x-postfiltered-kept")

    @property
    def post_filtered_kept(self) -> dict[str, PostFilterCount]:
        """Per post-filter dimension, how many rows it kept of how many it saw
        (parsed from ``X-PostFiltered-Kept``: ``<dimension>:<kept>/<before>``
        joined by ``;``). Empty when no post-filter fired; malformed parts are
        skipped (the raw value stays on :attr:`post_filtered_kept_raw`)."""
        return parse_post_filtered_kept(self.post_filtered_kept_raw)

    @property
    def document_type_fan_out(self) -> int | None:
        """Size of the EUR-Lex fan-out for multi-value documentType requests."""
        return _header_int(header(self.headers, "x-documenttype-fanout"))

    @property
    def fetched_at(self) -> str | None:
        return _s(self._map, "fetchedAt")


class DocumentResponse(APIResponse):
    """``POST /documentContent`` — one parsed document plus read-path headers."""

    @property
    def document(self) -> Document:
        return Document.from_dict(_dict(self._map, "document"))

    @property
    def source(self) -> str | None:
        """Who answered (the ``X-Source`` header): ``corpus`` (settles at 1
        credit), ``cellar`` / ``cellar-oj`` (Publications Office, 4 credits) or
        ``live`` (browser fetch of EUR-Lex, 5 credits)."""
        return header(self.headers, "x-source")

    @property
    def text_available(self) -> bool | None:
        """False when EUR-Lex publishes the notice but not the text
        (``X-Text-Available: false``; ``document.content`` is then ``None``).
        Falls back to ``document.text_availability.available``."""
        value = header(self.headers, "x-text-available")
        if value is not None:
            return value.strip().lower() != "false"
        return self.document.text_available

    @property
    def corpus_version(self) -> int | None:
        """Monotonic per-CELEX version (present when served from corpus)."""
        return _header_int(header(self.headers, "x-corpus-version"))

    @property
    def corpus_fetched_at(self) -> str | None:
        return header(self.headers, "x-corpus-fetchedat")

    @property
    def language_served(self) -> str | None:
        """The language rendition actually served (``X-Language`` header)."""
        return header(self.headers, "x-language")

    @property
    def language_fallback(self) -> str | None:
        """The language served instead when the requested one had no text
        (``X-Language-Fallback``): ``en`` for the automatic fallback, or the
        first ``accept_languages`` entry that had text."""
        return header(self.headers, "x-language-fallback")

    @property
    def cache(self) -> str | None:
        return header(self.headers, "x-cache")


class DocumentByUrlResponse(DocumentResponse):
    """``POST /documents/url`` — document plus URL-extraction echo."""

    @property
    def source_url(self) -> str | None:
        return _s(self._map, "sourceUrl")

    @property
    def extracted_celex(self) -> str | None:
        return _s(self._map, "extractedCelex")


class DocumentMetadataResponse(APIResponse):
    """``POST /documents/metadata`` — metadata only (no article content)."""

    @property
    def metadata(self) -> Document:
        return Document.from_dict(_dict(self._map, "metadata"))

    @property
    def source(self) -> str | None:
        """``corpus`` (1 credit), ``cellar`` (4) or ``live`` (5) — ``X-Source``."""
        return header(self.headers, "x-source")

    @property
    def corpus_version(self) -> int | None:
        return _header_int(header(self.headers, "x-corpus-version"))

    @property
    def cache(self) -> str | None:
        return header(self.headers, "x-cache")


class DocumentBatchResponse(APIResponse):
    """``POST /documentContent/batch`` — documents plus per-CELEX errors.

    Tier clamping is surfaced, never silent: ``trimmed`` / ``trimmed_to`` /
    ``trimmed_reason`` mirror the body flags and ``.warning`` carries the
    ``X-Warning`` header.
    """

    @property
    def documents(self) -> list[Document]:
        return _parse_items(self._map, "documents", Document)

    @property
    def errors(self) -> list[BatchDocumentError]:
        return _parse_items(self._map, "errors", BatchDocumentError)

    @property
    def requested(self) -> int | None:
        return _i(self._map, "requested")

    @property
    def processed(self) -> int | None:
        return _i(self._map, "processed")

    @property
    def successful(self) -> int | None:
        return _i(self._map, "successful")

    @property
    def failed(self) -> int | None:
        return _i(self._map, "failed")

    @property
    def trimmed(self) -> bool:
        """True when the batch was clamped to the tier ceiling."""
        return _b(self._map, "trimmed") or False

    @property
    def trimmed_to(self) -> int | None:
        return _i(self._map, "trimmedTo")

    @property
    def trimmed_reason(self) -> str | None:
        return _s(self._map, "trimmedReason")

    @property
    def text_unavailable(self) -> int | None:
        """Returned documents that carry metadata but no text (they stay in
        ``documents``; billed 1 credit each). Body field, falling back to the
        ``X-Batch-Text-Unavailable`` header."""
        value = _i(self._map, "textUnavailable")
        if value is not None:
            return value
        return _header_int(header(self.headers, "x-batch-text-unavailable"))

    @property
    def corpus_hits(self) -> int | None:
        """Items served from the LexAPI corpus, 1 credit each (``X-Batch-Corpus-Hits``)."""
        return _header_int(header(self.headers, "x-batch-corpus-hits"))

    @property
    def cellar_served(self) -> int | None:
        """Items the Cellar API answered, 4 credits each (``X-Batch-Cellar-Served``)."""
        return _header_int(header(self.headers, "x-batch-cellar-served"))

    @property
    def oj_recovered(self) -> int | None:
        """Items recovered from an Official Journal issue, 4 credits each
        (``X-Batch-OJ-Recovered``)."""
        return _header_int(header(self.headers, "x-batch-oj-recovered"))

    @property
    def live_fetches(self) -> int | None:
        """Items fetched live by browser (``X-Batch-Live-Fetches``): 5 credits
        each, or 1 when the item turned out to have no text."""
        return _header_int(header(self.headers, "x-batch-live-fetches"))

    @property
    def language_served(self) -> str | None:
        return header(self.headers, "x-language")


class RecentDocumentsResponse(APIResponse):
    """``GET /documents/recent`` — recently-published documents."""

    @property
    def results(self) -> list[SearchHit]:
        return _parse_items(self._map, "results", SearchHit)

    @property
    def total_results(self) -> int | None:
        return _i(self._map, "totalResults")

    @property
    def result_count(self) -> int | None:
        return _i(self._map, "resultCount")

    @property
    def parameters(self) -> dict[str, Any]:
        """Echo of the resolved query parameters (computed dateFrom/dateTo)."""
        return _dict(self._map, "parameters")

    @property
    def language_filter(self) -> dict[str, Any] | None:
        """Present only when the language post-filter dropped rows."""
        value = self._map.get("languageFilter")
        return value if isinstance(value, dict) else None

    @property
    def source(self) -> str | None:
        """``cellar`` (unfiltered request answered from Cellar — settles at 3
        credits whatever the ``limit``, though it is admitted against
        ``ceil(limit / 10)`` pages × 3 up front) or ``live`` (EUR-Lex, priced
        per page) — the ``X-Source`` header."""
        return header(self.headers, "x-source")

    @property
    def fetched_at(self) -> str | None:
        return _s(self._map, "fetchedAt")


# ── Citations ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RelationSync:
    """Freshness of a target's inbound structural edges (amendment /
    corrigendum / repeal), read from Cellar at most once every 7 days.

    ``status``: ``fresh`` · ``refreshed`` · ``pending`` (refresh still running
    in the background; this response shows the stored edges) · ``cooldown`` ·
    ``error`` · ``skipped`` (see ``reason``: ``not-applicable``, ``disabled``,
    ``busy``, ``budget``). ``synced_at`` is the last completed refresh when
    known.
    """

    source: str | None
    status: str | None
    reason: str | None
    synced_at: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationSync:
        return cls(
            source=_s(data, "source"),
            status=_s(data, "status"),
            reason=_s(data, "reason"),
            synced_at=_s(data, "syncedAt"),
            raw=data,
        )

    @classmethod
    def parse(cls, data: dict[str, Any]) -> RelationSync | None:
        value = data.get("relationSync")
        return cls.from_dict(value) if isinstance(value, dict) else None


@dataclass(frozen=True)
class CitationEdge:
    """A single citation edge with classification and context."""

    citation_type: str | None
    context_snippet: str | None
    target_celex: str | None
    extracted_at: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CitationEdge:
        return cls(
            citation_type=_s(data, "citationType"),
            context_snippet=_s(data, "contextSnippet"),
            target_celex=_s(data, "targetCelex"),
            extracted_at=_s(data, "extractedAt"),
            raw=data,
        )


@dataclass(frozen=True)
class CitationDocumentGroup:
    """All edges between one document and the queried document."""

    celex_number: str | None
    title: str | None
    citation_count: int | None
    citations: list[CitationEdge]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CitationDocumentGroup:
        return cls(
            celex_number=_s(data, "celexNumber"),
            title=_s(data, "title"),
            citation_count=_i(data, "citationCount"),
            citations=_parse_items(data, "citations", CitationEdge),
            raw=data,
        )


@dataclass(frozen=True)
class CitationNeighbour:
    """A neighbour in the citation graph, deduplicated across edge types."""

    celex_number: str | None
    title: str | None
    count: int | None
    types: list[str]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CitationNeighbour:
        return cls(
            celex_number=_s(data, "celexNumber"),
            title=_s(data, "title"),
            count=_i(data, "count"),
            types=_str_list(data, "types"),
            raw=data,
        )


@dataclass(frozen=True)
class CitationPathNode:
    """A node on a citation path (also the ``document`` of an extraction).

    ``title_source`` says where the title came from (``corpus``, ``edge``,
    ``unknown``) on path responses.
    """

    celex_number: str | None
    title: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    title_source: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CitationPathNode:
        return cls(
            celex_number=_s(data, "celexNumber"),
            title=_s(data, "title"),
            raw=data,
            title_source=_s(data, "titleSource"),
        )


@dataclass(frozen=True)
class RelatedDocument:
    """Bibliographic-coupling neighbour."""

    celex_number: str | None
    title: str | None
    shared_count: int | None
    shared_targets: list[str]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    title_source: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelatedDocument:
        return cls(
            celex_number=_s(data, "celexNumber"),
            title=_s(data, "title"),
            shared_count=_i(data, "sharedCount"),
            shared_targets=_str_list(data, "sharedTargets"),
            raw=data,
            title_source=_s(data, "titleSource"),
        )


@dataclass(frozen=True)
class CitationStatsEntry:
    """One row of the mostCited / mostCiting top-10 lists."""

    celex_number: str | None
    title: str | None
    cited_by_count: int | None
    cites_count: int | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CitationStatsEntry:
        return cls(
            celex_number=_s(data, "celexNumber"),
            title=_s(data, "title"),
            cited_by_count=_i(data, "citedByCount"),
            cites_count=_i(data, "citesCount"),
            raw=data,
        )


class CitationExtractResponse(APIResponse):
    """``POST /citations/extract`` — crawl a document and persist its edges."""

    @property
    def citation_count(self) -> int | None:
        return _i(self._map, "citationCount")

    @property
    def citations(self) -> list[CitationEdge]:
        """Edges just extracted. Empty when ``already_extracted`` is True."""
        return _parse_items(self._map, "citations", CitationEdge)

    @property
    def already_extracted(self) -> bool:
        """True on the idempotent short-circuit (settles at 1 credit, not 5)."""
        return _b(self._map, "alreadyExtracted") or False

    @property
    def document(self) -> CitationPathNode | None:
        """The crawled source document (``celex_number``, ``title``); present
        on a fresh extraction, ``None`` when ``already_extracted``."""
        value = self._map.get("document")
        return CitationPathNode.from_dict(value) if isinstance(value, dict) else None

    @property
    def message(self) -> str | None:
        return _s(self._map, "message")


class CitedByResponse(APIResponse):
    """``GET /citations/cited-by/{celex}`` — inbound edges, grouped by source."""

    @property
    def target_document(self) -> str | None:
        return _s(self._map, "targetDocument")

    @property
    def cited_by(self) -> list[CitationDocumentGroup]:
        return _parse_items(self._map, "citedBy", CitationDocumentGroup)

    @property
    def total_citations(self) -> int | None:
        """Total inbound edges matching the filter (across all pages)."""
        return _i(self._map, "totalCitations")

    @property
    def unique_documents(self) -> int | None:
        """Citing documents **in this page** (not the global distinct total)."""
        return _i(self._map, "uniqueDocuments")

    @property
    def limit(self) -> int | None:
        return _i(self._map, "limit")

    @property
    def offset(self) -> int | None:
        return _i(self._map, "offset")

    @property
    def relation_sync(self) -> RelationSync | None:
        """Freshness of the Cellar-sourced amendment/corrigendum/repeal edges."""
        return RelationSync.parse(self._map)

    @property
    def edge_count(self) -> int:
        """Edge rows in this page (sum of the groups' ``citation_count``) —
        the amount to advance ``offset`` by, since paging is in edges."""
        return sum(group.citation_count or len(group.citations) for group in self.cited_by)


class CitesResponse(APIResponse):
    """``GET /citations/cites/{celex}`` — outbound edges, grouped by target."""

    @property
    def source_document(self) -> str | None:
        return _s(self._map, "sourceDocument")

    @property
    def cites(self) -> list[CitationDocumentGroup]:
        return _parse_items(self._map, "cites", CitationDocumentGroup)

    @property
    def total_citations(self) -> int | None:
        return _i(self._map, "totalCitations")

    @property
    def unique_documents(self) -> int | None:
        """Cited documents **in this page** (not the global distinct total)."""
        return _i(self._map, "uniqueDocuments")

    @property
    def limit(self) -> int | None:
        return _i(self._map, "limit")

    @property
    def offset(self) -> int | None:
        return _i(self._map, "offset")

    @property
    def edge_count(self) -> int:
        """Edges in this page — the amount to advance ``offset`` by."""
        return sum(group.citation_count or len(group.citations) for group in self.cites)


class CitationNetworkResponse(APIResponse):
    """``GET /citations/network/{celex}`` — both directions in one call.

    Counts are page-scoped; unbounded totals live in ``cites_total`` /
    ``cited_by_total`` (``None`` on partial responses). When the server-side
    wall-clock budget expires the endpoint still returns 200 with
    ``partial=True`` and empty arrays — check ``.partial``.
    """

    @property
    def document(self) -> str | None:
        return _s(self._map, "document")

    @property
    def cites(self) -> list[CitationNeighbour]:
        return _parse_items(_dict(self._map, "network"), "cites", CitationNeighbour)

    @property
    def cited_by(self) -> list[CitationNeighbour]:
        return _parse_items(_dict(self._map, "network"), "citedBy", CitationNeighbour)

    @property
    def cites_count(self) -> int | None:
        """Unique outbound neighbours in this page (deduplicated)."""
        return _i(_dict(self._map, "network"), "citesCount")

    @property
    def cited_by_count(self) -> int | None:
        return _i(_dict(self._map, "network"), "citedByCount")

    @property
    def cites_total(self) -> int | None:
        """Total outbound edges (unbounded COUNT); None when partial."""
        return _i(_dict(self._map, "paging"), "citesTotal")

    @property
    def cited_by_total(self) -> int | None:
        return _i(_dict(self._map, "paging"), "citedByTotal")

    @property
    def limit(self) -> int | None:
        return _i(_dict(self._map, "paging"), "limit")

    @property
    def offset(self) -> int | None:
        return _i(_dict(self._map, "paging"), "offset")

    @property
    def partial(self) -> bool:
        """True when the server-side time budget expired (arrays are empty)."""
        return _b(self._map, "partial") or False

    @property
    def partial_reason(self) -> str | None:
        return _s(self._map, "partialReason")

    @property
    def message(self) -> str | None:
        return _s(self._map, "message")

    @property
    def timeout_ms(self) -> int | None:
        return _i(self._map, "timeoutMs")

    @property
    def relation_sync(self) -> RelationSync | None:
        """Freshness of the Cellar-sourced amendment/corrigendum/repeal edges."""
        return RelationSync.parse(self._map)


class CitationStatsResponse(APIResponse):
    """``GET /citations/stats`` — global citation-graph counters."""

    @property
    def total_citations(self) -> int | None:
        return _i(_dict(self._map, "stats"), "totalCitations")

    @property
    def unique_source_documents(self) -> int | None:
        return _i(_dict(self._map, "stats"), "uniqueSourceDocuments")

    @property
    def unique_target_documents(self) -> int | None:
        return _i(_dict(self._map, "stats"), "uniqueTargetDocuments")

    @property
    def most_cited(self) -> list[CitationStatsEntry]:
        return _parse_items(_dict(self._map, "stats"), "mostCited", CitationStatsEntry)

    @property
    def most_citing(self) -> list[CitationStatsEntry]:
        return _parse_items(_dict(self._map, "stats"), "mostCiting", CitationStatsEntry)


class CitationPathResponse(APIResponse):
    """``GET /citations/path/{from}/{to}`` — shortest citation path (BFS).

    ``found=False`` is a graceful HTTP-200 response, not an error.
    """

    @property
    def found(self) -> bool:
        return _b(self._map, "found") or False

    @property
    def path(self) -> list[CitationPathNode]:
        return _parse_items(self._map, "path", CitationPathNode)

    @property
    def path_length(self) -> int | None:
        """Number of edges (0 for self-loops); None when not found."""
        return _i(self._map, "pathLength")

    @property
    def max_depth(self) -> int | None:
        return _i(self._map, "maxDepth")

    @property
    def from_celex(self) -> str | None:
        return _s(self._map, "from")

    @property
    def to_celex(self) -> str | None:
        return _s(self._map, "to")

    @property
    def message(self) -> str | None:
        return _s(self._map, "message")


class RelatedDocumentsResponse(APIResponse):
    """``GET /citations/related/{celex}`` — bibliographic-coupling neighbours."""

    @property
    def related(self) -> list[RelatedDocument]:
        return _parse_items(self._map, "related", RelatedDocument)

    @property
    def celex_number(self) -> str | None:
        return _s(self._map, "celexNumber")

    @property
    def method(self) -> str | None:
        return _s(self._map, "method")

    @property
    def seed_citation_count(self) -> int | None:
        return _i(self._map, "seedCitationCount")

    @property
    def count(self) -> int | None:
        return _i(self._map, "count")

    @property
    def message(self) -> str | None:
        """Present when the seed has no outbound citations to couple on."""
        return _s(self._map, "message")


# ── Webhooks ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WebhookDelivery:
    """A single webhook delivery attempt."""

    id: str | None
    status: str | None
    payload: dict[str, Any]
    response_status: int | None
    response_body: str | None
    error_message: str | None
    attempt_count: int | None
    next_retry_at: str | None
    delivered_at: str | None
    created_at: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    webhook_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebhookDelivery:
        return cls(
            id=_s(data, "id"),
            status=_s(data, "status"),
            payload=_dict(data, "payload"),
            response_status=_i(data, "responseStatus"),
            response_body=_s(data, "responseBody"),
            error_message=_s(data, "errorMessage"),
            attempt_count=_i(data, "attemptCount"),
            next_retry_at=_s(data, "nextRetryAt"),
            delivered_at=_s(data, "deliveredAt"),
            created_at=_s(data, "createdAt"),
            raw=data,
            webhook_id=_s(data, "webhookId"),
        )


@dataclass(frozen=True)
class WebhookRetryPolicy:
    """Delivery retry schedule (``GET /webhooks/{id}`` only): 7 attempts
    over ~24 h, delays 1 min, 5 min, 30 min, 2 h, 6 h, 15 h."""

    max_attempts: int | None
    window_hours: int | None
    delays_minutes: list[int]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebhookRetryPolicy:
        return cls(
            max_attempts=_i(data, "maxAttempts"),
            window_hours=_i(data, "windowHours"),
            delays_minutes=[
                int(v)
                for v in _list(data, "delaysMinutes")
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ],
            raw=data,
        )


@dataclass(frozen=True)
class Webhook:
    """A webhook subscription.

    A webhook fires ``documents.new`` for new documents matching
    ``search_criteria`` and/or ``watchlist.changes`` when an act in
    ``watch_celex`` is amended, corrected, repealed or consolidated.

    ``secret`` is populated **only** on the create response — the API never
    returns it again; store it then. ``recent_deliveries`` and
    ``retry_policy`` are populated only by ``GET /webhooks/{id}`` (up to 20
    deliveries, newest first). ``criteria_errors`` lists what is wrong with
    stored criteria saved before a validation rule existed (such criteria are
    skipped until fixed with an update). ``delivery_count`` is on list items
    only. ``max_retries`` is deprecated: the API no longer sends
    ``maxRetries``, so it is always ``None`` — reading it emits a
    :class:`DeprecationWarning`; use ``retry_policy.max_attempts``.
    """

    def __getattribute__(self, name: str) -> Any:
        # Warn on user access only: dataclasses.asdict()/replace()/astuple()
        # read every field from inside the dataclasses module.
        if name == "max_retries" and sys._getframe(1).f_globals.get("__name__") != "dataclasses":
            warnings.warn(
                "Webhook.max_retries is deprecated and always None (the API no longer "
                "sends maxRetries); use Webhook.retry_policy.max_attempts",
                DeprecationWarning,
                stacklevel=2,
            )
        return object.__getattribute__(self, name)

    id: str | None
    name: str | None
    url: str | None
    status: str | None
    search_criteria: dict[str, Any]
    secret: str | None
    last_checked: str | None
    last_triggered: str | None
    consecutive_failures: int | None
    delivery_count: int | None
    max_retries: int | None = field(repr=False, compare=False)
    created_at: str | None
    updated_at: str | None
    recent_deliveries: list[WebhookDelivery]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    watch_celex: list[str] = field(default_factory=list)
    criteria_errors: list[str] = field(default_factory=list)
    retry_policy: WebhookRetryPolicy | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Webhook:
        policy = data.get("retryPolicy")
        criteria = data.get("searchCriteria")
        return cls(
            id=_s(data, "id"),
            name=_s(data, "name"),
            url=_s(data, "url"),
            status=_s(data, "status"),
            search_criteria=criteria if isinstance(criteria, dict) else {},
            secret=_s(data, "secret"),
            last_checked=_s(data, "lastChecked"),
            last_triggered=_s(data, "lastTriggered"),
            consecutive_failures=_i(data, "consecutiveFailures"),
            delivery_count=_i(data, "deliveryCount"),
            max_retries=_i(data, "maxRetries"),
            created_at=_s(data, "createdAt"),
            updated_at=_s(data, "updatedAt"),
            recent_deliveries=_parse_items(data, "recentDeliveries", WebhookDelivery),
            raw=data,
            watch_celex=_str_list(data, "watchCelex"),
            criteria_errors=_str_list(data, "criteriaErrors"),
            retry_policy=WebhookRetryPolicy.from_dict(policy) if isinstance(policy, dict) else None,
        )

    @property
    def has_search_criteria(self) -> bool:
        """False when the webhook has no criteria (``searchCriteria: null``) —
        ``search_criteria`` is then ``{}``, which would otherwise read as
        "match everything"."""
        return isinstance(self.raw.get("searchCriteria"), dict)


class WebhookListResponse(APIResponse):
    """``GET /webhooks`` — the caller's subscriptions (no secrets)."""

    @property
    def webhooks(self) -> list[Webhook]:
        return _parse_items(self._map, "webhooks", Webhook)

    @property
    def count(self) -> int | None:
        return _i(self._map, "count")


class WebhookResponse(APIResponse):
    """``GET /webhooks/{id}`` / ``POST /webhooks`` / ``PUT /webhooks/{id}``."""

    @property
    def webhook(self) -> Webhook:
        return Webhook.from_dict(_dict(self._map, "webhook"))

    @property
    def message(self) -> str | None:
        return _s(self._map, "message")

    @property
    def requeued_deliveries(self) -> int | None:
        """``PUT`` with ``status="ACTIVE"`` only: exhausted deliveries from the
        last 7 days queued again on a fresh 7-attempt schedule."""
        return _i(self._map, "requeuedDeliveries")


class WebhookTestResponse(APIResponse):
    """``POST /webhooks/{id}/test`` — synchronous test delivery result.

    ``ok`` mirrors the body's ``success`` flag: ``False`` (still HTTP 200)
    when the upstream returned non-2xx or the delivery failed — inspect
    ``delivery.error_message``.
    """

    @property
    def ok(self) -> bool:
        return _b(self._map, "success") or False

    @property
    def delivery(self) -> WebhookDelivery:
        return WebhookDelivery.from_dict(_dict(self._map, "delivery"))

    @property
    def message(self) -> str | None:
        return _s(self._map, "message")


class WebhookDeliveriesResponse(APIResponse):
    """``GET /webhooks/{id}/deliveries`` — delivery history, newest first."""

    @property
    def deliveries(self) -> list[WebhookDelivery]:
        return _parse_items(self._map, "deliveries", WebhookDelivery)

    @property
    def total(self) -> int | None:
        """Total deliveries for this webhook (across all pages)."""
        return _i(self._map, "total")

    @property
    def limit(self) -> int | None:
        return _i(self._map, "limit")

    @property
    def offset(self) -> int | None:
        return _i(self._map, "offset")


# ── Semantic search ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SemanticCaseLawResult:
    """Single case-law match from ``/search/semantic``.

    Persistent document identity is ``(celex, document_type)`` — read off
    ``metadata.celex_id`` / ``metadata.document_type``. ``case_id`` is
    snapshot-scoped and regenerated on index rebuilds; don't store it.

    By default a hit carries a ~600-character ``snippet``; ``text`` (the full
    document text) is present only when the request set
    ``include_text=True``. Fields the upstream extractor could not populate
    are omitted by the API, so they come back ``None`` here; the structured
    analytics block (``outcome``, ``claimed_amount``, ...) stays on ``raw``.
    """

    score: float | None
    text: str | None
    case_id: str | None
    case_name: str | None
    case_number: str | None
    ecli: str | None
    court: str | None
    celex: str | None
    document_type: str | None
    metadata: dict[str, Any]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    snippet: str | None = None
    summary: str | None = None
    parties: list[str] | None = None
    decision_date: str | None = None
    jurisdiction: str | None = None
    language: str | None = None
    paragraph_number: int | None = None

    @property
    def parties_text(self) -> str | None:
        """``parties`` joined with ``"; "`` (``None`` when absent).

        The upstream list carries one entry per party without their roles, so
        no "applicant v defendant" order can be inferred — CJEU cases often
        have several parties on a side (``X and Others v Commission``). For
        the citation-style title use ``case_name``."""
        return "; ".join(self.parties) if self.parties else None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticCaseLawResult:
        metadata = _dict(data, "metadata")
        score = data.get("score")
        return cls(
            score=float(score) if isinstance(score, (int, float)) else None,
            text=_s(data, "text"),
            case_id=_s(data, "case_id"),
            case_name=_s(data, "case_name"),
            case_number=_s(data, "case_number"),
            ecli=_s(data, "ecli"),
            court=_s(data, "court"),
            celex=_s(metadata, "celex_id"),
            document_type=_s(metadata, "document_type"),
            metadata=metadata,
            raw=data,
            snippet=_s(data, "snippet"),
            summary=_s(data, "summary"),
            parties=_parties(data.get("parties")),
            decision_date=_s(data, "decision_date"),
            jurisdiction=_s(data, "jurisdiction"),
            language=_s(data, "language"),
            paragraph_number=_i(metadata, "paragraph_number"),
        )


@dataclass(frozen=True)
class SemanticBaseAct:
    """The authentic act's own match, on a merged consolidated hit."""

    law_id: str | None
    article_ref: str | None
    score: float | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticBaseAct:
        return cls(
            law_id=_s(data, "law_id"),
            article_ref=_s(data, "article_ref"),
            score=_f(data, "score"),
            raw=data,
        )


@dataclass(frozen=True)
class AsOfVersion:
    """The version an ``asOf`` hit's text comes from.

    ``kind`` is ``consolidated`` or ``as_published``; ``date`` is the
    consolidation date (``None`` for the act as published).
    """

    celex: str | None
    date: str | None
    kind: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AsOfVersion:
        return cls(
            celex=_s(data, "celex"),
            date=_s(data, "date"),
            kind=_s(data, "kind"),
            raw=data,
        )


@dataclass(frozen=True)
class MatchedVersion:
    """What the index matched, when ``asOf`` swapped the hit's text for the
    version in force (the hit's ``score`` is this version's similarity)."""

    id: str | None
    law_id: str | None
    consolidated_as_of: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchedVersion:
        return cls(
            id=_s(data, "id"),
            law_id=_s(data, "law_id"),
            consolidated_as_of=_s(data, "consolidatedAsOf"),
            raw=data,
        )


@dataclass(frozen=True)
class AsOfSummary:
    """``asOfSummary`` on an ``asOf`` legislation search."""

    #: Live fetches attempted, including failed / timed-out ones (those cost 0).
    live_fetches: int | None
    #: Hits returned with ``as_of_resolved=False``.
    unresolved: int | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AsOfSummary:
        return cls(
            live_fetches=_i(data, "liveFetches"),
            unresolved=_i(data, "unresolved"),
            raw=data,
        )


@dataclass(frozen=True)
class SemanticLegislationResult:
    """Single article-level match from ``/legislation/semantic``.

    ``article_ref`` (``art_19``) is usable as ``article_id`` on
    :meth:`~lexapi.LexAPI.get_document`. By default a hit carries a
    ``snippet``; ``text`` is present only with ``include_text=True``.

    **Consolidated texts.** When the act as published and its consolidated
    version match the same article, the response carries one hit: the
    consolidated article, with ``consolidated=True``, ``consolidated_as_of``,
    ``base_celex`` and ``consolidated_note``, and the authentic act's own
    match under ``base_act`` (``score`` is the higher of the two).
    Consolidated texts are not authentic — cite ``base_celex``.

    **asOf.** On an ``as_of`` search every hit carries ``as_of`` and
    ``as_of_resolved``. A resolved hit names its source in ``as_of_version``
    and, when its text was swapped, what the index matched in
    ``matched_version`` (``id``, ``law_id``, ``law_title``, ``section`` and the
    text then describe the version in force; ``chapter`` is ``None``). An
    unresolved hit keeps its latest text and says why in ``as_of_reason``
    (one of :data:`AS_OF_REASONS`).
    """

    score: float | None
    text: str | None
    article_ref: str | None
    law_id: str | None
    law_title: str | None
    metadata: dict[str, Any]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    id: str | None = None
    snippet: str | None = None
    language: str | None = None
    chapter: str | None = None
    section: str | None = None
    consolidated: bool = False
    consolidated_as_of: str | None = None
    base_celex: str | None = None
    consolidated_note: str | None = None
    base_act: SemanticBaseAct | None = None
    as_of: str | None = None
    as_of_resolved: bool | None = None
    as_of_reason: str | None = None
    as_of_version: AsOfVersion | None = None
    matched_version: MatchedVersion | None = None

    @property
    def authentic_celex(self) -> str | None:
        """The CELEX to cite as the authentic source: ``base_celex`` on a
        consolidated text, ``law_id`` otherwise."""
        return self.base_celex if self.consolidated and self.base_celex else self.law_id

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticLegislationResult:
        base_act = _opt_dict(data, "baseAct")
        as_of_version = _opt_dict(data, "asOfVersion")
        matched = _opt_dict(data, "matchedVersion")
        return cls(
            score=_f(data, "score"),
            text=_s(data, "text"),
            article_ref=_s(data, "article_ref"),
            law_id=_s(data, "law_id"),
            law_title=_s(data, "law_title"),
            metadata=_dict(data, "metadata"),
            raw=data,
            id=_s(data, "id"),
            snippet=_s(data, "snippet"),
            language=_s(data, "language"),
            chapter=_s(data, "chapter"),
            section=_s(data, "section"),
            consolidated=_b(data, "consolidated") or False,
            consolidated_as_of=_s(data, "consolidatedAsOf"),
            base_celex=_s(data, "baseCelex"),
            consolidated_note=_s(data, "consolidatedNote"),
            base_act=SemanticBaseAct.from_dict(base_act) if base_act is not None else None,
            as_of=_s(data, "asOf"),
            as_of_resolved=_b(data, "asOfResolved"),
            as_of_reason=_s(data, "asOfReason"),
            as_of_version=(
                AsOfVersion.from_dict(as_of_version) if as_of_version is not None else None
            ),
            matched_version=MatchedVersion.from_dict(matched) if matched is not None else None,
        )


class _SemanticResponseBase(APIResponse):
    @property
    def search_type(self) -> str | None:
        return _s(self._map, "searchType")

    @property
    def query(self) -> str | None:
        return _s(self._map, "query")

    @property
    def result_count(self) -> int | None:
        return _i(self._map, "resultCount")

    @property
    def hint(self) -> str | None:
        """Low-confidence signal: present only on best-effort responses.

        When set, the adaptive relevance floor surfaced hits below your
        ``min_score`` (or fell back to the noise floor) — the string says
        what to do (rephrase, lower ``min_score``). Absent on confident
        responses; its presence is itself the signal.
        """
        return _s(self._map, "hint")

    @property
    def took_ms(self) -> int | None:
        return _i(self._map, "tookMs")

    @property
    def semantic_usage(self) -> dict[str, Any] | None:
        """Legacy daily-call plans only; credit plans meter from the pool."""
        value = self._map.get("semanticUsage")
        return value if isinstance(value, dict) else None

    @property
    def upstream_metadata(self) -> dict[str, Any]:
        """The response-level ``metadata`` — an opaque pass-through from the
        embedding service whose shape is not guaranteed (use ``took_ms`` for
        timing)."""
        return _dict(self._map, "metadata")

    @property
    def max_results(self) -> int | None:
        """The effective result cap (``subscription.maxResults``)."""
        return _i(_dict(self._map, "subscription"), "maxResults")


class SemanticSearchResponse(_SemanticResponseBase):
    """``POST /search/semantic`` — ranked case-law matches.

    HyDE visibility: ``hyde`` reports whether HyDE rewriting actually ran
    (``False`` after a fallback — the 10-credit premium was auto-refunded
    and ``credits.operation_weight`` reflects the base rate); the key is
    absent (``None``) when the request didn't ask for HyDE.
    ``hypothetical_document`` carries the LLM-drafted passage when HyDE ran.
    """

    @property
    def results(self) -> list[SemanticCaseLawResult]:
        return _parse_items(self._map, "results", SemanticCaseLawResult)

    @property
    def hyde(self) -> bool | None:
        """Whether HyDE ran: True/False when requested, None when not."""
        return _b(self._map, "hyde")

    @property
    def hypothetical_document(self) -> str | None:
        """The LLM-drafted hypothetical passage (present only when HyDE ran)."""
        return _s(self._map, "hypotheticalDocument")


class SemanticLegislationResponse(_SemanticResponseBase):
    """``POST /legislation/semantic`` — ranked article-level matches.

    On an ``as_of`` search, ``as_of`` echoes the date and ``as_of_summary``
    counts the live fetches attempted and the unresolved hits.
    """

    @property
    def results(self) -> list[SemanticLegislationResult]:
        return _parse_items(self._map, "results", SemanticLegislationResult)

    @property
    def as_of(self) -> str | None:
        """Echo of the request's ``asOf`` (``as_of`` searches only)."""
        return _s(self._map, "asOf")

    @property
    def as_of_summary(self) -> AsOfSummary | None:
        value = _opt_dict(self._map, "asOfSummary")
        return AsOfSummary.from_dict(value) if value is not None else None


class ResolveResponse(APIResponse):
    """``POST /resolve`` — canonical CELEX for any legal identifier."""

    @property
    def celex(self) -> str | None:
        return _s(self._map, "celex")

    @property
    def identifier(self) -> str | None:
        return _s(self._map, "identifier")

    @property
    def identifier_type(self) -> str | None:
        """``celex`` / ``url`` / ``eli`` / ``ecli``."""
        return _s(self._map, "identifierType")

    @property
    def resolved_via(self) -> str | None:
        return _s(self._map, "resolvedVia")

    @property
    def urls(self) -> dict[str, Any]:
        return _dict(self._map, "urls")


@dataclass(frozen=True)
class VersionEntry:
    """One entry in a document's point-in-time version history.

    Versions are LexAPI *observation snapshots* (content-hash changes
    between fetches), not legal in-force reconstructions. ``fetched_at``
    is when THIS version was fetched — version N is "current" from there
    until version N+1's ``fetched_at``.
    """

    version: int | None
    is_current: bool | None
    content_hash: str | None
    fetched_at: str | None
    title: str | None
    document_type: str | None
    document_type_code: str | None
    parsed_content: dict[str, Any] | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VersionEntry:
        parsed = data.get("parsedContent")
        return cls(
            version=_i(data, "version"),
            is_current=_b(data, "isCurrent"),
            content_hash=_s(data, "contentHash"),
            fetched_at=_s(data, "fetchedAt"),
            title=_s(data, "title"),
            document_type=_s(data, "documentType"),
            document_type_code=_s(data, "documentTypeCode"),
            parsed_content=parsed if isinstance(parsed, dict) else None,
            raw=data,
        )


class DocumentVersionsResponse(APIResponse):
    """``GET /documents/{celex}/versions`` — history list, newest first."""

    @property
    def celex(self) -> str | None:
        return _s(self._map, "celex")

    @property
    def language(self) -> str | None:
        return _s(self._map, "language")

    @property
    def current_version(self) -> int | None:
        return _i(self._map, "currentVersion")

    @property
    def tracked_since(self) -> str | None:
        """When the document first entered the corpus — history starts here."""
        return _s(self._map, "trackedSince")

    @property
    def semantics(self) -> str | None:
        """The server's restatement of the observation-snapshot semantics."""
        return _s(self._map, "semantics")

    @property
    def versions(self) -> list[VersionEntry]:
        return _parse_items(self._map, "versions", VersionEntry)


class DocumentVersionResponse(APIResponse):
    """``GET /documents/{celex}/versions/{version}`` and ``/at/{date}``."""

    @property
    def celex(self) -> str | None:
        return _s(self._map, "celex")

    @property
    def language(self) -> str | None:
        return _s(self._map, "language")

    @property
    def as_of(self) -> str | None:
        """Present on ``/at/{date}`` responses only — the requested date."""
        return _s(self._map, "asOf")

    @property
    def semantics(self) -> str | None:
        return _s(self._map, "semantics")

    @property
    def document(self) -> VersionEntry:
        return VersionEntry.from_dict(_dict(self._map, "document"))

    @property
    def corpus_version(self) -> int | None:
        return _header_int(header(self.headers, "x-corpus-version"))


class InfoResponse(APIResponse):
    """``GET /info`` — service metadata, the caller's plan and usage, and the
    canonical filter enumerations (free: 0 credits)."""

    @property
    def service(self) -> str | None:
        return _s(self._map, "service")

    @property
    def version(self) -> str | None:
        """The API version (e.g. ``2.0.0``)."""
        return _s(self._map, "version")

    @property
    def description(self) -> str | None:
        return _s(self._map, "description")

    @property
    def endpoints(self) -> dict[str, Any]:
        """Self-describing capability map (per-endpoint method, path, params)."""
        return _dict(self._map, "endpoints")

    @property
    def search_filters(self) -> dict[str, list[str]]:
        """Allowed values per filter (``textScope``, ``domain``, ``subdomain``,
        ``documentType``, ``author``, ``procedure``)."""
        filters = _dict(self._map, "searchFilters")
        return {
            key: [item for item in value if isinstance(item, str)]
            for key, value in filters.items()
            if isinstance(value, list)
        }

    @property
    def supported_languages(self) -> list[str]:
        return _str_list(self._map, "supportedLanguages")


# ── National law (launch 2026-11-03) ────────────────────────────────────
#
# Validated against lex-api's server code directly (server/routes/api.js,
# server/routes/semantic.js, server/middleware/jurisdiction.js,
# server/controllers/nationalController.js,
# server/controllers/nationalSemanticController.js,
# server/utils/jurisdictions.js) and the diff of lex-api PR #306 (the
# monthly allowance for FREE/STARTER/PROFESSIONAL). The public
# ``GET /national/coverage`` endpoint and PR #306 were not yet deployed at
# the time this was written — re-validate against the live API once both
# ship.


@dataclass(frozen=True)
class NationalCoverageAvailability:
    """``legislation`` block of one jurisdiction's coverage entry."""

    available: bool | None
    description: str | None
    documents: int | None
    from_: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalCoverageAvailability:
        return cls(
            available=_b(data, "available"),
            description=_s(data, "description"),
            documents=_i(data, "documents"),
            from_=_s(data, "from"),
            raw=data,
        )


@dataclass(frozen=True)
class NationalCaseLawCoverageAvailability(NationalCoverageAvailability):
    """``caseLaw`` block — adds the list of courts covered."""

    courts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalCaseLawCoverageAvailability:
        return cls(
            available=_b(data, "available"),
            description=_s(data, "description"),
            documents=_i(data, "documents"),
            from_=_s(data, "from"),
            raw=data,
            courts=_str_list(data, "courts"),
        )


@dataclass(frozen=True)
class NationalSemanticSearchAvailability:
    """``semanticSearch`` block: whether each semantic index is live."""

    legislation: bool | None
    case_law: bool | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalSemanticSearchAvailability:
        return cls(
            legislation=_b(data, "legislation"),
            case_law=_b(data, "caseLaw"),
            raw=data,
        )


@dataclass(frozen=True)
class NationalSource:
    """One entry in a jurisdiction's ``sources`` list."""

    name: str | None
    url: str | None
    licence: str | None
    attribution: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalSource:
        return cls(
            name=_s(data, "name"),
            url=_s(data, "url"),
            licence=_s(data, "licence"),
            attribution=_s(data, "attribution"),
            raw=data,
        )


@dataclass(frozen=True)
class NationalJurisdictionCoverage:
    """One jurisdiction's entry in ``GET /national/coverage``.

    Always show ``sources`` (name, licence, attribution) to the end user
    when presenting national-law content — it is the attribution notice
    the upstream data providers require.
    """

    code: str | None
    name: str | None
    live: bool | None
    launched_at: str | None
    legislation: NationalCoverageAvailability | None
    case_law: NationalCaseLawCoverageAvailability | None
    semantic_search: NationalSemanticSearchAvailability | None
    sources: list[NationalSource]
    last_sync_at: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalJurisdictionCoverage:
        legislation = _opt_dict(data, "legislation")
        case_law = _opt_dict(data, "caseLaw")
        semantic = _opt_dict(data, "semanticSearch")
        return cls(
            code=_s(data, "code"),
            name=_s(data, "name"),
            live=_b(data, "live"),
            launched_at=_s(data, "launchedAt"),
            legislation=(
                NationalCoverageAvailability.from_dict(legislation)
                if legislation is not None
                else None
            ),
            case_law=(
                NationalCaseLawCoverageAvailability.from_dict(case_law)
                if case_law is not None
                else None
            ),
            semantic_search=(
                NationalSemanticSearchAvailability.from_dict(semantic)
                if semantic is not None
                else None
            ),
            sources=_parse_items(data, "sources", NationalSource),
            last_sync_at=_s(data, "lastSyncAt"),
            raw=data,
        )


class NationalCoverageResponse(APIResponse):
    """``GET /national/coverage`` — public, free. Call this first: a
    jurisdiction not yet launched answers 404 on every other national
    endpoint, exactly like one that does not exist."""

    @property
    def updated_at(self) -> str | None:
        return _s(self._map, "updatedAt")

    @property
    def jurisdictions(self) -> list[NationalJurisdictionCoverage]:
        return _parse_items(self._map, "jurisdictions", NationalJurisdictionCoverage)


@dataclass(frozen=True)
class NationalTextBasis:
    """``textBasis`` — present only for lanes that label what their text is
    (e.g. PL: the act as announced vs. an official consolidated text)."""

    basis: str | None
    as_of: str | None
    newer_consolidation_exists: bool | None
    newest_pdf_only_date: str | None
    newer_pdf_only: list[str]
    amended_since: bool | None
    text_from: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalTextBasis:
        return cls(
            basis=_s(data, "basis"),
            as_of=_s(data, "asOf"),
            newer_consolidation_exists=_b(data, "newerConsolidationExists"),
            newest_pdf_only_date=_s(data, "newestPdfOnlyDate"),
            newer_pdf_only=_str_list(data, "newerPdfOnly"),
            amended_since=_b(data, "amendedSince"),
            text_from=_s(data, "textFrom"),
            raw=data,
        )


@dataclass(frozen=True)
class NationalDocument:
    """A document from a national-law jurisdiction (not EUR-Lex).

    National reads are always served from the LexAPI corpus — there is no
    live-fetch fallback, so ``content is None`` means EUR-Lex-style
    "no published text", not a cold cache. ``attribution`` is the
    jurisdiction's required attribution notice: always show it next to the
    content.
    """

    id: str | None
    jurisdiction: str | None
    language: str | None
    title: str | None
    document_type: str | None
    ecli: str | None
    eli: str | None
    date_of_document: str | None
    version: int | None
    fetched_at: str | None
    valid_to: str | None
    in_force: bool | None
    content: DocumentContent | None
    text_availability: TextAvailability | None
    attribution: str | None
    text_basis: NationalTextBasis | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalDocument:
        content = data.get("content")
        availability = data.get("textAvailability")
        text_basis = _opt_dict(data, "textBasis")
        return cls(
            id=_s(data, "id"),
            jurisdiction=_s(data, "jurisdiction"),
            language=_s(data, "language"),
            title=_s(data, "title"),
            document_type=_s(data, "documentType"),
            ecli=_s(data, "ecli"),
            eli=_s(data, "eli"),
            date_of_document=_s(data, "dateOfDocument"),
            version=_i(data, "version"),
            fetched_at=_s(data, "fetchedAt"),
            valid_to=_s(data, "validTo"),
            in_force=_b(data, "inForce"),
            content=DocumentContent.from_dict(content) if isinstance(content, dict) else None,
            text_availability=(
                TextAvailability.from_dict(availability) if isinstance(availability, dict) else None
            ),
            attribution=_s(data, "attribution"),
            text_basis=NationalTextBasis.from_dict(text_basis) if text_basis is not None else None,
            raw=data,
        )


class NationalDocumentResponse(APIResponse):
    """``GET /{jurisdiction}/documents/{id}`` — one national document.

    No ``success`` key (unlike the EU document endpoints) — the national
    controller answers ``{"document": {...}}`` directly. 5 credits upfront,
    settled to 1 (``CORPUS_HIT_WEIGHT``): every national read is a corpus
    read, never a live fetch.
    """

    @property
    def document(self) -> NationalDocument:
        return NationalDocument.from_dict(_dict(self._map, "document"))


class NationalDocumentListResponse(APIResponse):
    """``GET /{jurisdiction}/documents`` — a page of one jurisdiction's
    documents. 3 credits flat per call (not per page)."""

    @property
    def jurisdiction(self) -> str | None:
        return _s(self._map, "jurisdiction")

    @property
    def total(self) -> int | None:
        return _i(self._map, "total")

    @property
    def page(self) -> int | None:
        return _i(self._map, "page")

    @property
    def limit(self) -> int | None:
        return _i(self._map, "limit")

    @property
    def include_repealed(self) -> bool | None:
        return _b(self._map, "includeRepealed")

    @property
    def results(self) -> list[NationalDocument]:
        """Summaries only: no ``content`` / ``textAvailability`` / ``attribution``."""
        return _parse_items(self._map, "results", NationalDocument)


@dataclass(frozen=True)
class NationalSemanticCaseLawResult(SemanticCaseLawResult):
    """A :class:`SemanticCaseLawResult` plus the national extras the
    controller adds: ``document_id`` / ``jurisdiction`` (also mirrored into
    ``metadata.celex_id``), ``valid_to`` / ``in_force`` (only present when a
    confirmed repeal overrides the hit toward *not* in force) and
    ``attribution`` (always show it next to the content)."""

    document_id: str | None = None
    valid_to: str | None = None
    in_force: bool | None = None
    attribution: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalSemanticCaseLawResult:
        base = SemanticCaseLawResult.from_dict(data)
        return cls(
            **{f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()},
            document_id=_s(data, "documentId"),
            valid_to=_s(data, "validTo"),
            in_force=_b(data, "inForce"),
            attribution=_s(data, "attribution"),
        )


@dataclass(frozen=True)
class NationalSemanticLegislationResult(SemanticLegislationResult):
    """A :class:`SemanticLegislationResult` plus the national extras.

    ``law_id`` / ``document_id`` are the national id (e.g. ``AT-NOR-...``).
    Quirk straight from the server: unlike every camelCase field elsewhere
    in this SDK, ``in_force`` / ``valid_to`` here are the server's own
    snake_case wire names (``in_force`` / ``valid_to``, not ``inForce`` /
    ``validTo``) — do not "fix" this, it would stop matching the API.
    They are present only when a confirmed repeal overrides the upstream
    hit's ``in_force`` toward ``False``.
    """

    document_id: str | None = None
    in_force: bool | None = None
    valid_to: str | None = None
    attribution: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NationalSemanticLegislationResult:
        base = SemanticLegislationResult.from_dict(data)
        return cls(
            **{f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()},
            document_id=_s(data, "documentId"),
            # Server wire names are snake_case here (in_force / valid_to,
            # not inForce / validTo) — see the class docstring.
            in_force=_b(data, "in_force"),
            valid_to=_s(data, "valid_to"),
            attribution=_s(data, "attribution"),
        )


class _NationalSemanticResponseBase(_SemanticResponseBase):
    @property
    def jurisdiction(self) -> str | None:
        return _s(self._map, "jurisdiction")

    @property
    def attribution(self) -> str | None:
        """The jurisdiction's attribution notice — always show it to the user."""
        return _s(self._map, "attribution")


class NationalSemanticSearchResponse(_NationalSemanticResponseBase):
    """``POST /{jurisdiction}/search/semantic`` — national case-law search.

    5 credits flat. ``hyde`` is accepted for API compatibility but the
    national index never rewrites: a request with ``hyde=True`` gets
    ``hyde=False`` back, never a 15-credit charge. Unlike the EU endpoint,
    this response never carries a ``credits`` block in the body (same gap
    as :class:`SemanticSearchResponse` — ``semantic.js`` does not attach the
    credits envelope); read the ``X-National-*`` headers
    (``national_allowance_limit`` / ``_remaining`` / ``_resets_at``) for
    FREE/STARTER/PROFESSIONAL accounts instead.
    """

    @property
    def results(self) -> list[NationalSemanticCaseLawResult]:
        return _parse_items(self._map, "results", NationalSemanticCaseLawResult)

    @property
    def hyde(self) -> bool | None:
        return _b(self._map, "hyde")


class NationalSemanticLegislationResponse(_NationalSemanticResponseBase):
    """``POST /{jurisdiction}/legislation/semantic`` — national legislation
    search. 5 credits flat. No ``hyde``, no ``as_of`` (both EU-only)."""

    @property
    def results(self) -> list[NationalSemanticLegislationResult]:
        return _parse_items(self._map, "results", NationalSemanticLegislationResult)
