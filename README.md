# lexapi (Python)

Official Python SDK for [LexAPI](https://lex-api.com/?ref=pypi-sdk) — European legal data, made queryable. EUR-Lex, CJEU case law, and the Official Journal behind one REST API.

Covers every documented `/api/v1` endpoint (search, documents, point-in-time versions, semantic search incl. `asOf`, the citation graph, webhooks, bulk export), sync and async, with typed responses, typed errors, retries and credit visibility. Python 3.9+; `httpx` is the only dependency. See [CHANGELOG.md](CHANGELOG.md).

## Install

```bash
pip install lexapi-client
```

> The PyPI distribution is `lexapi-client` (same name as npm’s `@lexapi/client`); the import is `lexapi` — `from lexapi import LexAPI`.

## Quickstart

```python
from lexapi import LexAPI

client = LexAPI(api_key="lex_...")  # or set LEXAPI_API_KEY
info = client.get_info()            # free (0 credits)
print(info.subscription_info.tier, info.credits_remaining)
```

Keys come from the [LexAPI dashboard](https://lex-api.com/dashboard?ref=pypi-sdk) and are prefixed `lex_`. Keep them server-side — never ship them in client-side code.

### Search

```python
results = client.search(
    "cybersecurity",
    author=["court-of-justice"],
    year=2024,
    document_type="judgment",
    max_pages=1,          # 3 credits per page, tier-clamped, unfetched pages refunded
)
for hit in results.results:
    print(hit.celex, hit.document_type_code, hit.title)

# Capping and fallbacks are surfaced, never hidden:
if results.truncated:
    print("tier-capped:", results.truncated_reason)
if results.partial:
    print("partial answer:", results.partial_reason)
if results.post_filtered_by:
    print("controller post-filter fired on:", results.post_filtered_by)
print(results.source)   # "soap" | "cellar" | "corpus" | None (EUR-Lex result pages)
print(results.cache)    # "hit" | "miss" | "bypass" — 60-minute result cache, same price

client.search("*", date_from="2026-10-01", date_to="2026-10-25", fresh=True)  # filter-only, bypass cache
```

### Documents

```python
resp = client.get_document("32016R0679", language="fr")
doc = resp.document
print(doc.title, doc.date_of_document_iso, resp.source)  # source: corpus | cellar | cellar-oj | live
for article in doc.content.articles[:3]:
    print(article.number, article.title)

# Notice published but no text: a 200 with content=None (billed 1 credit, not 5)
if not resp.text_available:
    ta = doc.text_availability
    print(ta.reason, ta.retryable, ta.available_languages)   # None = unknown, [] = none
    if ta.latest_consolidated:                               # base acts: pointer to CONSLEG
        print("try", ta.latest_consolidated.celex)

# Title not in the requested language? titleLanguage says which language it is in
print(doc.title_language)

# Authentic text in another language in one call (most General Court decisions have no English)
resp = client.get_document("62019TJ0001", accept_languages=["fr", "de"])   # or "any"
print(resp.document.language, resp.document.language_fallback)

# Trim the payload: only metadata + one article
resp = client.get_document("32016R0679", include=["metadata", "articles"], article_id="17")

# Batch — per-CELEX failures don't abort the batch
batch = client.get_documents_batch(["32016R0679", "62018CJ0311"])
print(batch.successful, "ok /", batch.failed, "failed /", batch.text_unavailable, "without text")
print(batch.corpus_hits, batch.cellar_served, batch.oj_recovered, batch.live_fetches)
if batch.trimmed:  # tier ceiling hit — mirrored from X-Warning too
    print(batch.trimmed_reason)

recent = client.get_recent_documents(days=3, document_type=["regulation", "directive"])
print(recent.source)  # "cellar" (unfiltered: 3 credits flat) or "live" (per page)
by_url = client.get_document_by_url(
    "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679"
)
meta = client.get_document_metadata("32016R0679").metadata  # no body parse
celex = client.resolve("ECLI:EU:C:2020:559").celex          # CELEX/URL/ELI/ECLI → CELEX
```

Typed responses stay mapping-compatible (`results["totalResults"]` works) and
every typed model keeps the raw payload on `.raw`.

### Semantic search

```python
# Case law (CJEU) — concept queries, similarity-ranked (5 credits)
resp = client.semantic_search(
    "transfer of personal data to third countries",
    min_score=0.5,   # widen recall past the ~0.7 default relevance floor
    limit=10,
)
for hit in resp.results:
    print(f"{hit.score:.2f}", hit.celex, hit.case_number, hit.case_name, hit.snippet)
if resp.hint:        # present only on low-confidence (best-effort) responses
    print(resp.hint)

# HyDE query rewriting for terse/keyword queries — 15 credits instead of 5
resp = client.semantic_search("credit scoring article 22", hyde=True)
print(resp.hyde)                   # False means the LLM fell back — premium auto-refunded
print(resp.units_charged)          # 15 when HyDE ran, 5 after a fallback
print(resp.hypothetical_document)  # the LLM-drafted passage (when HyDE ran)

# Legislation — article-level matches (HyDE is case-law only; limit capped at 50)
laws = client.semantic_legislation_search("right to be forgotten", limit=5)
for hit in laws.results:
    print(hit.law_id, hit.article_ref, hit.law_title)
    if hit.consolidated:   # merged hit: consolidated text + the authentic act's own match
        print("  consolidated as of", hit.consolidated_as_of, "— cite", hit.base_celex)
        print("  ", hit.consolidated_note)
```

#### The text in force on a date (`as_of`)

```python
import datetime

laws = client.semantic_legislation_search("own funds requirements", as_of="2019-06-01")
# as_of also accepts a datetime.date
print(laws.as_of, laws.as_of_summary.live_fetches, laws.as_of_summary.unresolved)
for hit in laws.results:
    if hit.as_of_resolved:
        v = hit.as_of_version            # kind: "consolidated" | "as_published"
        print(hit.law_id, hit.article_ref, v.kind, v.date)
        if hit.matched_version:          # the text was swapped for the version in force
            print("  index matched", hit.matched_version.law_id)
    else:
        print(hit.law_id, "kept its latest text:", hit.as_of_reason)  # see lexapi.AS_OF_REASONS
```

Each hit is checked for being in force on that date and answered with the act
as published before its first consolidation, otherwise the consolidated version
in force. Cost: 5 credits for the search; each version the corpus doesn't hold
is fetched live (at most 3 per request by default) and settles at 4 (Cellar),
5 (browser), 1 (no text) or 0 (failed / timed out). A fetch the pool can't
cover is skipped (`as_of_reason == "insufficient_credits"`) — only the search
itself can raise `CreditsExhaustedError`. An invalid date is a 400
(`InvalidParamsError`, refunded).

### Citations

The typed citation graph lives under `client.citations`:

```python
client.citations.extract("32016R0679")          # crawl + persist edges (5 credits, 1 if already done)

inbound = client.citations.cited_by("32008R1272", citation_type="corrigendum", limit=50)
print(inbound.total_citations, "edges;", inbound.unique_documents, "documents in this page")
print(inbound.relation_sync.status)             # Cellar refresh state of amendment/corrigendum/repeal edges

for group in client.citations.iter_cited_by("32008R1272"):   # walks every edge page (1 credit each)
    print(group.celex_number, group.citation_count)

outbound = client.citations.cites("32016R0679")
network = client.citations.network("32016R0679", limit=100)   # 20 credits
if network.partial:  # server-side budget expired — 200, not an error
    print(network.message)

path = client.citations.path("32016R0679", "31995L0046", max_depth=4)
if path.found:
    print(" -> ".join(node.celex_number for node in path.path))

related = client.citations.related("32016R0679", limit=10)  # bibliographic coupling
stats = client.citations.stats()
print(stats.most_cited[0].celex_number)
```

### Webhooks

```python
# New documents matching criteria, a watch list of acts, or both.
# The secret is returned ONLY on create — store it to verify X-Webhook-Signature.
created = client.webhooks.create(
    "Chemicals compliance watch",
    "https://example.com/webhooks/lexapi",
    {"documentType": "regulation", "query": "chemical"},     # documents.new (optional)
    watch_celex=["32006R1907", "32008R1272"],                # watchlist.changes (optional)
)
secret = created.webhook.secret

hooks = client.webhooks.list()
hook = client.webhooks.get(created.webhook.id)    # incl. 20 recent deliveries + retry_policy
print(hook.webhook.criteria_errors)                # stored criteria a newer rule rejects
resp = client.webhooks.update(hook.webhook.id, status="ACTIVE")  # resets failures, re-queues
print(resp.requeued_deliveries)
client.webhooks.update(hook.webhook.id, watch_celex=["32008R1272"], remove_search_criteria=True)

result = client.webhooks.test(hook.webhook.id)    # synchronous test delivery
if not result.ok:                                 # HTTP 200 either way
    print(result.delivery.error_message)

for delivery in client.webhooks.iter_deliveries(hook.webhook.id):  # walks all pages
    print(delivery.status, delivery.response_status)

client.webhooks.delete(hook.webhook.id)
```

Verifying a delivery in your endpoint (pass the **raw** request body bytes):

```python
from lexapi import WebhookSignatureError, verify_webhook

def handle_delivery(raw_body: bytes, signature: str) -> int:
    try:
        event = verify_webhook(raw_body, signature, secret)  # 5-minute replay window
    except WebhookSignatureError:
        return 401
    if event["event"] == "watchlist.changes":
        for change in event["data"]["changes"]:
            print(change["watchedCelex"], change["relation"], change["document"]["celex"])
    # dedupe side effects on event["delivery"]["id"] — stable across retries
    return 200
```

### Corpus export (BUSINESS, SCALE, ENTERPRISE)

`export()` streams NDJSON rows with constant memory (1 credit per row); the
`_meta` / `_done` envelopes and export headers are exposed on the stream:

```python
with client.export(document_type="regulation", date_from="2024-01-01", limit=10_000) as stream:
    print(stream.total, stream.streaming)   # X-Export-Total / X-Export-Streaming
    for row in stream:                      # one dict per corpus row
        ingest(row["celex"], row.get("parsedContent"))
    print(stream.meta)                      # leading _meta envelope
    print(stream.written, stream.truncated, stream.skipped_stubs)  # _done trailer

# Metadata-only rows (no text) are skipped unless include_stubs=True.

# Async
async with await client.export(fetched_since="2026-06-01T00:00:00Z") as stream:
    async for row in stream:
        ingest(row)
```

Licensing: using exported content to train, fine-tune or evaluate ML models
needs a separate licence ([Terms §6](https://lex-api.com/terms?ref=pypi-sdk#acceptable-use)).

### Point-in-time versions

Versions are LexAPI *observation snapshots* — the document as fetched — not
legal in-force reconstructions; history begins at first ingestion. (For the
text *in force* on a date, use `semantic_legislation_search(as_of=...)` or a
consolidated CELEX such as `02016R0679-20180524`.)

```python
history = client.list_document_versions("32016R0679")
print(history.current_version, history.tracked_since)
for v in history.versions:
    print(v.version, v.fetched_at, v.content_hash, "(current)" if v.is_current else "")

snapshot = client.get_document_version("32016R0679", 2)
print(snapshot.document.parsed_content)

as_of = client.get_document_at_date("32016R0679", "2026-03-20")  # or a datetime.date
print(as_of.as_of, as_of.document.version)
```

Each call costs 1 credit. Dates before the document entered the corpus raise
`NotFoundError` with the tracking start date in the message.

### Async

Every operation is also available on the async client:

```python
import asyncio
from lexapi import AsyncLexAPI

async def main():
    async with AsyncLexAPI() as client:  # LEXAPI_API_KEY from the env
        info = await client.get_info()
        laws = await client.semantic_legislation_search("own funds", as_of="2019-06-01")
        async for group in client.citations.iter_cited_by("32008R1272"):
            print(group.celex_number)

asyncio.run(main())
```

### Credit visibility

Every response wrapper exposes the credit envelope without losing access to
the raw body:

```python
resp = client.get_document("32016R0679")
resp["credits"]               # raw body access still works (Mapping)
resp.units_charged            # credits this request cost after settlement (1 from the corpus)
resp.credits_remaining        # credits.remaining, falling back to usage.remaining
resp.resets_at                # ISO-8601 timestamp of the next monthly reset
resp.credits                  # full typed CreditsInfo (None on legacy daily-call accounts)
resp.subscription_info        # typed subscription block (tier, max_pages, max_results, ...)
resp.rate_limit_remaining     # RateLimit-Remaining (also rate_limit_limit / rate_limit_reset)
```

Refunds for *failed* requests (4xx in full, 404 down to 1, 5xx in full) are
applied just after the response is sent, so the next call reflects them.

### Typed errors

All non-2xx responses raise a typed exception mirroring the API's stable
error-code enum (both the typed envelope and the legacy bare `{"error": ...}`
shape are handled; a legacy body's `details` list is kept on `err.details`):

```python
from lexapi import (
    LexAPIError,          # base — code / message / status / details / body / retry_after
    NotFoundError,        # 404 NOT_FOUND
    InvalidCelexError,    # 400 INVALID_CELEX
    InvalidParamsError,   # 400 INVALID_PARAMS (e.g. an invalid as_of)
    RateLimitedError,     # 429 — .retry_after; .quota_exhausted / .resets_at on legacy quotas
    CreditsExhaustedError,# 402 CREDITS_EXHAUSTED — .resets_at, .operation, .weight, .available
    TierForbiddenError,   # 403 TIER_FORBIDDEN — .upgrade_url on legacy tier gates
    UpstreamError,        # 502 UPSTREAM_ERROR
    TimeoutError,         # 504 TIMEOUT (server-side upstream timeout, retry-safe)
    AuthenticationError,  # 401 — missing/invalid/deactivated key
    RequestTimeoutError,  # the SDK's own timeout elapsed
)

try:
    client.semantic_search("credit scoring")
except CreditsExhaustedError as err:
    print(f"need {err.weight}, have {err.available}; resets {err.resets_at}")
except RateLimitedError as err:
    print(f"rate limited, retry in {err.retry_after}s")
except LexAPIError as err:
    print(err.code, err.status, err.message)
```

### Retries

Idempotent requests are retried automatically (default: up to 3 retries on
429/502/503/504, Cloudflare 520–524 and connect errors) with exponential
backoff + full jitter, honoring the server's `Retry-After` header. Non-idempotent
POSTs are only retried on 429 and connect-phase failures. A legacy-plan 429
that reports an exhausted *quota* is not retried (it can't succeed before
`resets_at`), and neither is a 402.

```python
from lexapi import LexAPI, RetryConfig

client = LexAPI(max_retries=5)                     # just the budget
client = LexAPI(retry_config=RetryConfig(          # full control
    max_retries=2, backoff_base=1.0, backoff_cap=10.0,
))
client = LexAPI(max_retries=0)                     # disable retries
```

### Configuration

```python
client = LexAPI(
    api_key="lex_...",              # or LEXAPI_API_KEY env var
    base_url="https://lex-api.com/api/v1",
    timeout=95.0,                   # read/overall seconds (or an httpx.Timeout); default 95
    connect_timeout=10.0,
    user_agent_suffix="myapp/1.0",  # appended to lexapi-python/<version>
)

# Per-call overrides share the connection pool:
client.with_options(timeout=180, max_retries=0).semantic_legislation_search(
    "own funds", as_of="2019-06-01"
)
```

The 95 s default sits above the API's 90 s upstream budget, so a slow EUR-Lex
fetch comes back as the server's typed `TimeoutError` (504, refunded) rather
than a local `RequestTimeoutError`.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy
```

Docs: <https://lex-api.com/docs?ref=pypi-sdk>
