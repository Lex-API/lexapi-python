"""Citation-graph resource namespace: ``client.citations.*``."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Union

from lexapi._payloads import drop_none, seg
from lexapi.models import (
    CitationDocumentGroup,
    CitationExtractResponse,
    CitationNetworkResponse,
    CitationPathResponse,
    CitationStatsResponse,
    CitedByResponse,
    CitesResponse,
    CounterpartsResponse,
    RelatedDocumentsResponse,
    TranspositionResponse,
)

if TYPE_CHECKING:  # pragma: no cover
    from lexapi.async_client import AsyncLexAPI
    from lexapi.client import LexAPI


#: A filter that takes one value or several (sent comma-separated).
StrOrMany = Union[str, Sequence[str]]

_EDGE_PAGE_SIZE = 100


def _csv(name: str, value: StrOrMany | None) -> str | None:
    """One value, or a list/tuple joined with commas (the API's multi-value
    form). An empty list sends no filter at all, i.e. the server default
    (every value), not "match nothing"."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        items = list(value)
        for item in items:
            if not isinstance(item, str):
                raise TypeError(f"{name} values must be strings, got {type(item).__name__}")
        return ",".join(items) if items else None
    raise TypeError(f"{name} must be a string or a list of strings, got {type(value).__name__}")


def _graph_params(
    *,
    citation_type: StrOrMany | None = None,
    provenance: StrOrMany | None = None,
    jurisdictions: StrOrMany | None = None,
    resolution: StrOrMany | None = None,
    include_national: bool | None = None,
    language: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    unresolved: bool | None = None,
    max_depth: int | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "citationType": _csv("citation_type", citation_type),
            "provenance": _csv("provenance", provenance),
            "jurisdictions": _csv("jurisdictions", jurisdictions),
            "resolution": _csv("resolution", resolution),
            "includeNational": include_national,
            "language": language,
            "limit": limit,
            "offset": offset,
            "unresolved": unresolved,
            "maxDepth": max_depth,
        }
    )


def _page_done(rows: int, page_size: int, offset: int, total_edges: int | None) -> bool:
    """Edge pages are full until the last one (paging is over edge rows);
    ``total_edges``, when sent, ends the walk without an extra empty page."""
    if rows == 0 or rows < page_size:
        return True
    return total_edges is not None and offset >= total_edges


class CitationsResource:
    """Synchronous ``/citations/*`` operations (``client.citations``).

    The edge reads (``cited_by``, ``cites``, ``network``, ``path``,
    ``related``) share these filters, each one value or a list (sent
    comma-separated):

    - ``citation_type`` — :data:`~lexapi.CITATION_TYPES` (e.g.
      ``["amendment", "repeal"]``);
    - ``provenance`` — :data:`~lexapi.CITATION_PROVENANCES`;
    - ``jurisdictions`` — ``eu`` and the lower-case codes of the national
      jurisdictions open to you (``["eu", "at"]``); a national code asks
      for national documents like ``include_national=True``, with the same
      plan gate;
    - ``resolution`` — :data:`~lexapi.CITATION_RESOLUTIONS` (national edges
      only; e.g. ``["id", "key"]`` leaves out abbreviation matches).

    An empty list sends no filter (the server default). An unknown value
    raises :class:`~lexapi.InvalidParamsError` (refunded)
    with ``param`` and ``allowed``. ``language`` picks the language of the
    titles filled in from the corpus; it does not filter edges.
    """

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
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include_national: bool | None = None,
        language: str | None = None,
    ) -> CitedByResponse:
        """GET /citations/cited-by/{celex} — inbound edges (documents citing this one).

        Paginated in edge rows via ``limit`` (default 100) / ``offset``:
        page until ``offset + limit >= total_edges``. ``total_citations``
        counts distinct document pairs, ``unique_documents`` only this page;
        ``counts_source`` says where the totals were read from. For
        legislation, amendment / corrigendum / repeal edges are refreshed
        from Cellar when older than 7 days (waits at most ~2.5 s; see
        ``response.relation_sync``). 1 credit. Filters: see the class
        docstring.

        ``celex`` may be a national document id (e.g. ``AT-NOR-10001597``).
        ``include_national=True`` also returns national neighbours of live
        jurisdictions — BUSINESS, SCALE and ENTERPRISE only
        (:class:`~lexapi.TierForbiddenError` below that, nothing charged). A
        national id whose jurisdiction is not live raises
        :class:`~lexapi.NotFoundError`.
        """
        return self._client._request(
            "GET",
            f"/citations/cited-by/{seg(celex)}",
            params=_graph_params(
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                limit=limit,
                offset=offset,
                include_national=include_national,
                language=language,
            ),
            cast=CitedByResponse,
        )

    def cites(
        self,
        celex: str,
        *,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include_national: bool | None = None,
        unresolved: bool | None = None,
        language: str | None = None,
    ) -> CitesResponse:
        """GET /citations/cites/{celex} — outbound edges (documents this one cites).

        A document must have been crawled via :meth:`extract` to appear here.
        1 credit. ``celex`` may be a national document id;
        ``include_national`` also returns national neighbours — see
        :meth:`cited_by`. ``unresolved=True`` on a national document id adds
        ``response.unresolved`` (the references that could not be turned
        into an edge, at most 500) at no extra cost; ignored on an EU id.
        """
        return self._client._request(
            "GET",
            f"/citations/cites/{seg(celex)}",
            params=_graph_params(
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                limit=limit,
                offset=offset,
                include_national=include_national,
                unresolved=unresolved,
                language=language,
            ),
            cast=CitesResponse,
        )

    def iter_cited_by(
        self,
        celex: str,
        *,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        include_national: bool | None = None,
        language: str | None = None,
        page_size: int = _EDGE_PAGE_SIZE,
    ) -> Iterator[CitationDocumentGroup]:
        """Walk every inbound edge page of :meth:`cited_by`, yielding each
        page's document groups.

        Paging is in edge rows, so a document whose edges straddle a page
        boundary is yielded once per page it appears in (merge on ``id`` if
        you need one entry per document). Edges extracted while you iterate
        can shift between pages. Each page is a separate billed call (1
        credit); the walk stops at the first short page or once ``offset``
        reaches ``total_edges``.
        """
        offset = 0
        while True:
            page = self.cited_by(
                celex,
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                include_national=include_national,
                language=language,
                limit=page_size,
                offset=offset,
            )
            yield from page.cited_by
            rows = page.edge_count
            offset += rows
            if _page_done(rows, page_size, offset, page.total_edges):
                return

    def iter_cites(
        self,
        celex: str,
        *,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        include_national: bool | None = None,
        language: str | None = None,
        page_size: int = _EDGE_PAGE_SIZE,
    ) -> Iterator[CitationDocumentGroup]:
        """Walk every outbound edge page of :meth:`cites` (see
        :meth:`iter_cited_by` for the paging caveat). 1 credit per page.
        Does not take ``unresolved``; call :meth:`cites` for that."""
        offset = 0
        while True:
            page = self.cites(
                celex,
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                include_national=include_national,
                language=language,
                limit=page_size,
                offset=offset,
            )
            yield from page.cites
            rows = page.edge_count
            offset += rows
            if _page_done(rows, page_size, offset, page.total_edges):
                return

    def network(
        self,
        celex: str,
        *,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include_national: bool | None = None,
        unresolved: bool | None = None,
        language: str | None = None,
    ) -> CitationNetworkResponse:
        """GET /citations/network/{celex} — both directions in one call.

        ``limit`` applies per direction (max 500). Each neighbour carries
        ``edge_details`` (the distinct type / provenance / resolution of
        its edges). On server-side budget expiry the response is still 200
        with ``partial=True`` and empty arrays (refunded) — always check
        ``response.partial``. 20 credits. ``celex`` may be a national
        document id; ``include_national`` also includes national neighbours
        — see :meth:`cited_by`; ``unresolved`` — see :meth:`cites`.
        """
        return self._client._request(
            "GET",
            f"/citations/network/{seg(celex)}",
            params=_graph_params(
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                limit=limit,
                offset=offset,
                include_national=include_national,
                unresolved=unresolved,
                language=language,
            ),
            cast=CitationNetworkResponse,
        )

    def path(
        self,
        from_celex: str,
        to_celex: str,
        *,
        max_depth: int | None = None,
        include_national: bool | None = None,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        language: str | None = None,
    ) -> CitationPathResponse:
        """GET /citations/path/{from}/{to} — shortest citation path (BFS).

        No path within the budget is a graceful ``found=False`` response
        (HTTP 200), not an error. ``max_depth`` defaults to 3 (max 8).
        20 credits. ``from_celex``/``to_celex`` may be national document ids;
        ``include_national`` also follows national documents along the way —
        see :meth:`cited_by`.
        """
        return self._client._request(
            "GET",
            f"/citations/path/{seg(from_celex)}/{seg(to_celex)}",
            params=_graph_params(
                max_depth=max_depth,
                include_national=include_national,
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                language=language,
            ),
            cast=CitationPathResponse,
        )

    def related(
        self,
        celex: str,
        *,
        limit: int | None = None,
        include_national: bool | None = None,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        language: str | None = None,
    ) -> RelatedDocumentsResponse:
        """GET /citations/related/{celex} — bibliographic-coupling neighbours.

        Seeds with no outbound citations return an empty list plus a
        ``message`` (graceful, not an error). ``limit`` defaults to 10 (max
        50). 20 credits. ``celex`` may be a national document id;
        ``include_national`` also considers national documents — see
        :meth:`cited_by`.
        """
        return self._client._request(
            "GET",
            f"/citations/related/{seg(celex)}",
            params=_graph_params(
                limit=limit,
                include_national=include_national,
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                language=language,
            ),
            cast=RelatedDocumentsResponse,
        )

    def stats(self, *, include_national: bool | None = None) -> CitationStatsResponse:
        """GET /citations/stats — global citation-graph counters and top-10s
        (20 credits). ``include_national`` also counts national documents of
        live jurisdictions and adds ``by_jurisdiction``, ``by_provenance``
        and ``transposition`` — BUSINESS, SCALE and ENTERPRISE only
        (:class:`~lexapi.TierForbiddenError` below that).
        """
        return self._client._request(
            "GET",
            "/citations/stats",
            params=drop_none({"includeNational": include_national}),
            cast=CitationStatsResponse,
        )

    def transposition(
        self,
        celex: str,
        *,
        jurisdictions: StrOrMany | None = None,
        resolved_only: bool | None = None,
        language: str | None = None,
    ) -> TranspositionResponse:
        """GET /citations/transposition/{celex} — the measures the member
        states notified to the Commission for an EU act (CELEX sector 3),
        grouped by country. 5 credits, every plan.

        ``jurisdictions`` narrows the countries (ISO-2 codes, ``EL`` for
        Greece, ``UK`` for the United Kingdom; default all 28). At most
        2,000 measures per call (``response.truncated``).

        On BUSINESS, SCALE and ENTERPRISE, for live jurisdictions, the
        answer also says which measures LexAPI holds (``id``, ``resolved``,
        ``resolution``, ``resolution_basis``) and adds the documents whose
        national source records the relation (``provenance`` ``source``);
        ``resolved_only=True`` keeps only the held ones. Below BUSINESS,
        for a jurisdiction that is not live, and while the national-source
        terms are not accepted (``response.terms_required``) those fields
        are ``None``.

        A notification is not a finding that the measure transposes the
        act, or transposes it completely — show ``response.note`` with the
        measures. A CELEX that is not an EU act raises
        :class:`~lexapi.NotFoundError`.
        """
        return self._client._request(
            "GET",
            f"/citations/transposition/{seg(celex)}",
            params=drop_none(
                {
                    "jurisdictions": _csv("jurisdictions", jurisdictions),
                    "resolvedOnly": resolved_only,
                    "language": language,
                }
            ),
            cast=TranspositionResponse,
        )

    def counterparts(
        self,
        id: str,
        *,
        jurisdictions: StrOrMany | None = None,
        language: str | None = None,
    ) -> CounterpartsResponse:
        """GET /citations/counterparts/{id} — a national document's
        counterparts in the other live jurisdictions, through the EU acts it
        transposes or implements. An EU act CELEX (sector 3) as ``id``
        returns its measures grouped the same way.

        20 credits; BUSINESS, SCALE and ENTERPRISE only
        (:class:`~lexapi.TierForbiddenError` below that, refunded). A lookup
        past its 20-second server-side budget raises
        :class:`~lexapi.TimeoutError` (503, ``partial=True``, refunded). The
        SDK does not retry it: re-running waits the full budget again —
        narrow it with ``jurisdictions`` instead.
        """
        return self._client._request(
            "GET",
            f"/citations/counterparts/{seg(id)}",
            params=drop_none(
                {
                    "jurisdictions": _csv("jurisdictions", jurisdictions),
                    "language": language,
                }
            ),
            cast=CounterpartsResponse,
        )


class AsyncCitationsResource:
    """Asynchronous ``/citations/*`` operations (``client.citations``).
    See :class:`CitationsResource` for the shared filters."""

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
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include_national: bool | None = None,
        language: str | None = None,
    ) -> CitedByResponse:
        """GET /citations/cited-by/{celex} — see :meth:`CitationsResource.cited_by`."""
        return await self._client._request(
            "GET",
            f"/citations/cited-by/{seg(celex)}",
            params=_graph_params(
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                limit=limit,
                offset=offset,
                include_national=include_national,
                language=language,
            ),
            cast=CitedByResponse,
        )

    async def cites(
        self,
        celex: str,
        *,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include_national: bool | None = None,
        unresolved: bool | None = None,
        language: str | None = None,
    ) -> CitesResponse:
        """GET /citations/cites/{celex} — see :meth:`CitationsResource.cites`."""
        return await self._client._request(
            "GET",
            f"/citations/cites/{seg(celex)}",
            params=_graph_params(
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                limit=limit,
                offset=offset,
                include_national=include_national,
                unresolved=unresolved,
                language=language,
            ),
            cast=CitesResponse,
        )

    async def iter_cited_by(
        self,
        celex: str,
        *,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        include_national: bool | None = None,
        language: str | None = None,
        page_size: int = _EDGE_PAGE_SIZE,
    ) -> AsyncIterator[CitationDocumentGroup]:
        """Async twin of :meth:`CitationsResource.iter_cited_by`."""
        offset = 0
        while True:
            page = await self.cited_by(
                celex,
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                include_national=include_national,
                language=language,
                limit=page_size,
                offset=offset,
            )
            for group in page.cited_by:
                yield group
            rows = page.edge_count
            offset += rows
            if _page_done(rows, page_size, offset, page.total_edges):
                return

    async def iter_cites(
        self,
        celex: str,
        *,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        include_national: bool | None = None,
        language: str | None = None,
        page_size: int = _EDGE_PAGE_SIZE,
    ) -> AsyncIterator[CitationDocumentGroup]:
        """Async twin of :meth:`CitationsResource.iter_cites`."""
        offset = 0
        while True:
            page = await self.cites(
                celex,
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                include_national=include_national,
                language=language,
                limit=page_size,
                offset=offset,
            )
            for group in page.cites:
                yield group
            rows = page.edge_count
            offset += rows
            if _page_done(rows, page_size, offset, page.total_edges):
                return

    async def network(
        self,
        celex: str,
        *,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include_national: bool | None = None,
        unresolved: bool | None = None,
        language: str | None = None,
    ) -> CitationNetworkResponse:
        """GET /citations/network/{celex} — see :meth:`CitationsResource.network`."""
        return await self._client._request(
            "GET",
            f"/citations/network/{seg(celex)}",
            params=_graph_params(
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                limit=limit,
                offset=offset,
                include_national=include_national,
                unresolved=unresolved,
                language=language,
            ),
            cast=CitationNetworkResponse,
        )

    async def path(
        self,
        from_celex: str,
        to_celex: str,
        *,
        max_depth: int | None = None,
        include_national: bool | None = None,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        language: str | None = None,
    ) -> CitationPathResponse:
        """GET /citations/path/{from}/{to} — see :meth:`CitationsResource.path`."""
        return await self._client._request(
            "GET",
            f"/citations/path/{seg(from_celex)}/{seg(to_celex)}",
            params=_graph_params(
                max_depth=max_depth,
                include_national=include_national,
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                language=language,
            ),
            cast=CitationPathResponse,
        )

    async def related(
        self,
        celex: str,
        *,
        limit: int | None = None,
        include_national: bool | None = None,
        citation_type: StrOrMany | None = None,
        provenance: StrOrMany | None = None,
        jurisdictions: StrOrMany | None = None,
        resolution: StrOrMany | None = None,
        language: str | None = None,
    ) -> RelatedDocumentsResponse:
        """GET /citations/related/{celex} — see :meth:`CitationsResource.related`."""
        return await self._client._request(
            "GET",
            f"/citations/related/{seg(celex)}",
            params=_graph_params(
                limit=limit,
                include_national=include_national,
                citation_type=citation_type,
                provenance=provenance,
                jurisdictions=jurisdictions,
                resolution=resolution,
                language=language,
            ),
            cast=RelatedDocumentsResponse,
        )

    async def stats(self, *, include_national: bool | None = None) -> CitationStatsResponse:
        """GET /citations/stats — see :meth:`CitationsResource.stats`."""
        return await self._client._request(
            "GET",
            "/citations/stats",
            params=drop_none({"includeNational": include_national}),
            cast=CitationStatsResponse,
        )

    async def transposition(
        self,
        celex: str,
        *,
        jurisdictions: StrOrMany | None = None,
        resolved_only: bool | None = None,
        language: str | None = None,
    ) -> TranspositionResponse:
        """GET /citations/transposition/{celex} — see
        :meth:`CitationsResource.transposition`."""
        return await self._client._request(
            "GET",
            f"/citations/transposition/{seg(celex)}",
            params=drop_none(
                {
                    "jurisdictions": _csv("jurisdictions", jurisdictions),
                    "resolvedOnly": resolved_only,
                    "language": language,
                }
            ),
            cast=TranspositionResponse,
        )

    async def counterparts(
        self,
        id: str,
        *,
        jurisdictions: StrOrMany | None = None,
        language: str | None = None,
    ) -> CounterpartsResponse:
        """GET /citations/counterparts/{id} — see
        :meth:`CitationsResource.counterparts`."""
        return await self._client._request(
            "GET",
            f"/citations/counterparts/{seg(id)}",
            params=drop_none(
                {
                    "jurisdictions": _csv("jurisdictions", jurisdictions),
                    "language": language,
                }
            ),
            cast=CounterpartsResponse,
        )
