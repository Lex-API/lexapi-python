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
