"""National-law resource namespace: ``client.national.*``.

National law launches 2026-11-03 (lex-api). Call :meth:`NationalResource.coverage`
first: a jurisdiction that is not (yet) launched answers every other national
endpoint with a plain 404, exactly like a path that does not exist — it never
advertises unreleased coverage.

Pricing below BUSINESS: the four non-coverage endpoints below draw from a
monthly allowance of national calls (FREE 30, STARTER 500, PROFESSIONAL
2,000 per UTC month; lex-api PR #306) on top of the ordinary credit cost. A
used-up allowance raises :class:`~lexapi.errors.NationalAllowanceExhaustedError`
(402) before anything is charged. BUSINESS and above have no separate limit.
Every response from these four endpoints carries ``X-National-Limit`` /
``X-National-Remaining`` / ``X-National-Reset`` for FREE/STARTER/PROFESSIONAL
accounts, read via :attr:`~lexapi._response.APIResponse.national_allowance_limit`
/ ``national_allowance_remaining`` / ``national_allowance_resets_at``.

At the time this module was written, the public coverage endpoint and the
PR #306 allowance had not yet been deployed — re-verify against the live API
once both ship.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lexapi._payloads import drop_none, seg
from lexapi.models import (
    NationalCoverageResponse,
    NationalDocumentListResponse,
    NationalDocumentResponse,
    NationalSemanticLegislationResponse,
    NationalSemanticSearchResponse,
)

if TYPE_CHECKING:  # pragma: no cover
    from lexapi.async_client import AsyncLexAPI
    from lexapi.client import LexAPI


def _jur(jurisdiction: str) -> str:
    """The route segment for a jurisdiction code (lower-cased, e.g. ``at``)."""
    return seg(jurisdiction.lower())


def _bool(value: bool | None) -> str | None:
    """``true`` / ``false`` (httpx would otherwise send Python's ``True``/``False``)."""
    if value is None:
        return None
    return "true" if value else "false"


def _list_params(
    page: int | None,
    limit: int | None,
    q: str | None,
    document_type: str | None,
    include_repealed: bool | None,
) -> dict[str, Any]:
    return drop_none(
        {
            "page": page,
            "limit": limit,
            "q": q,
            "type": document_type,
            "includeRepealed": _bool(include_repealed),
        }
    )


def _semantic_case_law_body(
    query: str,
    *,
    hyde: bool,
    min_score: float | None,
    limit: int | None,
    language: str | None,
    include_text: bool,
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    body = drop_none(
        {
            "query": query,
            "min_score": min_score,
            "limit": limit,
            "language": language,
            "filters": filters,
        }
    )
    # hyde is accepted but never changes billing or behaviour here — the
    # national index never rewrites (clausly answers hyde: false). Sent only
    # when True so older deployments never see a param they don't know.
    if hyde:
        body["hyde"] = True
    if include_text:
        body["include"] = ["text"]
    return body


def _semantic_legislation_body(
    query: str,
    *,
    min_score: float | None,
    limit: int | None,
    language: str | None,
    include_text: bool,
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    body = drop_none(
        {
            "query": query,
            "min_score": min_score,
            "limit": limit,
            "language": language,
            "filters": filters,
        }
    )
    if include_text:
        body["include"] = ["text"]
    return body


class NationalResource:
    """Synchronous ``/national/*`` and ``/{jurisdiction}/*`` operations
    (``client.national``)."""

    def __init__(self, client: LexAPI) -> None:
        self._client = client

    def coverage(self) -> NationalCoverageResponse:
        """``GET /national/coverage`` — public, free. Which jurisdictions are
        live, their legislation / case-law coverage, semantic-search
        availability and the sources to attribute. Call this before any
        other national method to check a country has launched."""
        return self._client._request(
            "GET", "/national/coverage", cast=NationalCoverageResponse, free=True
        )

    def get_document(
        self, jurisdiction: str, id: str, *, language: str | None = None  # noqa: A002 - matches the API's own name
    ) -> NationalDocumentResponse:
        """``GET /{jurisdiction}/documents/{id}`` — one national document.

        5 credits upfront, settled to 1: national reads are always served
        from the corpus (no live fetch). 404 for an unknown id, or for a
        jurisdiction that does not exist or has not launched yet (see
        :meth:`coverage`). Below BUSINESS, counts against the monthly
        national-call allowance (:class:`~lexapi.errors.NationalAllowanceExhaustedError`
        on 402).
        """
        return self._client._request(
            "GET",
            f"/{_jur(jurisdiction)}/documents/{seg(id)}",
            params=drop_none({"language": language}),
            cast=NationalDocumentResponse,
        )

    def list_documents(
        self,
        jurisdiction: str,
        *,
        page: int | None = None,
        limit: int | None = None,
        q: str | None = None,
        document_type: str | None = None,
        include_repealed: bool | None = None,
    ) -> NationalDocumentListResponse:
        """``GET /{jurisdiction}/documents`` — a page of one jurisdiction's
        documents (title search ``q``, ``document_type`` filter, pagination).
        Repealed acts are excluded unless ``include_repealed=True``.

        3 credits flat per call (not per page). Below BUSINESS, counts
        against the monthly national-call allowance.
        """
        return self._client._request(
            "GET",
            f"/{_jur(jurisdiction)}/documents",
            params=_list_params(page, limit, q, document_type, include_repealed),
            cast=NationalDocumentListResponse,
        )

    def semantic_search(
        self,
        jurisdiction: str,
        query: str,
        *,
        hyde: bool = False,
        min_score: float | None = None,
        limit: int | None = None,
        language: str | None = None,
        include_text: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> NationalSemanticSearchResponse:
        """``POST /{jurisdiction}/search/semantic`` — national case-law
        semantic search. Same request shape as :meth:`LexAPI.semantic_search`,
        but ``filters`` only honours ``court``, ``date_from``, ``date_to``,
        ``ecli`` and ``language`` here (an unsupported key is a 400).

        5 credits flat, whatever ``hyde`` says: the national index never
        rewrites, so ``hyde=True`` never bills the 15-credit EU premium —
        the response reports ``hyde=False`` when requested. Below BUSINESS,
        counts against the monthly national-call allowance (checked, and
        validation runs, before any credit is charged).
        """
        return self._client._request(
            "POST",
            f"/{_jur(jurisdiction)}/search/semantic",
            json=_semantic_case_law_body(
                query, hyde=hyde, min_score=min_score, limit=limit, language=language,
                include_text=include_text, filters=filters,
            ),
            cast=NationalSemanticSearchResponse,
            read_only=True,
        )

    def legislation_semantic_search(
        self,
        jurisdiction: str,
        query: str,
        *,
        min_score: float | None = None,
        limit: int | None = None,
        language: str | None = None,
        include_text: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> NationalSemanticLegislationResponse:
        """``POST /{jurisdiction}/legislation/semantic`` — national
        legislation semantic search. ``filters`` honours ``id``/``law_id``,
        ``inForce``/``in_force`` and ``documentType``/``law_type``, plus
        ``language``. No ``hyde``, no ``as_of`` — both are EU-only.

        5 credits flat. Below BUSINESS, counts against the monthly
        national-call allowance.
        """
        return self._client._request(
            "POST",
            f"/{_jur(jurisdiction)}/legislation/semantic",
            json=_semantic_legislation_body(
                query, min_score=min_score, limit=limit, language=language,
                include_text=include_text, filters=filters,
            ),
            cast=NationalSemanticLegislationResponse,
            read_only=True,
        )


class AsyncNationalResource:
    """Asynchronous ``/national/*`` and ``/{jurisdiction}/*`` operations
    (``client.national``)."""

    def __init__(self, client: AsyncLexAPI) -> None:
        self._client = client

    async def coverage(self) -> NationalCoverageResponse:
        """``GET /national/coverage`` — see :meth:`NationalResource.coverage`."""
        return await self._client._request(
            "GET", "/national/coverage", cast=NationalCoverageResponse, free=True
        )

    async def get_document(
        self, jurisdiction: str, id: str, *, language: str | None = None  # noqa: A002
    ) -> NationalDocumentResponse:
        """``GET /{jurisdiction}/documents/{id}`` — see :meth:`NationalResource.get_document`."""
        return await self._client._request(
            "GET",
            f"/{_jur(jurisdiction)}/documents/{seg(id)}",
            params=drop_none({"language": language}),
            cast=NationalDocumentResponse,
        )

    async def list_documents(
        self,
        jurisdiction: str,
        *,
        page: int | None = None,
        limit: int | None = None,
        q: str | None = None,
        document_type: str | None = None,
        include_repealed: bool | None = None,
    ) -> NationalDocumentListResponse:
        """``GET /{jurisdiction}/documents`` — see :meth:`NationalResource.list_documents`."""
        return await self._client._request(
            "GET",
            f"/{_jur(jurisdiction)}/documents",
            params=_list_params(page, limit, q, document_type, include_repealed),
            cast=NationalDocumentListResponse,
        )

    async def semantic_search(
        self,
        jurisdiction: str,
        query: str,
        *,
        hyde: bool = False,
        min_score: float | None = None,
        limit: int | None = None,
        language: str | None = None,
        include_text: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> NationalSemanticSearchResponse:
        """``POST /{jurisdiction}/search/semantic`` —
        see :meth:`NationalResource.semantic_search`."""
        return await self._client._request(
            "POST",
            f"/{_jur(jurisdiction)}/search/semantic",
            json=_semantic_case_law_body(
                query, hyde=hyde, min_score=min_score, limit=limit, language=language,
                include_text=include_text, filters=filters,
            ),
            cast=NationalSemanticSearchResponse,
            read_only=True,
        )

    async def legislation_semantic_search(
        self,
        jurisdiction: str,
        query: str,
        *,
        min_score: float | None = None,
        limit: int | None = None,
        language: str | None = None,
        include_text: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> NationalSemanticLegislationResponse:
        """``POST /{jurisdiction}/legislation/semantic`` — see
        :meth:`NationalResource.legislation_semantic_search`."""
        return await self._client._request(
            "POST",
            f"/{_jur(jurisdiction)}/legislation/semantic",
            json=_semantic_legislation_body(
                query, min_score=min_score, limit=limit, language=language,
                include_text=include_text, filters=filters,
            ),
            cast=NationalSemanticLegislationResponse,
            read_only=True,
        )
