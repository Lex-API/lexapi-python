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

from dataclasses import dataclass, field
from typing import Any

from lexapi._response import APIResponse


def _s(data: dict, key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _i(data: dict, key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _b(data: dict, key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _dict(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _list(data: dict, key: str) -> list:
    value = data.get(key)
    return value if isinstance(value, list) else []


def _str_list(data: dict, key: str) -> list[str]:
    return [item for item in _list(data, key) if isinstance(item, str)]


def _parse_items(data: dict, key: str, cls: Any) -> list:
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
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Article:
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
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Recital:
        return cls(number=_i(data, "number"), text=_s(data, "text"), raw=data)


@dataclass(frozen=True)
class Section:
    """Chapter/section hierarchy node."""

    type: str | None
    number: str | None
    title: str | None
    article_range: list[str] | None
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Section:
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
    tables: list[dict]
    annexes: list[dict]
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> DocumentContent:
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
class Document:
    """A parsed document: normalized metadata plus (optionally) content.

    Also used for metadata-only responses, where ``content`` is ``None``.
    ISO date variants (``date_of_document_iso``, ...) are preferred over
    the legacy DD/MM/YYYY strings.
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
    urls: dict
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Document:
        content = data.get("content")
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
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> SearchHit:
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
        )


@dataclass(frozen=True)
class BatchDocumentError:
    """One failed CELEX from a batch fetch (does not abort the batch)."""

    celex_number: str | None
    error: str | None
    message: str | None
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> BatchDocumentError:
        return cls(
            celex_number=_s(data, "celexNumber"),
            error=_s(data, "error"),
            message=_s(data, "message"),
            raw=data,
        )


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
    def document_type_fan_out(self) -> int | None:
        """Size of the EUR-Lex fan-out for multi-value documentType requests."""
        return _header_int(self.headers.get("x-documenttype-fanout"))

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
        """``corpus`` or ``live`` (the ``X-Source`` header)."""
        return self.headers.get("x-source")

    @property
    def corpus_version(self) -> int | None:
        """Monotonic per-CELEX version (present when served from corpus)."""
        return _header_int(self.headers.get("x-corpus-version"))

    @property
    def corpus_fetched_at(self) -> str | None:
        return self.headers.get("x-corpus-fetchedat")

    @property
    def language_served(self) -> str | None:
        """The language rendition actually served (``X-Language`` header)."""
        return self.headers.get("x-language")

    @property
    def language_fallback(self) -> str | None:
        """Set (to ``en``) when the requested language was unavailable."""
        return self.headers.get("x-language-fallback")

    @property
    def cache(self) -> str | None:
        return self.headers.get("x-cache")


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
        return self.headers.get("x-source")

    @property
    def corpus_version(self) -> int | None:
        return _header_int(self.headers.get("x-corpus-version"))

    @property
    def cache(self) -> str | None:
        return self.headers.get("x-cache")


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
    def parameters(self) -> dict:
        """Echo of the resolved query parameters (computed dateFrom/dateTo)."""
        return _dict(self._map, "parameters")

    @property
    def language_filter(self) -> dict | None:
        """Present only when the language post-filter dropped rows."""
        value = self._map.get("languageFilter")
        return value if isinstance(value, dict) else None


# ── Citations ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CitationEdge:
    """A single citation edge with classification and context."""

    citation_type: str | None
    context_snippet: str | None
    target_celex: str | None
    extracted_at: str | None
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> CitationEdge:
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
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> CitationDocumentGroup:
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
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> CitationNeighbour:
        return cls(
            celex_number=_s(data, "celexNumber"),
            title=_s(data, "title"),
            count=_i(data, "count"),
            types=_str_list(data, "types"),
            raw=data,
        )


@dataclass(frozen=True)
class CitationPathNode:
    celex_number: str | None
    title: str | None
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> CitationPathNode:
        return cls(celex_number=_s(data, "celexNumber"), title=_s(data, "title"), raw=data)


@dataclass(frozen=True)
class RelatedDocument:
    """Bibliographic-coupling neighbour."""

    celex_number: str | None
    title: str | None
    shared_count: int | None
    shared_targets: list[str]
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> RelatedDocument:
        return cls(
            celex_number=_s(data, "celexNumber"),
            title=_s(data, "title"),
            shared_count=_i(data, "sharedCount"),
            shared_targets=_str_list(data, "sharedTargets"),
            raw=data,
        )


@dataclass(frozen=True)
class CitationStatsEntry:
    """One row of the mostCited / mostCiting top-10 lists."""

    celex_number: str | None
    title: str | None
    cited_by_count: int | None
    cites_count: int | None
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> CitationStatsEntry:
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
        return _b(self._map, "alreadyExtracted") or False

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
        return _i(self._map, "uniqueDocuments")

    @property
    def limit(self) -> int | None:
        return _i(self._map, "limit")

    @property
    def offset(self) -> int | None:
        return _i(self._map, "offset")


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
        return _i(self._map, "uniqueDocuments")

    @property
    def limit(self) -> int | None:
        return _i(self._map, "limit")

    @property
    def offset(self) -> int | None:
        return _i(self._map, "offset")


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
    payload: dict
    response_status: int | None
    response_body: str | None
    error_message: str | None
    attempt_count: int | None
    next_retry_at: str | None
    delivered_at: str | None
    created_at: str | None
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> WebhookDelivery:
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
        )


@dataclass(frozen=True)
class Webhook:
    """A webhook subscription.

    ``secret`` is populated **only** on the create response — the API never
    returns it again; store it then. ``recent_deliveries`` is populated only
    by ``GET /webhooks/{id}`` (up to 20, newest first).
    """

    id: str | None
    name: str | None
    url: str | None
    status: str | None
    search_criteria: dict
    secret: str | None
    last_checked: str | None
    last_triggered: str | None
    consecutive_failures: int | None
    delivery_count: int | None
    max_retries: int | None
    created_at: str | None
    updated_at: str | None
    recent_deliveries: list[WebhookDelivery]
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Webhook:
        return cls(
            id=_s(data, "id"),
            name=_s(data, "name"),
            url=_s(data, "url"),
            status=_s(data, "status"),
            search_criteria=_dict(data, "searchCriteria"),
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
        )


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
    metadata: dict
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> SemanticCaseLawResult:
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
        )


@dataclass(frozen=True)
class SemanticLegislationResult:
    """Single article-level match from ``/legislation/semantic``."""

    score: float | None
    text: str | None
    article_ref: str | None
    law_id: str | None
    law_title: str | None
    metadata: dict
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> SemanticLegislationResult:
        score = data.get("score")
        return cls(
            score=float(score) if isinstance(score, (int, float)) else None,
            text=_s(data, "text"),
            article_ref=_s(data, "article_ref"),
            law_id=_s(data, "law_id"),
            law_title=_s(data, "law_title"),
            metadata=_dict(data, "metadata"),
            raw=data,
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
    def semantic_usage(self) -> dict | None:
        """Legacy daily-call plans only; credit plans meter from the pool."""
        value = self._map.get("semanticUsage")
        return value if isinstance(value, dict) else None


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
    """``POST /legislation/semantic`` — ranked article-level matches."""

    @property
    def results(self) -> list[SemanticLegislationResult]:
        return _parse_items(self._map, "results", SemanticLegislationResult)


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
    def urls(self) -> dict:
        return _dict(self._map, "urls")
