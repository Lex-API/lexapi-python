"""Synchronous LexAPI client."""

from __future__ import annotations

import time
from typing import Any, TypeVar

import httpx

from lexapi import _payloads
from lexapi._base import (
    DEFAULT_BASE_URL,
    build_httpx_kwargs,
    exception_retry_wait,
    make_retry_config,
    process_response,
    status_retry_wait,
    transport_error,
)
from lexapi._response import APIResponse
from lexapi._retry import RetryConfig
from lexapi.models import (
    DocumentBatchResponse,
    DocumentByUrlResponse,
    DocumentMetadataResponse,
    DocumentResponse,
    RecentDocumentsResponse,
    ResolveResponse,
    SearchResponse,
)

R = TypeVar("R", bound=APIResponse)


class LexAPI:
    """Synchronous LexAPI client.

    Args:
        api_key: LexAPI key (``lex_...``). Falls back to ``LEXAPI_API_KEY``.
            Keys are server-side secrets — never ship them in client-side code.
        base_url: API base URL (defaults to production).
        timeout: read/overall timeout in seconds, or a full ``httpx.Timeout``.
        connect_timeout: connect timeout in seconds (ignored when ``timeout``
            is an ``httpx.Timeout``).
        max_retries: retry budget for 429/502/503/504 and connect errors
            (default 3). ``0`` disables retries.
        retry_config: full :class:`~lexapi._retry.RetryConfig` override;
            takes precedence over ``max_retries``.
        user_agent_suffix: optional suffix appended to the
            ``lexapi-python/<version>`` User-Agent.
        transport: optional httpx transport (used for testing).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout = 60.0,
        connect_timeout: float = 10.0,
        max_retries: int | None = None,
        retry_config: RetryConfig | None = None,
        user_agent_suffix: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._retry = make_retry_config(max_retries, retry_config)
        self._http = httpx.Client(
            transport=transport,
            **build_httpx_kwargs(
                api_key, base_url, timeout, connect_timeout, user_agent_suffix
            ),
        )

    # ── Meta ──────────────────────────────────────────────────────────
    def get_info(self) -> APIResponse:
        """GET /info — API capabilities, subscription tier, and current usage.

        Free operation (0 credits).
        """
        return self._request("GET", "/info")

    # ── Search ────────────────────────────────────────────────────────
    def search(
        self,
        query: str | None = None,
        *,
        text_scope: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        year: int | list[int] | None = None,
        month: int | list[int] | None = None,
        document_type: str | list[str] | None = None,
        author: str | list[str] | None = None,
        procedure: str | list[str] | None = None,
        domain: str | None = None,
        subdomain: str | list[str] | None = None,
        language: str | None = None,
        max_pages: int | None = None,
    ) -> SearchResponse:
        """POST /search — search EUR-Lex with structured filters.

        At least one filter is required by the server. ``max_pages`` is
        clamped to the subscription tier; check ``response.truncated`` /
        ``response.partial`` / ``response.warning`` for capping signals.
        Prefer 1-3 distinctive query terms — EUR-Lex keyword matching is
        literal (use semantic search for concept queries).
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
        )
        return self._request("POST", "/search", json=body, cast=SearchResponse)

    # ── Documents ─────────────────────────────────────────────────────
    def get_document(
        self,
        celex: str,
        *,
        bypass_corpus: bool | None = None,
        language: str | None = None,
        include: str | list[str] | None = None,
        fields: str | list[str] | None = None,
        article_id: str | None = None,
    ) -> DocumentResponse:
        """POST /documentContent — fetch one fully-parsed document by CELEX.

        Corpus-first read path: ``response.source`` reports ``corpus`` or
        ``live``; pass ``bypass_corpus=True`` to force a live scrape.
        ``include``/``fields`` trim the payload to the listed pieces
        (``metadata``, ``fullText``, ``articles``, ``recitals``, ``sections``,
        ``tables``, ``annexes``); ``article_id`` narrows to one article.
        """
        body = _payloads.document_content_body(
            celex,
            bypass_corpus=bypass_corpus,
            language=language,
            include=include,
            fields=fields,
            article_id=article_id,
        )
        return self._request("POST", "/documentContent", json=body, cast=DocumentResponse)

    def get_documents_batch(
        self,
        celexes: list[str],
        *,
        bypass_corpus: bool | None = None,
        language: str | None = None,
        include: str | list[str] | None = None,
        fields: str | list[str] | None = None,
        article_id: str | None = None,
    ) -> DocumentBatchResponse:
        """POST /documentContent/batch — fetch multiple documents at once.

        Per-CELEX failures land in ``response.errors`` instead of aborting
        the batch. The batch size is clamped to the tier ceiling —
        ``response.trimmed`` / ``trimmed_to`` / ``trimmed_reason`` (and the
        ``X-Warning`` header via ``response.warning``) report clamping.
        """
        body = _payloads.document_batch_body(
            celexes,
            bypass_corpus=bypass_corpus,
            language=language,
            include=include,
            fields=fields,
            article_id=article_id,
        )
        return self._request(
            "POST", "/documentContent/batch", json=body, cast=DocumentBatchResponse
        )

    def get_recent_documents(
        self,
        *,
        days: int | None = None,
        document_type: str | None = None,
        author: str | None = None,
        domain: str | None = None,
        subdomain: str | None = None,
        language: str | None = None,
        limit: int | None = None,
    ) -> RecentDocumentsResponse:
        """GET /documents/recent — documents published in the last ``days`` days."""
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
        include: str | list[str] | None = None,
        fields: str | list[str] | None = None,
        article_id: str | None = None,
    ) -> DocumentByUrlResponse:
        """POST /documents/url — fetch a document by any EUR-Lex URL.

        The response echoes ``source_url`` and the ``extracted_celex``.
        """
        body = _payloads.document_by_url_body(
            url, include=include, fields=fields, article_id=article_id
        )
        return self._request("POST", "/documents/url", json=body, cast=DocumentByUrlResponse)

    def get_document_metadata(
        self, celex: str, *, bypass_corpus: bool | None = None
    ) -> DocumentMetadataResponse:
        """POST /documents/metadata — metadata only (faster/cheaper than content)."""
        body = _payloads.drop_none({"celexNumber": celex, "bypassCorpus": bypass_corpus})
        return self._request(
            "POST", "/documents/metadata", json=body, cast=DocumentMetadataResponse
        )

    def resolve(self, identifier: str) -> ResolveResponse:
        """POST /resolve — resolve a CELEX / EUR-Lex URL / ELI / ECLI to a CELEX."""
        return self._request(
            "POST", "/resolve", json={"identifier": identifier}, cast=ResolveResponse
        )

    # ── Request core ──────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        cast: type[R] = APIResponse,  # type: ignore[assignment]
        timeout: float | httpx.Timeout | None = None,
    ) -> R:
        request_kwargs: dict[str, Any] = {"params": params, "json": json}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        attempt = 0
        while True:
            try:
                response = self._http.request(method, path, **request_kwargs)
            except httpx.HTTPError as exc:
                wait = exception_retry_wait(exc, attempt, self._retry)
                if wait is None:
                    raise transport_error(exc) from exc
                time.sleep(wait)
                attempt += 1
                continue
            wait = status_retry_wait(method, response, attempt, self._retry)
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
