"""Citation-graph resource namespace: ``client.citations.*``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lexapi._payloads import drop_none
from lexapi.models import (
    CitationExtractResponse,
    CitationNetworkResponse,
    CitationPathResponse,
    CitationStatsResponse,
    CitedByResponse,
    CitesResponse,
    RelatedDocumentsResponse,
)

if TYPE_CHECKING:  # pragma: no cover
    from lexapi.async_client import AsyncLexAPI
    from lexapi.client import LexAPI


def _edge_params(
    citation_type: str | None, limit: int | None, offset: int | None
) -> dict:
    return drop_none({"citationType": citation_type, "limit": limit, "offset": offset})


class CitationsResource:
    """Synchronous ``/citations/*`` operations (``client.citations``)."""

    def __init__(self, client: LexAPI) -> None:
        self._client = client

    def extract(self, celex: str) -> CitationExtractResponse:
        """POST /citations/extract — crawl a document's metadata page and
        persist its citation edges.

        Idempotent: if already extracted, returns immediately with
        ``already_extracted=True`` and an empty ``citations`` list.
        """
        return self._client._request(
            "POST",
            "/citations/extract",
            json={"celexNumber": celex},
            cast=CitationExtractResponse,
        )

    def cited_by(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> CitedByResponse:
        """GET /citations/cited-by/{celex} — inbound edges (documents citing this one).

        Paginated in edges via ``limit``/``offset``; ``total_citations``
        counts all pages.
        """
        return self._client._request(
            "GET",
            f"/citations/cited-by/{celex}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitedByResponse,
        )

    def cites(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> CitesResponse:
        """GET /citations/cites/{celex} — outbound edges (documents this one cites).

        A document must have been crawled via :meth:`extract` to appear here.
        """
        return self._client._request(
            "GET",
            f"/citations/cites/{celex}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitesResponse,
        )

    def network(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> CitationNetworkResponse:
        """GET /citations/network/{celex} — both directions in one call.

        ``limit`` applies per direction (max 500). On server-side budget
        expiry the response is still 200 with ``partial=True`` and empty
        arrays — always check ``response.partial``.
        """
        return self._client._request(
            "GET",
            f"/citations/network/{celex}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitationNetworkResponse,
        )

    def path(
        self, from_celex: str, to_celex: str, *, max_depth: int | None = None
    ) -> CitationPathResponse:
        """GET /citations/path/{from}/{to} — shortest citation path (BFS).

        No path within the budget is a graceful ``found=False`` response
        (HTTP 200), not an error. ``max_depth`` defaults to 3 (max 8).
        """
        return self._client._request(
            "GET",
            f"/citations/path/{from_celex}/{to_celex}",
            params=drop_none({"maxDepth": max_depth}),
            cast=CitationPathResponse,
        )

    def related(self, celex: str, *, limit: int | None = None) -> RelatedDocumentsResponse:
        """GET /citations/related/{celex} — bibliographic-coupling neighbours.

        Seeds with no outbound citations return an empty list plus a
        ``message`` (graceful, not an error).
        """
        return self._client._request(
            "GET",
            f"/citations/related/{celex}",
            params=drop_none({"limit": limit}),
            cast=RelatedDocumentsResponse,
        )

    def stats(self) -> CitationStatsResponse:
        """GET /citations/stats — global citation-graph counters and top-10s."""
        return self._client._request("GET", "/citations/stats", cast=CitationStatsResponse)


class AsyncCitationsResource:
    """Asynchronous ``/citations/*`` operations (``client.citations``)."""

    def __init__(self, client: AsyncLexAPI) -> None:
        self._client = client

    async def extract(self, celex: str) -> CitationExtractResponse:
        """POST /citations/extract — see :meth:`CitationsResource.extract`."""
        return await self._client._request(
            "POST",
            "/citations/extract",
            json={"celexNumber": celex},
            cast=CitationExtractResponse,
        )

    async def cited_by(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> CitedByResponse:
        """GET /citations/cited-by/{celex} — see :meth:`CitationsResource.cited_by`."""
        return await self._client._request(
            "GET",
            f"/citations/cited-by/{celex}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitedByResponse,
        )

    async def cites(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> CitesResponse:
        """GET /citations/cites/{celex} — see :meth:`CitationsResource.cites`."""
        return await self._client._request(
            "GET",
            f"/citations/cites/{celex}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitesResponse,
        )

    async def network(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> CitationNetworkResponse:
        """GET /citations/network/{celex} — see :meth:`CitationsResource.network`."""
        return await self._client._request(
            "GET",
            f"/citations/network/{celex}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitationNetworkResponse,
        )

    async def path(
        self, from_celex: str, to_celex: str, *, max_depth: int | None = None
    ) -> CitationPathResponse:
        """GET /citations/path/{from}/{to} — see :meth:`CitationsResource.path`."""
        return await self._client._request(
            "GET",
            f"/citations/path/{from_celex}/{to_celex}",
            params=drop_none({"maxDepth": max_depth}),
            cast=CitationPathResponse,
        )

    async def related(
        self, celex: str, *, limit: int | None = None
    ) -> RelatedDocumentsResponse:
        """GET /citations/related/{celex} — see :meth:`CitationsResource.related`."""
        return await self._client._request(
            "GET",
            f"/citations/related/{celex}",
            params=drop_none({"limit": limit}),
            cast=RelatedDocumentsResponse,
        )

    async def stats(self) -> CitationStatsResponse:
        """GET /citations/stats — see :meth:`CitationsResource.stats`."""
        return await self._client._request(
            "GET", "/citations/stats", cast=CitationStatsResponse
        )
