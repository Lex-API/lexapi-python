"""Citation-graph resource namespace: ``client.citations.*``."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

from lexapi._payloads import drop_none, seg
from lexapi.models import (
    CitationDocumentGroup,
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


_EDGE_PAGE_SIZE = 100


def _edge_params(
    citation_type: str | None, limit: int | None, offset: int | None
) -> dict:
    return drop_none({"citationType": citation_type, "limit": limit, "offset": offset})


def _page_done(rows: int, page_size: int) -> bool:
    """Edge pages are full until the last one (paging is over edge rows)."""
    return rows == 0 or rows < page_size


class CitationsResource:
    """Synchronous ``/citations/*`` operations (``client.citations``)."""

    def __init__(self, client: LexAPI) -> None:
        self._client = client

    def extract(self, celex: str) -> CitationExtractResponse:
        """POST /citations/extract — crawl a document's metadata page and
        persist its citation edges.

        Idempotent: if already extracted, returns immediately with
        ``already_extracted=True`` and an empty ``citations`` list. 5 credits,
        settled at 1 on that short-circuit.
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

        Paginated in edges via ``limit`` (default 100) / ``offset``;
        ``total_citations`` counts all pages, ``unique_documents`` only this
        one. For legislation, amendment / corrigendum / repeal edges are
        refreshed from Cellar when older than 7 days (waits at most ~2.5 s;
        see ``response.relation_sync``). 1 credit. ``citation_type``:
        ``reference``, ``amendment``, ``repeal``, ``corrigendum``,
        ``implementation``, ``legal-basis``, ``proposal``.
        """
        return self._client._request(
            "GET",
            f"/citations/cited-by/{seg(celex)}",
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
        1 credit.
        """
        return self._client._request(
            "GET",
            f"/citations/cites/{seg(celex)}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitesResponse,
        )

    def iter_cited_by(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        page_size: int = _EDGE_PAGE_SIZE,
    ) -> Iterator[CitationDocumentGroup]:
        """Walk every inbound edge page of :meth:`cited_by`, yielding each
        page's document groups.

        Paging is in edges, so a document whose edges straddle a page
        boundary is yielded once per page it appears in (merge on
        ``celex_number`` if you need one entry per document). Each page is a
        separate billed call (1 credit).
        """
        offset = 0
        while True:
            page = self.cited_by(
                celex, citation_type=citation_type, limit=page_size, offset=offset
            )
            yield from page.cited_by
            rows = page.edge_count
            offset += rows
            if _page_done(rows, page_size):
                return

    def iter_cites(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        page_size: int = _EDGE_PAGE_SIZE,
    ) -> Iterator[CitationDocumentGroup]:
        """Walk every outbound edge page of :meth:`cites` (see
        :meth:`iter_cited_by` for the paging caveat). 1 credit per page."""
        offset = 0
        while True:
            page = self.cites(celex, citation_type=citation_type, limit=page_size, offset=offset)
            yield from page.cites
            rows = page.edge_count
            offset += rows
            if _page_done(rows, page_size):
                return

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
        arrays — always check ``response.partial``. 20 credits.
        """
        return self._client._request(
            "GET",
            f"/citations/network/{seg(celex)}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitationNetworkResponse,
        )

    def path(
        self, from_celex: str, to_celex: str, *, max_depth: int | None = None
    ) -> CitationPathResponse:
        """GET /citations/path/{from}/{to} — shortest citation path (BFS).

        No path within the budget is a graceful ``found=False`` response
        (HTTP 200), not an error. ``max_depth`` defaults to 3 (max 8).
        20 credits.
        """
        return self._client._request(
            "GET",
            f"/citations/path/{seg(from_celex)}/{seg(to_celex)}",
            params=drop_none({"maxDepth": max_depth}),
            cast=CitationPathResponse,
        )

    def related(self, celex: str, *, limit: int | None = None) -> RelatedDocumentsResponse:
        """GET /citations/related/{celex} — bibliographic-coupling neighbours.

        Seeds with no outbound citations return an empty list plus a
        ``message`` (graceful, not an error). ``limit`` defaults to 10 (max
        50). 20 credits.
        """
        return self._client._request(
            "GET",
            f"/citations/related/{seg(celex)}",
            params=drop_none({"limit": limit}),
            cast=RelatedDocumentsResponse,
        )

    def stats(self) -> CitationStatsResponse:
        """GET /citations/stats — global citation-graph counters and top-10s (20 credits)."""
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
            f"/citations/cited-by/{seg(celex)}",
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
            f"/citations/cites/{seg(celex)}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitesResponse,
        )

    async def iter_cited_by(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        page_size: int = _EDGE_PAGE_SIZE,
    ) -> AsyncIterator[CitationDocumentGroup]:
        """Async twin of :meth:`CitationsResource.iter_cited_by`."""
        offset = 0
        while True:
            page = await self.cited_by(
                celex, citation_type=citation_type, limit=page_size, offset=offset
            )
            for group in page.cited_by:
                yield group
            rows = page.edge_count
            offset += rows
            if _page_done(rows, page_size):
                return

    async def iter_cites(
        self,
        celex: str,
        *,
        citation_type: str | None = None,
        page_size: int = _EDGE_PAGE_SIZE,
    ) -> AsyncIterator[CitationDocumentGroup]:
        """Async twin of :meth:`CitationsResource.iter_cites`."""
        offset = 0
        while True:
            page = await self.cites(
                celex, citation_type=citation_type, limit=page_size, offset=offset
            )
            for group in page.cites:
                yield group
            rows = page.edge_count
            offset += rows
            if _page_done(rows, page_size):
                return

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
            f"/citations/network/{seg(celex)}",
            params=_edge_params(citation_type, limit, offset),
            cast=CitationNetworkResponse,
        )

    async def path(
        self, from_celex: str, to_celex: str, *, max_depth: int | None = None
    ) -> CitationPathResponse:
        """GET /citations/path/{from}/{to} — see :meth:`CitationsResource.path`."""
        return await self._client._request(
            "GET",
            f"/citations/path/{seg(from_celex)}/{seg(to_celex)}",
            params=drop_none({"maxDepth": max_depth}),
            cast=CitationPathResponse,
        )

    async def related(
        self, celex: str, *, limit: int | None = None
    ) -> RelatedDocumentsResponse:
        """GET /citations/related/{celex} — see :meth:`CitationsResource.related`."""
        return await self._client._request(
            "GET",
            f"/citations/related/{seg(celex)}",
            params=drop_none({"limit": limit}),
            cast=RelatedDocumentsResponse,
        )

    async def stats(self) -> CitationStatsResponse:
        """GET /citations/stats — see :meth:`CitationsResource.stats`."""
        return await self._client._request(
            "GET", "/citations/stats", cast=CitationStatsResponse
        )
