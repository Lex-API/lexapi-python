"""Synchronous LexAPI client."""

from __future__ import annotations

import copy
import datetime as _dt
import time
from typing import Any, TypeVar

import httpx

from lexapi import _payloads
from lexapi._base import (
    DEFAULT_BASE_URL,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_TIMEOUT,
    build_httpx_kwargs,
    exception_retry_wait,
    make_retry_config,
    make_timeout,
    process_response,
    status_retry_wait,
    transport_error,
)
from lexapi._payloads import DateLike, OneOrMany, seg
from lexapi._response import APIResponse
from lexapi._retry import RetryConfig
from lexapi.citations import CitationsResource
from lexapi.export import ExportStream, export_params
from lexapi.models import (
    DocumentBatchResponse,
    DocumentByUrlResponse,
    DocumentMetadataResponse,
    DocumentResponse,
    DocumentVersionResponse,
    DocumentVersionsResponse,
    InfoResponse,
    RecentDocumentsResponse,
    ResolveResponse,
    SearchResponse,
    SemanticLegislationResponse,
    SemanticSearchResponse,
)
from lexapi.national import NationalResource
from lexapi.webhooks import WebhooksResource

R = TypeVar("R", bound=APIResponse)


class LexAPI:
    """Synchronous LexAPI client.

    Args:
        api_key: LexAPI key (``lex_...``). Falls back to ``LEXAPI_API_KEY``.
            Keys are server-side secrets — never ship them in client-side code.
        base_url: API base URL (defaults to production,
            ``https://lex-api.com/api/v1``).
        timeout: per-attempt response timeout in seconds — the longest wait
            for the next bytes of a response (its head, then each body chunk);
            not a deadline for the whole call. Default 95: above the API's
            90 s budget for one upstream EUR-Lex fetch, below Cloudflare's
            100 s limit. Or a full ``httpx.Timeout``.
        connect_timeout: connect timeout in seconds (TCP + TLS, and the wait
            for a pooled connection); ignored when ``timeout`` is an
            ``httpx.Timeout``.
        max_retries: retry budget for 429/502/503/504/520-524 and connect
            errors (default 3). ``0`` disables retries.
        retry_config: full :class:`~lexapi._retry.RetryConfig` override;
            takes precedence over ``max_retries``.
        user_agent_suffix: optional suffix appended to the
            ``lexapi-python/<version>`` User-Agent.
        transport: optional httpx transport (used for testing).

    Per-call overrides: ``client.with_options(timeout=..., max_retries=...)``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        max_retries: int | None = None,
        retry_config: RetryConfig | None = None,
        user_agent_suffix: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._retry = make_retry_config(max_retries, retry_config)
        self._timeout: httpx.Timeout | None = None
        self._http = httpx.Client(
            transport=transport,
            **build_httpx_kwargs(api_key, base_url, timeout, connect_timeout, user_agent_suffix),
        )
        self._bind_resources()

    def _bind_resources(self) -> None:
        #: Citation-graph operations (``client.citations.cited_by(...)``, ...).
        self.citations = CitationsResource(self)
        #: Webhook subscriptions (``client.webhooks.create(...)``, ...).
        self.webhooks = WebhooksResource(self)
        #: National-law operations (``client.national.coverage()``, ...).
        self.national = NationalResource(self)

    def with_options(
        self,
        *,
        timeout: float | httpx.Timeout | None = None,
        connect_timeout: float | None = None,
        max_retries: int | None = None,
        retry_config: RetryConfig | None = None,
    ) -> LexAPI:
        """A view of this client with a different timeout and/or retry policy.

        Shares the underlying connection pool (closing either closes both),
        so it is cheap to create per call. Values above ~100 s buy nothing
        on the public endpoint — Cloudflare answers 524 at 100 s::

            client.with_options(timeout=30).semantic_legislation_search(
                "own funds requirements", as_of="2019-06-01"
            )
        """
        clone = copy.copy(self)
        if timeout is not None or connect_timeout is not None:
            current = self._timeout or self._http.timeout
            if isinstance(timeout, httpx.Timeout):
                clone._timeout = timeout
            else:
                read = timeout if timeout is not None else current.read
                connect = connect_timeout if connect_timeout is not None else current.connect
                clone._timeout = make_timeout(
                    read if read is not None else DEFAULT_TIMEOUT,
                    connect if connect is not None else DEFAULT_CONNECT_TIMEOUT,
                )
        if retry_config is not None or max_retries is not None:
            clone._retry = make_retry_config(max_retries, retry_config)
        clone._bind_resources()
        return clone

    # ── Meta ──────────────────────────────────────────────────────────
    def get_info(self) -> InfoResponse:
        """GET /info — API capabilities, subscription tier, and current usage.

        Free operation (0 credits). ``response.search_filters`` and
        ``response.supported_languages`` carry the live filter enumerations.
        """
        return self._request("GET", "/info", cast=InfoResponse, free=True)

    # ── Search ────────────────────────────────────────────────────────
    def search(
        self,
        query: str | None = None,
        *,
        text_scope: str | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        year: int | list[int] | None = None,
        month: int | list[int] | None = None,
        document_type: OneOrMany | None = None,
        author: OneOrMany | None = None,
        procedure: OneOrMany | None = None,
        domain: str | None = None,
        subdomain: OneOrMany | None = None,
        language: str | None = None,
        max_pages: int | None = None,
        fresh: bool | None = None,
    ) -> SearchResponse:
        """POST /search — search EUR-Lex with structured filters.

        At least one of ``query``, a date, ``year``/``month``,
        ``document_type``, ``author``, ``procedure``, ``domain`` or
        ``subdomain`` is required. ``query="*"`` means "no text constraint"
        (a filter-only search). Prefer 1-3 distinctive terms — EUR-Lex keyword
        matching is literal (use semantic search for concept queries).

        **Cost: 3 credits per result page**, charged upfront for ``max_pages``
        (clamped to the tier: FREE 1, STARTER 5, PROFESSIONAL 20, BUSINESS+
        100) and refunded for pages never fetched. Identical searches within
        60 minutes are served from a result cache (``response.cache``; same
        price); ``fresh=True`` forces a re-fetch.

        Capping and fallbacks are surfaced, never hidden: ``response.truncated``
        / ``partial`` / ``post_filtered_by`` / ``warning`` (``X-Warning``), and
        ``response.source`` names the upstream (``soap``, ``cellar``,
        ``corpus`` — a corpus answer is always ``partial``).

        List-valued ``document_type`` fans out server-side (one EUR-Lex query
        per type, merged; ``response.document_type_fan_out``). A ``subdomain``
        list is OR-ed on the EUR-Lex web service; if the search falls back to
        EUR-Lex result pages, only the first subdomain applies.
        """
        body = _payloads.search_body(
            query,
            text_scope=text_scope,
            date_from=date_from,
            date_to=date_to,
            year=year,
            month=month,
            document_type=document_type,
            author=author,
            procedure=procedure,
            domain=domain,
            subdomain=subdomain,
            language=language,
            max_pages=max_pages,
            fresh=fresh,
        )
        return self._request("POST", "/search", json=body, cast=SearchResponse, read_only=True)

    # ── Documents ─────────────────────────────────────────────────────
    def get_document(
        self,
        celex: str,
        *,
        bypass_corpus: bool | None = None,
        language: str | None = None,
        accept_languages: str | list[str] | None = None,
        include: OneOrMany | None = None,
        fields: OneOrMany | None = None,
        article_id: str | None = None,
    ) -> DocumentResponse:
        """POST /documentContent — fetch one fully-parsed document by CELEX.

        Corpus-first: ``response.source`` reports ``corpus`` (settles at 1
        credit), ``cellar`` / ``cellar-oj`` (4) or ``live`` (5).
        ``bypass_corpus=True`` forces a fresh upstream read (billed at the
        live rate). ``include``/``fields`` trim the payload to the listed
        pieces (``metadata``, ``fullText``, ``articles``, ``recitals``,
        ``sections``, ``tables``, ``annexes``); ``article_id`` (``art_17``,
        ``17`` or ``Article 17``) narrows to one article.

        ``language`` (default ``en``) localises the whole document; when the
        document has no text in it, English is served (``document.language_
        fallback``). ``accept_languages`` (an ordered list of ISO codes, or
        ``"any"``) opts into serving the first of those that has text instead.

        A document whose notice exists but whose text EUR-Lex does not publish
        is a 200 with ``document.content is None`` and a
        ``document.text_availability`` block saying why (billed 1 credit).
        """
        body = _payloads.document_content_body(
            celex,
            bypass_corpus=bypass_corpus,
            language=language,
            accept_languages=accept_languages,
            include=include,
            fields=fields,
            article_id=article_id,
        )
        return self._request(
            "POST", "/documentContent", json=body, cast=DocumentResponse, read_only=True
        )

    def get_documents_batch(
        self,
        celexes: list[str],
        *,
        bypass_corpus: bool | None = None,
        language: str | None = None,
        accept_languages: str | list[str] | None = None,
        include: OneOrMany | None = None,
        fields: OneOrMany | None = None,
        article_id: str | None = None,
    ) -> DocumentBatchResponse:
        """POST /documentContent/batch — fetch multiple documents at once.

        Per-CELEX failures land in ``response.errors`` instead of aborting
        the batch; documents without text stay in ``documents``
        (``response.text_unavailable`` counts them). The batch size is clamped
        to the tier ceiling (FREE 1, STARTER 5, PROFESSIONAL 20, BUSINESS+ 50)
        — ``response.trimmed`` / ``trimmed_to`` / ``trimmed_reason`` (and
        ``response.warning``) report clamping; trimmed items are not charged.
        5 credits per processed item, settled per source (``corpus_hits`` at
        1, ``cellar_served`` / ``oj_recovered`` at 4, ``live_fetches`` at 5 —
        or 1 for a live item that turned out to have no text).
        """
        body = _payloads.document_batch_body(
            celexes,
            bypass_corpus=bypass_corpus,
            language=language,
            accept_languages=accept_languages,
            include=include,
            fields=fields,
            article_id=article_id,
        )
        return self._request(
            "POST", "/documentContent/batch", json=body, cast=DocumentBatchResponse, read_only=True
        )

    def get_recent_documents(
        self,
        *,
        days: int | None = None,
        document_type: str | None = None,
        author: OneOrMany | None = None,
        domain: str | None = None,
        subdomain: str | None = None,
        language: str | None = None,
        limit: int | None = None,
    ) -> RecentDocumentsResponse:
        """GET /documents/recent — documents published in the last ``days``
        days (default 7, up to ``limit`` results, default 50).

        Admission reserves 3 credits per result page needed for ``limit``
        (``ceil(limit / 10)`` pages, tier-clamped), so a nearly empty pool can
        402 even when the answer would settle lower. Unfiltered requests are
        answered from Cellar (``response.source == "cellar"``) and settle at 3
        credits whatever the ``limit``; filtering on ``document_type`` /
        ``author`` / ``domain`` / ``subdomain`` fetches from EUR-Lex and keeps
        3 credits per page fetched (unfetched pages refunded).

        ``document_type`` and ``subdomain`` take **one** value: this endpoint
        has no multi-type fan-out and EUR-Lex honours only the first of
        several, so a list of more than one raises :class:`ValueError` (use
        :meth:`search` for multi-value filters). ``author`` accepts a list.
        """
        params = _payloads.recent_documents_params(
            days=days,
            document_type=document_type,
            author=author,
            domain=domain,
            subdomain=subdomain,
            language=language,
            limit=limit,
        )
        return self._request(
            "GET", "/documents/recent", params=params, cast=RecentDocumentsResponse
        )

    def get_document_by_url(
        self,
        url: str,
        *,
        bypass_corpus: bool | None = None,
        include: OneOrMany | None = None,
        fields: OneOrMany | None = None,
        article_id: str | None = None,
    ) -> DocumentByUrlResponse:
        """POST /documents/url — fetch a document by any EUR-Lex URL.

        The URL must be on ``eur-lex.europa.eu`` and carry a ``CELEX:``
        segment or ``uri=`` parameter (else 400 ``INVALID_URL``). The response
        echoes ``source_url`` and the ``extracted_celex``; billed like
        :meth:`get_document`. Served in English.
        """
        body = _payloads.document_by_url_body(
            url,
            bypass_corpus=bypass_corpus,
            include=include,
            fields=fields,
            article_id=article_id,
        )
        return self._request(
            "POST", "/documents/url", json=body, cast=DocumentByUrlResponse, read_only=True
        )

    def get_document_metadata(
        self, celex: str, *, bypass_corpus: bool | None = None
    ) -> DocumentMetadataResponse:
        """POST /documents/metadata — metadata only (no body parse).

        5 credits, settled at 1 from the corpus and 4 from Cellar
        (``response.source``).
        """
        body = _payloads.drop_none({"celexNumber": celex, "bypassCorpus": bypass_corpus})
        return self._request(
            "POST", "/documents/metadata", json=body, cast=DocumentMetadataResponse, read_only=True
        )

    def resolve(self, identifier: str) -> ResolveResponse:
        """POST /resolve — resolve a CELEX / EUR-Lex URL / ELI / ECLI to a CELEX.

        1 credit. An identifier that doesn't resolve (e.g. an ECLI not yet in
        the corpus) raises :class:`~lexapi.NotFoundError`.
        """
        return self._request(
            "POST",
            "/resolve",
            json={"identifier": identifier},
            cast=ResolveResponse,
            read_only=True,
        )

    # ── Point-in-time versions ────────────────────────────────────────
    # Versions are LexAPI observation snapshots (content-hash changes
    # between fetches), NOT legal in-force reconstructions; history begins
    # at first ingestion. For the text in force on a date, use
    # semantic_legislation_search(as_of=...) or a consolidated CELEX.

    def list_document_versions(
        self, celex: str, *, language: str | None = None
    ) -> DocumentVersionsResponse:
        """GET /documents/{celex}/versions — version history, newest first (1 credit)."""
        params = _payloads.drop_none({"language": language})
        return self._request(
            "GET",
            f"/documents/{seg(celex)}/versions",
            params=params,
            cast=DocumentVersionsResponse,
        )

    def get_document_version(
        self, celex: str, version: int, *, language: str | None = None
    ) -> DocumentVersionResponse:
        """GET /documents/{celex}/versions/{version} — one immutable
        snapshot with content (1 credit)."""
        params = _payloads.drop_none({"language": language})
        return self._request(
            "GET",
            f"/documents/{seg(celex)}/versions/{seg(version)}",
            params=params,
            cast=DocumentVersionResponse,
        )

    def get_document_at_date(
        self, celex: str, date: DateLike, *, language: str | None = None
    ) -> DocumentVersionResponse:
        """GET /documents/{celex}/at/{date} — the snapshot observed as current on ``date``.

        ``date`` is ``YYYY-MM-DD`` or a :class:`datetime.date` (end-of-day UTC
        inclusive). Dates before the document entered the corpus raise
        :class:`NotFoundError` with the tracking start date in the message.
        1 credit.
        """
        params = _payloads.drop_none({"language": language})
        return self._request(
            "GET",
            f"/documents/{seg(celex)}/at/{seg(_payloads.iso_date(date))}",
            params=params,
            cast=DocumentVersionResponse,
        )

    # ── Semantic search ───────────────────────────────────────────────
    def semantic_search(
        self,
        query: str,
        *,
        hyde: bool = False,
        min_score: float | None = None,
        limit: int | None = None,
        language: str | None = None,
        include_text: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> SemanticSearchResponse:
        """POST /search/semantic — embedding-based CJEU case-law search (5 credits).

        Args:
            query: natural-language query to embed.
            hyde: enable HyDE query rewriting — an LLM drafts the passage a
                relevant judgment would contain and retrieval fuses plain-query
                and hypothetical-passage rankings. Markedly better recall on
                short or keyword-style queries. **Bills 15 credits instead of
                5** and adds ~1-3 s of latency. Best-effort: on generation
                failure the search falls back to plain retrieval, the response
                reports ``hyde=False``, and the 10-credit premium is
                **refunded automatically** (``response.units_charged`` then
                reflects the base rate).
            min_score: drop results below this cosine similarity. Without it
                the upstream applies a ~0.7 relevance floor — if you got fewer
                results than ``limit``, lower this (e.g. 0.5) to widen recall,
                or pass 0 to disable the floor.
            limit: max results, clamped to the tier's ``maxResults`` (FREE 10,
                STARTER 20, PROFESSIONAL 50, BUSINESS and above 100).
            language: language of returned content (default ``en``; the index
                covers ``en fr de es it sl el nl pt pl``).
            include_text: receive full document text per hit (``hit.text``)
                instead of the default ~600-char ``hit.snippet`` (10-50x
                payload increase).
            filters: free-form filter object forwarded to the backend.

        The response exposes ``hint`` (low-confidence signal), ``hyde``
        (whether HyDE actually ran), and ``hypothetical_document`` (the
        LLM-drafted passage, when HyDE ran).
        """
        body = _payloads.semantic_search_body(
            query,
            hyde=hyde,
            min_score=min_score,
            limit=limit,
            language=language,
            include_text=include_text,
            filters=filters,
        )
        return self._request(
            "POST", "/search/semantic", json=body, cast=SemanticSearchResponse, read_only=True
        )

    def semantic_legislation_search(
        self,
        query: str,
        *,
        min_score: float | None = None,
        limit: int | None = None,
        language: str | None = None,
        include_text: bool = False,
        filters: dict[str, Any] | None = None,
        as_of: DateLike | None = None,
    ) -> SemanticLegislationResponse:
        """POST /legislation/semantic — embedding-based EU legislation search.

        Article-level matches (``article_ref``, ``law_id``, ``law_title``,
        ``score``). Same ``min_score`` / ``language`` / ``include_text``
        semantics as :meth:`semantic_search`; ``limit`` is capped at 50 here.
        HyDE is case-law only, so it is not offered. When an act and its
        consolidated version match the same article you get one hit, the
        consolidated one, labelled ``consolidated`` / ``base_celex`` /
        ``consolidated_note`` with the authentic act's match in ``base_act``.

        Args:
            as_of: ``YYYY-MM-DD`` (or a :class:`datetime.date`), 1952-01-01 to
                today UTC. Each hit answers with the version in force on that
                date — the act as published before its first consolidation,
                otherwise the consolidated version in force — after checking
                the act was in force then. Hits that cannot be answered keep
                their latest text with ``as_of_resolved=False`` and an
                ``as_of_reason``. A malformed or out-of-range date is a 400
                (refunded).

        **Cost:** 5 credits for the search. With ``as_of``, each version the
        corpus doesn't hold is fetched live (at most 3 per request by
        default): each fetch reserves 5 credits just before it starts and
        settles at 4 (Cellar), 5 (browser), 1 (no text) or 0 (failed / timed
        out). A fetch the pool can't cover is skipped (``as_of_reason ==
        "insufficient_credits"``); only the search itself can 402. Corpus
        reads are free. ``as_of`` requests can take longer — the server
        answers within its 45 s default budget, inside the client's 95 s
        response timeout.
        """
        body = _payloads.semantic_search_body(
            query,
            min_score=min_score,
            limit=limit,
            language=language,
            include_text=include_text,
            filters=filters,
            as_of=as_of,
        )
        return self._request(
            "POST",
            "/legislation/semantic",
            json=body,
            cast=SemanticLegislationResponse,
            read_only=True,
        )

    # ── Export ────────────────────────────────────────────────────────
    def export(
        self,
        *,
        document_type: OneOrMany | None = None,
        document_type_code: OneOrMany | None = None,
        author: OneOrMany | None = None,
        subdomain: OneOrMany | None = None,
        language: str | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        fetched_since: str | _dt.datetime | None = None,
        changed_since: str | _dt.datetime | None = None,
        include_html: bool | None = None,
        include_content: bool | None = None,
        include_stubs: bool | None = None,
        limit: int | None = None,
    ) -> ExportStream:
        """GET /export — stream matching corpus rows as NDJSON (BUSINESS,
        SCALE and ENTERPRISE; lower tiers raise
        :class:`~lexapi.TierForbiddenError`). Billing is
        described below.

        Returns an :class:`~lexapi.export.ExportStream`: iterate it for one
        dict per corpus row. The leading ``_meta`` envelope and trailing
        ``_done`` line are captured on ``.meta`` / ``.done`` (with
        ``.total`` / ``.streaming`` / ``.truncated`` from the export
        headers, and ``.written`` / ``.skipped_stubs`` from the trailer).
        Use it as a context manager to guarantee the connection is closed::

            with client.export(document_type="regulation", limit=1000) as stream:
                for row in stream:
                    handle(row["celex"], row.get("parsedContent"))
                print(stream.done, stream.truncated)

        Filters (requires lex-api API 2.3.0; list values are sent
        comma-separated and ORed):

        - ``document_type``: the stored label, matched exactly ignoring case
          (``"regulation"`` matches ``Regulation``, not ``Implementing
          regulation``).
        - ``document_type_code``: a type family by the ``documentTypeCode``
          each row carries (``"regulation"`` includes implementing and
          delegated regulations).
        - ``author``: whole stored author values, ignoring case.
        - ``date_from`` / ``date_to``: document-date range, ``YYYY-MM-DD``
          (or ``datetime.date``).
        - ``fetched_since``: rows whose ``lastSeenAt`` is at or after the time
          (any write to the row). ``changed_since``: rows whose text changed
          (``contentChangedAt``) — the cheapest incremental sync. Both take an
          ISO 8601 date or a date-time *with a timezone*, or a timezone-aware
          :class:`datetime.datetime` (a naive one raises ``ValueError``).
        - ``limit``: a positive integer (else ``ValueError``); the server
          clamps it to its 50,000-row ceiling.

        Billing (credit plans): 1 credit per row written, at least 1 per
        export. The call is admitted for 1 credit, the matching rows are
        counted, and the rest of 1 credit per row it can return is reserved;
        a pool that cannot cover it raises
        :class:`~lexapi.CreditsExhaustedError` before anything streams.
        Unwritten rows are refunded when the stream ends; the settled charge
        is ``stream.done["credits"]["charged"]``. Metadata-only rows (no
        text) are skipped unless ``include_stubs=True``. Licensing: see
        Terms §6 — training ML models on exported content needs a separate
        licence.
        """
        params = export_params(
            document_type=document_type,
            document_type_code=document_type_code,
            author=author,
            subdomain=subdomain,
            language=language,
            date_from=date_from,
            date_to=date_to,
            fetched_since=fetched_since,
            changed_since=changed_since,
            include_html=include_html,
            include_content=include_content,
            include_stubs=include_stubs,
            limit=limit,
        )
        return ExportStream(self._stream_request("GET", "/export", params=params))

    def _stream_request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Send a streaming request with the same retry/error semantics as
        :meth:`_request`; the response body is left unread on success."""
        attempt = 0
        while True:
            request = self._http.build_request(
                method, path, params=params, **self._timeout_kwargs()
            )
            try:
                response = self._http.send(request, stream=True)
            except httpx.HTTPError as exc:
                wait = exception_retry_wait(exc, attempt, self._retry, method)
                if wait is None:
                    raise transport_error(exc) from exc
                time.sleep(wait)
                attempt += 1
                continue
            if response.is_success:
                return response
            response.read()
            wait = status_retry_wait(method, response, attempt, self._retry)
            if wait is None:
                process_response(response, APIResponse)  # raises the typed error
                raise AssertionError("unreachable")  # pragma: no cover
            response.close()
            time.sleep(wait)
            attempt += 1

    # ── Request core ──────────────────────────────────────────────────
    def _timeout_kwargs(self, timeout: float | httpx.Timeout | None = None) -> dict[str, Any]:
        if timeout is not None:
            return {"timeout": timeout}
        if self._timeout is not None:
            return {"timeout": self._timeout}
        return {}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        cast: type[R] = APIResponse,  # type: ignore[assignment]
        timeout: float | httpx.Timeout | None = None,
        read_only: bool = False,
        free: bool = False,
    ) -> R:
        """One API call with retries.

        ``read_only`` marks a POST that changes nothing (eligible for
        ``RetryConfig.retry_read_only_posts``); ``free`` marks a 0-credit
        operation, for which any transport failure or 5xx is billing-safe to
        retry (PLAN.md §1.2).
        """
        request_kwargs: dict[str, Any] = {"params": params, "json": json}
        request_kwargs.update(self._timeout_kwargs(timeout))
        attempt = 0
        while True:
            try:
                response = self._http.request(method, path, **request_kwargs)
            except httpx.HTTPError as exc:
                wait = exception_retry_wait(exc, attempt, self._retry, method, free=free)
                if wait is None:
                    raise transport_error(exc) from exc
                time.sleep(wait)
                attempt += 1
                continue
            wait = status_retry_wait(
                method, response, attempt, self._retry, read_only=read_only, free=free
            )
            if wait is None:
                return process_response(response, cast)
            time.sleep(wait)
            attempt += 1

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LexAPI:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
