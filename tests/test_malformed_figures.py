"""A malformed or absurd server answer never makes the client raise a raw
Python error or hang: it raises the SDK's documented error, or the value
reads as absent. Well-formed values read as before."""

import asyncio
import json

import httpx
import pytest

from lexapi import (
    AsyncLexAPI,
    LexAPI,
    NationalCoverageResponse,
    NationalSemanticUnavailableError,
    RateLimitedError,
    SemanticLegislationResponse,
    SemanticSearchResponse,
    passages,
)
from lexapi._retry import RetryConfig, parse_retry_after, server_wait_hint

LOOPBACK = "http://127.0.0.1:9/api/v1"
HUGE_INT = 10**400  # valid JSON, too large for a float
MAX_REQUESTS = 20
FAST = RetryConfig(max_retries=1, backoff_base=0.0001, backoff_cap=0.001)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Offline tests blocked a real HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def _handler(responses, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        seen.append(request)
        if len(seen) > MAX_REQUESTS:
            raise RuntimeError("runaway loop: too many requests")
        status, body, headers = responses[min(len(seen) - 1, len(responses) - 1)]
        content = body if isinstance(body, bytes) else json.dumps(body).encode()
        return httpx.Response(
            status, content=content, headers={"content-type": "application/json", **headers}
        )

    return handler


def run(mode, responses, call, retry=None, timeout=5.0):
    """Run ``call(client)`` sync or async; async under a hard timeout so a
    hang fails fast."""
    seen: list[httpx.Request] = []
    kwargs = {"retry_config": retry} if retry is not None else {"max_retries": 0}
    transport = httpx.MockTransport(_handler(responses, seen))
    if mode == "sync":
        with LexAPI("lex_test_key", base_url=LOOPBACK, transport=transport, **kwargs) as c:
            return call(c), seen

    async def go():
        async with AsyncLexAPI(
            "lex_test_key", base_url=LOOPBACK, transport=transport, **kwargs
        ) as c:
            return await asyncio.wait_for(call(c), timeout)

    return asyncio.run(go()), seen


# ── 1. float fields ──────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [HUGE_INT, -HUGE_INT])
def test_huge_integer_scores_read_as_none(value):
    leg = SemanticLegislationResponse(
        {"success": True, "results": [{"law_id": "32016R0679", "score": value}]}
    )
    assert leg.results[0].score is None
    case = SemanticSearchResponse(
        {"success": True, "results": [{"score": value, "metadata": {"celex_id": "62018CJ0311"}}]}
    )
    assert case.results[0].score is None
    (p,) = passages.from_legislation_search(leg).passages
    assert p.score is None


def test_huge_integer_coverage_figures_read_as_none():
    graph = {
        "decisionResolutionRate": HUGE_INT,
        "actEuRate": 0.5,
        "precision": {"errorUpperBound": HUGE_INT},
        "floors": {"decisionsToActs": HUGE_INT, "actsToEu": 1},
    }
    body = {"success": True, "jurisdictions": [{"code": "AT", "citationGraph": graph}]}
    (j,) = NationalCoverageResponse(body).jurisdictions
    cg = j.citation_graph
    assert cg is not None
    assert (cg.decision_resolution_rate, cg.act_eu_rate) == (None, 0.5)
    assert cg.precision is not None and cg.precision.error_upper_bound is None
    assert cg.floors is not None
    assert (cg.floors.decisions_to_acts, cg.floors.acts_to_eu) == (None, 1.0)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_huge_integer_score_through_the_client(mode):
    body = {"success": True, "results": [{"law_id": "32016R0679", "score": HUGE_INT}]}
    resp, _ = run(mode, [(200, body, {})], lambda c: c.semantic_legislation_search("erasure"))
    assert resp.results[0].score is None


@pytest.mark.parametrize("value, expected", [(0.91, 0.91), (1, 1.0), (True, None), ("0.9", None)])
def test_well_formed_and_released_float_values_are_unchanged(value, expected):
    leg = SemanticLegislationResponse({"success": True, "results": [{"score": value}]})
    assert leg.results[0].score == expected


# ── 2. retryAfter in the body ────────────────────────────────────────────


def _429(retry_after, where="top"):
    if where == "top":
        return {"error": "Too many requests", "code": "RATE_LIMITED", "retryAfter": retry_after}
    return {
        "success": False,
        "error": {
            "code": "RATE_LIMITED",
            "message": "Too many requests",
            "details": {"retryAfter": retry_after},
        },
    }


NATIONAL_503 = {
    "error": "Index unavailable",
    "code": "NATIONAL_SEMANTIC_UNAVAILABLE",
    "jurisdiction": "AT",
    "partialReason": "SEARCH_INCOMPLETE",
}


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("where", ["top", "details"])
@pytest.mark.parametrize("value", [HUGE_INT, -HUGE_INT, -5])
def test_absurd_body_retry_after_on_429(mode, where, value):
    with pytest.raises(RateLimitedError) as info:
        run(mode, [(429, _429(value, where), {})], lambda c: c.get_info())
    assert info.value.retry_after is None


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_absurd_body_retry_after_on_national_503(mode):
    body = dict(NATIONAL_503, retryAfter=HUGE_INT)
    with pytest.raises(NationalSemanticUnavailableError) as info:
        run(mode, [(503, body, {})], lambda c: c.national.semantic_search("AT", "Datenschutz"))
    assert info.value.retry_after is None


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_huge_body_retry_after_still_retries_with_backoff(mode):
    ok = {"success": True, "service": "LexAPI"}
    resp, seen = run(
        mode, [(429, _429(HUGE_INT), {}), (200, ok, {})], lambda c: c.get_info(), retry=FAST
    )
    assert len(seen) == 2 and resp.service == "LexAPI"


def test_well_formed_body_retry_after_is_unchanged():
    assert server_wait_hint(httpx.Headers(), _429(7), 429) == 7.0
    assert server_wait_hint(httpx.Headers(), _429(2.5, "details"), 429) == 2.5


# ── 3. Retry-After / RateLimit-Reset headers ─────────────────────────────

BAD_HEADER_VALUES = ["nan", "NaN", "inf", "-inf", "Infinity", "-3", "1e999", "junk", ""]


@pytest.mark.parametrize("value", BAD_HEADER_VALUES)
def test_bad_header_values_are_absent(value):
    assert parse_retry_after(value) is None
    headers = httpx.Headers({"ratelimit-reset": value})
    assert server_wait_hint(headers, None, 429) is None


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("name", ["retry-after", "ratelimit-reset"])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-3", "1e999"])
def test_bad_header_retries_with_backoff_and_never_hangs(mode, name, value):
    ok = {"success": True, "service": "LexAPI"}
    limited = ({"error": "Too many requests", "code": "RATE_LIMITED"}, {name: value})
    resp, seen = run(
        mode,
        [(429, limited[0], limited[1]), (200, ok, {})],
        lambda c: c.get_info(),
        retry=FAST,
    )
    assert len(seen) == 2 and resp.service == "LexAPI"


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_bad_header_leaves_err_retry_after_absent(mode, value):
    body = {"error": "Too many requests", "code": "RATE_LIMITED"}
    with pytest.raises(RateLimitedError) as info:
        run(mode, [(429, body, {"retry-after": value})], lambda c: c.get_info())
    assert info.value.retry_after is None


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_large_finite_wait_is_capped_by_backoff_cap(mode):
    ok = {"success": True, "service": "LexAPI"}
    body = {"error": "Too many requests", "code": "RATE_LIMITED"}
    resp, seen = run(
        mode,
        [(429, body, {"retry-after": "999999"}), (200, ok, {})],
        lambda c: c.get_info(),
        retry=FAST,
    )
    assert len(seen) == 2


def test_well_formed_headers_are_unchanged():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("0") == 0.0
    assert server_wait_hint(httpx.Headers({"ratelimit-reset": "4"}), None, 429) == 4.0


# ── 4. deeply nested export line ─────────────────────────────────────────

DEEP = ("[" * 100_000 + "]" * 100_000).encode()


def _export_handler(seen):
    body = (
        b'{"_meta": {"total": 1}}\n'
        + DEEP
        + b'\n{"celex": "32016R0679"}\n{"_done": {"count": 1}}\n'
    )

    def handler(request):
        assert request.url.host == "127.0.0.1"
        seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    return handler


def test_deeply_nested_export_line_is_a_malformed_line_sync():
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(_export_handler(seen))
    with LexAPI("lex_test_key", base_url=LOOPBACK, transport=transport, max_retries=0) as c:
        with c.export(limit=1) as stream:
            rows = list(stream)
            assert rows == [{"celex": "32016R0679"}]
            assert stream.invalid_lines == 1


def test_deeply_nested_export_line_is_a_malformed_line_async():
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(_export_handler(seen))

    async def go():
        async with AsyncLexAPI(
            "lex_test_key", base_url=LOOPBACK, transport=transport, max_retries=0
        ) as c:
            stream = await c.export(limit=1)
            async with stream:
                rows = [row async for row in stream]
                return rows, stream.invalid_lines

    rows, invalid = asyncio.run(asyncio.wait_for(go(), 10))
    assert rows == [{"celex": "32016R0679"}] and invalid == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_non_finite_score_reads_as_none(mode):
    raw = b'{"success": true, "results": [{"law_id": "32016R0679", "score": 1e999}]}'
    resp, _ = run(mode, [(200, raw, {})], lambda c: c.semantic_legislation_search("erasure"))
    assert resp.results[0].score is None
