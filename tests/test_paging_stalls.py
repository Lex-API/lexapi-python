"""Page iterators: a walk ends as before, never because of what a page
contains, and raises InvalidResponseError when the server does not advance.

Each scenario server keeps its own state and answers like the API (or like a
server that ignores ``offset`` / repeats a cursor). ``reference_*`` replay
the stop rules of the previous release, so the tests can assert that a
changing collection still returns everything those rules returned.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx
import pytest

from lexapi import AsyncLexAPI, InvalidResponseError, LexAPI

LOOPBACK = "http://127.0.0.1:9/api/v1"
MODES = ["sync", "async"]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


class Server:
    """Counts requests and refuses to go on past ``max_requests``."""

    def __init__(self, max_requests: int = 10_000) -> None:
        self.requests: list[httpx.Request] = []
        self.max_requests = max_requests

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        self.requests.append(request)
        if len(self.requests) > self.max_requests:
            raise RuntimeError("runaway walk")
        return httpx.Response(200, json=self.answer(request))

    def answer(self, request: httpx.Request) -> dict[str, Any]:
        raise NotImplementedError

    def params(self) -> list[tuple[str | None, str | None]]:
        return [(r.url.params.get("offset"), r.url.params.get("cursor")) for r in self.requests]


def walk(mode: str, server: Server, start: Callable[[Any], Any]) -> list[Any]:
    """Drain an iterator fully; returns the items (raises what it raises)."""
    transport = httpx.MockTransport(server)
    if mode == "sync":
        with LexAPI("lex_test_key", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
            return list(start(c))

    async def go():
        async with AsyncLexAPI(
            "lex_test_key", base_url=LOOPBACK, transport=transport, max_retries=0
        ) as c:
            return [item async for item in start(c)]

    return asyncio.run(asyncio.wait_for(go(), 30))


def walk_until_error(mode, server, start):
    """Items yielded before the walk raised, and the error."""
    items: list[Any] = []
    transport = httpx.MockTransport(server)
    if mode == "sync":
        with LexAPI("lex_test_key", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
            with pytest.raises(InvalidResponseError) as info:
                for item in start(c):
                    items.append(item)
        return items, info.value

    async def go():
        async with AsyncLexAPI(
            "lex_test_key", base_url=LOOPBACK, transport=transport, max_retries=0
        ) as c:
            async for item in start(c):
                items.append(item)

    with pytest.raises(InvalidResponseError) as info:
        asyncio.run(asyncio.wait_for(go(), 30))
    return items, info.value


# ── webhook deliveries ───────────────────────────────────────────────────


class DeliveryServer(Server):
    """Deliveries newest first; ``bursts`` maps a request number (1-based)
    to how many new deliveries arrive just before it."""

    def __init__(self, count, *, send_total=True, bursts=None, ignore_offset=False, **kw):
        super().__init__(**kw)
        self.items = [f"d{n}" for n in range(count, 0, -1)]
        self.next_id = count + 1
        self.send_total = send_total
        self.bursts = bursts or {}
        self.ignore_offset = ignore_offset

    def answer(self, request):
        for _ in range(self.bursts.get(len(self.requests), 0)):
            self.items.insert(0, f"d{self.next_id}")
            self.next_id += 1
        offset = 0 if self.ignore_offset else int(request.url.params.get("offset", 0))
        limit = int(request.url.params["limit"])
        page = self.items[offset : offset + limit]
        body: dict[str, Any] = {
            "success": True,
            "deliveries": [{"id": i, "status": "delivered"} for i in page],
        }
        if self.send_total:
            body["total"] = len(self.items)
        return body


def reference_deliveries(server_factory, page_size):
    """The previous release's walk over a fresh server: ids yielded."""
    server = server_factory()
    seen: set[str] = set()
    out: list[str] = []
    offset = 0
    request = type("R", (), {})
    while True:
        server.requests.append(request)
        fake = httpx.Request("GET", f"{LOOPBACK}/x?limit={page_size}&offset={offset}")
        body = server.answer(fake)
        deliveries = body["deliveries"]
        for d in deliveries:
            if d["id"] in seen:
                continue
            seen.add(d["id"])
            out.append(d["id"])
        offset += len(deliveries)
        total = body.get("total")
        if not deliveries or (total is not None and offset >= total):
            return out, len(server.requests)


def deliveries_walk(page_size):
    return lambda c: c.webhooks.iter_deliveries("wh_1", page_size=page_size)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("page_size", [2, 10, 50, 100])
@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.parametrize("send_total", [True, False])
@pytest.mark.parametrize("bursts", [{2: 1}, {2: 1, 3: 1}])
def test_delivery_bursts_return_everything_the_previous_release_did(
    mode, page_size, extra, send_total, bursts
):
    size = page_size + extra
    plan = {k: size for k in bursts}

    def factory():
        return DeliveryServer(page_size * 6, send_total=send_total, bursts=dict(plan))

    expected, expected_requests = reference_deliveries(factory, page_size)
    server = factory()
    got = [d.id for d in walk(mode, server, deliveries_walk(page_size))]
    assert got == expected
    assert len(server.requests) == expected_requests


@pytest.mark.parametrize("mode", MODES)
def test_delivery_server_ignoring_offset_without_total_raises_on_the_third_stall(mode):
    server = DeliveryServer(4, send_total=False, ignore_offset=True)
    items, err = walk_until_error(mode, server, deliveries_walk(2))
    assert [d.id for d in items] == ["d4", "d3"]
    assert len(server.requests) == 4  # 1 page + 3 stalled pages
    assert "Pagination stopped" in str(err)
    assert [d["id"] for d in err.body["deliveries"]] == ["d4", "d3"]


@pytest.mark.parametrize("mode", MODES)
def test_delivery_server_ignoring_offset_with_total_ends_by_total(mode):
    server = DeliveryServer(10, send_total=True, ignore_offset=True)
    items = walk(mode, server, deliveries_walk(2))
    assert [d.id for d in items] == ["d10", "d9"]
    assert len(server.requests) == 5  # offset reaches total = 10; repeats never count


@pytest.mark.parametrize("mode", MODES)
def test_delivery_pages_without_readable_ids_never_count(mode):
    class NoIds(Server):
        def answer(self, request):
            n = len(self.requests)
            if n > 6:
                return {"success": True, "deliveries": []}
            return {"success": True, "deliveries": [{"id": 5}, {"status": "x"}]}

    server = NoIds()
    items = walk(mode, server, deliveries_walk(2))
    assert len(items) == 12  # every delivery without a readable id is yielded
    assert len(server.requests) == 7


@pytest.mark.parametrize("mode", MODES)
def test_a_page_with_unseen_ids_resets_the_stall_count(mode):
    pages = [["a", "b"], ["a", "b"], ["a", "b"], ["c"], ["a", "c"], ["a"], ["c"], []]

    class Pages(Server):
        def answer(self, request):
            ids = pages[len(self.requests) - 1]
            return {"success": True, "deliveries": [{"id": i} for i in ids]}

    server = Pages()
    items, err = walk_until_error(mode, server, deliveries_walk(2))
    assert [d.id for d in items] == ["a", "b", "c"]
    assert len(server.requests) == 7  # stalls: pages 2, 3 | 5, 6, 7 -> raise on 7


@pytest.mark.parametrize("mode", MODES)
def test_well_formed_delivery_walk_of_10k(mode):
    server = DeliveryServer(10_000)
    items = walk(mode, server, deliveries_walk(100))
    assert len(items) == 10_000
    assert len(server.requests) == 100


# ── citations ────────────────────────────────────────────────────────────


class EdgeServer(Server):
    """Edge rows grouped per page by citing document, like the API."""

    def __init__(self, edges, *, signals=("totalEdges", "offset"), ignore_offset=False, **kw):
        super().__init__(**kw)
        self.edges = edges
        self.signals = signals
        self.ignore_offset = ignore_offset
        self.key = "citedBy"

    def answer(self, request):
        offset = 0 if self.ignore_offset else int(request.url.params.get("offset", 0))
        limit = int(request.url.params["limit"])
        rows = self.edges[offset : offset + limit]
        groups: dict[str, dict[str, Any]] = {}
        for celex, kind in rows:
            group = groups.setdefault(
                celex, {"celexNumber": celex, "citationCount": 0, "citations": []}
            )
            group["citationCount"] += 1
            group["citations"].append({"citationType": kind})
        body: dict[str, Any] = {"success": True, self.key: list(groups.values())}
        if "totalEdges" in self.signals:
            body["totalEdges"] = len(self.edges)
        if "offset" in self.signals:
            body["offset"] = int(request.url.params.get("offset", 0))
            body["limit"] = limit
        return body


def reference_edges(server, page_size):
    """The previous release's walk: groups yielded and requests made."""
    out: list[str] = []
    offset = 0
    while True:
        server.requests.append(None)
        fake = httpx.Request("GET", f"{LOOPBACK}/x?limit={page_size}&offset={offset}")
        body = server.answer(fake)
        groups = body[server.key]
        out.extend(g["celexNumber"] for g in groups)
        rows = sum(g["citationCount"] or len(g["citations"]) for g in groups)
        offset += rows
        total = body.get("totalEdges")
        if rows == 0 or rows < page_size or (total is not None and offset >= total):
            return out, len(server.requests)


def identical_run(n, before=3, after=3):
    edges = [(f"A{i}", "reference") for i in range(before)]
    edges += [("SAME", "reference")] * n
    edges += [(f"B{i}", "reference") for i in range(after)]
    return edges


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method", ["iter_cited_by", "iter_cites"])
@pytest.mark.parametrize("run_length", [150, 350])
@pytest.mark.parametrize("page_size", [2, 10, 50, 100])
def test_field_identical_edges_return_everything(mode, method, run_length, page_size):
    def factory():
        server = EdgeServer(identical_run(run_length))
        server.key = "citedBy" if method == "iter_cited_by" else "cites"
        return server

    expected, expected_requests = reference_edges(factory(), page_size)
    server = factory()
    got = walk(mode, server, lambda c: getattr(c.citations, method)("X", page_size=page_size))
    assert [g.celex_number for g in got] == expected
    assert len(server.requests) == expected_requests


@pytest.mark.parametrize("mode", MODES)
def test_identical_pages_without_signals_are_still_walked_up_to_three_repeats(mode):
    # 150 identical edges at 50 per page: three equal pages in a row (two repeats).
    def factory():
        return EdgeServer([("SAME", "reference")] * 150 + [("Z", "reference")], signals=())

    expected, _ = reference_edges(factory(), 50)
    got = walk(mode, factory(), lambda c: c.citations.iter_cited_by("X", page_size=50))
    assert [g.celex_number for g in got] == expected


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method", ["iter_cited_by", "iter_cites"])
def test_edge_server_ignoring_offset_without_signals_raises_after_three_repeats(mode, method):
    server = EdgeServer([(f"D{i}", "reference") for i in range(10)], signals=(), ignore_offset=True)
    server.key = "citedBy" if method == "iter_cited_by" else "cites"
    items, err = walk_until_error(
        mode, server, lambda c: getattr(c.citations, method)("X", page_size=2)
    )
    assert len(server.requests) == 4
    assert [g.celex_number for g in items] == ["D0", "D1"] * 3
    assert err.body[server.key][0]["celexNumber"] == "D0"


class EchoZero(EdgeServer):
    """Ignores ``offset`` and says so: it always echoes ``offset: 0``."""

    def answer(self, request):
        body = super().answer(request)
        body["offset"] = 0
        return body


@pytest.mark.parametrize("mode", MODES)
def test_edge_server_echoing_another_offset_raises_at_once(mode):
    server = EchoZero([(f"D{i}", "reference") for i in range(10)], ignore_offset=True)
    items, err = walk_until_error(
        mode, server, lambda c: c.citations.iter_cited_by("X", page_size=2)
    )
    assert [g.celex_number for g in items] == ["D0", "D1"]
    assert len(server.requests) == 2
    assert "did not advance" in str(err)


@pytest.mark.parametrize("mode", MODES)
def test_edge_server_ignoring_offset_with_total_ends_by_total(mode):
    server = EdgeServer(
        [(f"D{i}", "reference") for i in range(6)], signals=("totalEdges",), ignore_offset=True
    )
    items = walk(mode, server, lambda c: c.citations.iter_cited_by("X", page_size=2))
    assert len(items) == 6 and len(server.requests) == 3


class Clamping(Server):
    """Serves at most 2 rows per page whatever ``limit`` asks for, and
    echoes the limit it applied."""

    def __init__(self, edges, **kw):
        super().__init__(**kw)
        self.edges = edges

    def answer(self, request):
        offset = int(request.url.params.get("offset", 0))
        rows = self.edges[offset : offset + 2]
        return {
            "success": True,
            "citedBy": [{"celexNumber": c, "citationCount": 1, "citations": [{}]} for c in rows],
            "offset": offset,
            "limit": 2,
        }


@pytest.mark.parametrize("mode", MODES)
def test_echoed_limit_decides_a_short_page(mode):
    server = Clamping([f"D{i}" for i in range(5)])
    items = walk(mode, server, lambda c: c.citations.iter_cited_by("X", page_size=5))
    assert [g.celex_number for g in items] == ["D0", "D1", "D2", "D3", "D4"]
    assert len(server.requests) == 3


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("count", [-1, 10**400, "7", True, None, 2.5])
def test_a_citation_count_that_is_not_a_count_uses_the_edge_list(mode, count):
    class Odd(Server):
        def answer(self, request):
            if request.url.params.get("offset") != "0":
                return {"success": True, "citedBy": []}
            return {
                "success": True,
                "citedBy": [{"celexNumber": "A", "citationCount": count, "citations": [{}, {}]}],
            }

    server = Odd()
    items = walk(mode, server, lambda c: c.citations.iter_cited_by("X", page_size=2))
    assert len(items) == 1
    assert [r.url.params.get("offset") for r in server.requests] == ["0", "2"]


@pytest.mark.parametrize("mode", MODES)
def test_a_page_without_its_list_ends_the_walk(mode):
    class NoList(Server):
        def answer(self, request):
            return {"success": True, "totalEdges": 99}

    server = NoList()
    assert walk(mode, server, lambda c: c.citations.iter_cited_by("X", page_size=2)) == []
    assert len(server.requests) == 1


@pytest.mark.parametrize("mode", MODES)
def test_well_formed_edge_walk_of_10k(mode):
    edges = [(f"D{i // 3}", "reference") for i in range(10_000)]
    server = EdgeServer(edges)
    expected, expected_requests = reference_edges(EdgeServer(edges), 100)
    items = walk(mode, server, lambda c: c.citations.iter_cited_by("X", page_size=100))
    assert [g.celex_number for g in items] == expected
    assert len(server.requests) == expected_requests == 100


# ── national cursors ─────────────────────────────────────────────────────


class CursorServer(Server):
    def __init__(self, cursors, **kw):
        super().__init__(**kw)
        self.cursors = cursors  # nextCursor of each page, in order

    def answer(self, request):
        n = len(self.requests) - 1
        cursor = self.cursors[n] if n < len(self.cursors) else None
        return {
            "success": True,
            "results": [{"id": f"AT-{n}"}],
            "hasMore": cursor is not None,
            "nextCursor": cursor,
        }


def national_walk(cursor=None, limit=None):
    return lambda c: c.national.iter_documents("AT", cursor=cursor, limit=limit)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "cursors, sent",
    [
        (["a", "a"], [None, "a"]),
        (["a", "b", "a"], [None, "a", "b"]),
    ],
)
def test_a_repeated_cursor_raises_and_the_page_is_not_yielded(mode, cursors, sent):
    server = CursorServer(cursors)
    pages, err = walk_until_error(mode, server, national_walk())
    assert [r.url.params.get("cursor") for r in server.requests] == sent
    assert len(pages) == len(sent) - 1
    assert "repeated a page cursor" in str(err)
    assert err.body["nextCursor"] == cursors[len(sent) - 1]


@pytest.mark.parametrize("mode", MODES)
def test_a_resumed_walk_counts_its_start_cursor_as_sent(mode):
    server = CursorServer(["start"])
    pages, err = walk_until_error(mode, server, national_walk(cursor="start"))
    assert [r.url.params.get("cursor") for r in server.requests] == ["start"]
    assert pages == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("cursor", [None, ""])
def test_no_cursor_ends_as_before(mode, cursor):
    class End(Server):
        def answer(self, request):
            return {
                "success": True,
                "results": [{"id": "x"}],
                "hasMore": True,
                "nextCursor": cursor,
            }

    server = End()
    assert len(walk(mode, server, national_walk())) == 1
    assert len(server.requests) == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("cursor", [5, ["a"], {"c": 1}, True])
def test_a_cursor_that_is_not_a_string_raises(mode, cursor):
    class Odd(Server):
        def answer(self, request):
            return {
                "success": True,
                "results": [{"id": "x"}],
                "hasMore": True,
                "nextCursor": cursor,
            }

    server = Odd()
    pages, err = walk_until_error(mode, server, national_walk())
    assert pages == [] and len(server.requests) == 1


@pytest.mark.parametrize("mode", MODES)
def test_has_more_not_true_ends_even_with_a_repeated_cursor(mode):
    class Last(Server):
        def answer(self, request):
            return {"success": True, "results": [{"id": "x"}], "hasMore": "yes", "nextCursor": "c"}

    server = Last()
    assert len(walk(mode, server, national_walk(cursor="c"))) == 1


@pytest.mark.parametrize("mode", MODES)
def test_well_formed_cursor_walk_of_10k(mode):
    server = CursorServer([f"c{i}" for i in range(1, 10_000)])
    pages = walk(mode, server, national_walk())
    assert len(pages) == 10_000 and len(server.requests) == 10_000


# ── caller arguments ─────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("bad", [0, -1, 2.0, True, "10", None])
def test_bad_page_sizes_raise_before_any_request(mode, bad):
    for start in (
        lambda c: c.citations.iter_cited_by("X", page_size=bad),
        lambda c: c.citations.iter_cites("X", page_size=bad),
        lambda c: c.webhooks.iter_deliveries("wh_1", page_size=bad),
    ):
        server = Server()
        with pytest.raises(ValueError):
            walk(mode, server, start)
        assert server.requests == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("bad", [0, -5, 1.5, False])
def test_bad_national_limit_raises_before_any_request(mode, bad):
    server = Server()
    with pytest.raises(ValueError):
        walk(mode, server, national_walk(limit=bad))
    assert server.requests == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("value", [2**53, 2**60, -2, 1.5, "0", True])
def test_figures_that_are_not_usable_numbers_are_no_signal(mode, value):
    class Odd(EdgeServer):
        def answer(self, request):
            body = super().answer(request)
            body["offset"] = value  # not a usable number: no "did not advance"
            body["totalEdges"] = value
            body["limit"] = value
            return body

    server = Odd([(f"D{i}", "reference") for i in range(5)], signals=())
    items = walk(mode, server, lambda c: c.citations.iter_cited_by("X", page_size=2))
    assert [g.celex_number for g in items] == [f"D{i}" for i in range(5)]
    assert len(server.requests) == 3
