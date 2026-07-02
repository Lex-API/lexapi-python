"""Asynchronous LexAPI client (mirrors :class:`lexapi.LexAPI`)."""

from __future__ import annotations

import asyncio
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


class AsyncLexAPI:
    """Asynchronous LexAPI client.

    Same configuration surface as :class:`lexapi.LexAPI`; every operation
    method is a coroutine. Shares all request-building, retry, and
    response-parsing logic with the sync client.
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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._retry = make_retry_config(max_retries, retry_config)
        self._http = httpx.AsyncClient(
            transport=transport,
            **build_httpx_kwargs(
                api_key, base_url, timeout, connect_timeout, user_agent_suffix
            ),
        )

    # ── Meta ──────────────────────────────────────────────────────────
    async def get_info(self) -> APIResponse:
        """GET /info — API capabilities, subscription tier, and current usage.

        Free operation (0 credits).
        """
        return await self._request("GET", "/info")

    # ── Search ────────────────────────────────────────────────────────
    async def search(
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
        """POST /search — see :meth:`lexapi.LexAPI.search`."""
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
        return await self._request("POST", "/search", json=body, cast=SearchResponse)

    # ── Documents ─────────────────────────────────────────────────────
    async def get_document(
        self,
        celex: str,
        *,
        bypass_corpus: bool | None = None,
        language: str | None = None,
        include: str | list[str] | None = None,
        fields: str | list[str] | None = None,
        article_id: str | None = None,
    ) -> DocumentResponse:
        """POST /documentContent — see :meth:`lexapi.LexAPI.get_document`."""
        body = _payloads.document_content_body(
            celex,
            bypass_corpus=bypass_corpus,
            language=language,
            include=include,
            fields=fields,
            article_id=article_id,
        )
        return await self._request(
            "POST", "/documentContent", json=body, cast=DocumentResponse
        )

    async def get_documents_batch(
        self,
        celexes: list[str],
        *,
        bypass_corpus: bool | None = None,
        language: str | None = None,
        include: str | list[str] | None = None,
        fields: str | list[str] | None = None,
        article_id: str | None = None,
    ) -> DocumentBatchResponse:
        """POST /documentContent/batch — see :meth:`lexapi.LexAPI.get_documents_batch`."""
        body = _payloads.document_batch_body(
            celexes,
            bypass_corpus=bypass_corpus,
            language=language,
            include=include,
            fields=fields,
            article_id=article_id,
        )
        return await self._request(
            "POST", "/documentContent/batch", json=body, cast=DocumentBatchResponse
        )

    async def get_recent_documents(
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
        """GET /documents/recent — see :meth:`lexapi.LexAPI.get_recent_documents`."""
        params = _payloads.recent_documents_params(
            days=days,
            document_type=document_type,
            author=author,
            domain=domain,
            subdomain=subdomain,
            language=language,
            limit=limit,
        )
        return await self._request(
            "GET", "/documents/recent", params=params, cast=RecentDocumentsResponse
        )

    async def get_document_by_url(
        self,
        url: str,
        *,
        include: str | list[str] | None = None,
        fields: str | list[str] | None = None,
        article_id: str | None = None,
    ) -> DocumentByUrlResponse:
        """POST /documents/url — see :meth:`lexapi.LexAPI.get_document_by_url`."""
        body = _payloads.document_by_url_body(
            url, include=include, fields=fields, article_id=article_id
        )
        return await self._request(
            "POST", "/documents/url", json=body, cast=DocumentByUrlResponse
        )

    async def get_document_metadata(
        self, celex: str, *, bypass_corpus: bool | None = None
    ) -> DocumentMetadataResponse:
        """POST /documents/metadata — see :meth:`lexapi.LexAPI.get_document_metadata`."""
        body = _payloads.drop_none({"celexNumber": celex, "bypassCorpus": bypass_corpus})
        return await self._request(
            "POST", "/documents/metadata", json=body, cast=DocumentMetadataResponse
        )

    async def resolve(self, identifier: str) -> ResolveResponse:
        """POST /resolve — see :meth:`lexapi.LexAPI.resolve`."""
        return await self._request(
            "POST", "/resolve", json={"identifier": identifier}, cast=ResolveResponse
        )

    # ── Request core ──────────────────────────────────────────────────
    async def _request(
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
                response = await self._http.request(method, path, **request_kwargs)
            except httpx.HTTPError as exc:
                wait = exception_retry_wait(exc, attempt, self._retry)
                if wait is None:
                    raise transport_error(exc) from exc
                await asyncio.sleep(wait)
                attempt += 1
                continue
            wait = status_retry_wait(method, response, attempt, self._retry)
            if wait is None:
                return process_response(response, cast)
            await asyncio.sleep(wait)
            attempt += 1

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncLexAPI:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
