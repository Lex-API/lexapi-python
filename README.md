# lexapi (Python)

Official Python SDK for [LexAPI](https://lex-api.com) — European legal data, made queryable. EUR-Lex, CJEU case law, and the Official Journal behind one REST API.

> **Status: pre-release scaffold (0.x).** API coverage and installation instructions below are placeholders until the first PyPI release. See [PLAN.md](PLAN.md).

## Install

```bash
pip install lexapi-client
```

> The PyPI distribution is `lexapi-client` (same name as npm’s `@lexapi/client`); the import is `lexapi` — `from lexapi import LexAPI`.

## Quickstart

```python
from lexapi import LexAPI

client = LexAPI(api_key="lex_...")  # or set LEXAPI_API_KEY
info = client.get_info()
print(info["subscription"]["tier"], info["usage"]["remaining"])
```

Keys come from the [LexAPI dashboard](https://lex-api.com) and are prefixed `lex_`. Keep them server-side — never ship them in client-side code.

### Search

```python
results = client.search(
    "cybersecurity",
    author=["court-of-justice"],
    year=2024,
    document_type="judgment",
    max_pages=1,
)
for hit in results.results:
    print(hit.celex, hit.document_type_code, hit.title)

# Capping is surfaced, never hidden:
if results.truncated:
    print("tier-capped:", results.truncated_reason)
if results.partial:
    print("an upstream page timed out:", results.partial_reason)
if results.post_filtered_by:
    print("controller post-filter fired on:", results.post_filtered_by)
```

### Documents

```python
doc = client.get_document("32016R0679", language="en").document
print(doc.title, doc.date_of_document_iso)
for article in doc.content.articles[:3]:
    print(article.number, article.title)

# Trim the payload: only metadata + one article
resp = client.get_document("32016R0679", include=["metadata", "articles"], article_id="17")
print(resp.source)  # "corpus" or "live" (X-Source header)

# Batch — per-CELEX failures don't abort the batch
batch = client.get_documents_batch(["32016R0679", "62018CJ0311"])
print(batch.successful, "ok /", batch.failed, "failed")
if batch.trimmed:  # tier ceiling hit — mirrored from X-Warning too
    print(batch.trimmed_reason)

recent = client.get_recent_documents(days=3, document_type="regulation")
by_url = client.get_document_by_url(
    "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679"
)
meta = client.get_document_metadata("32016R0679").metadata  # faster, no body parse
celex = client.resolve("ECLI:EU:C:2020:559").celex          # CELEX/URL/ELI/ECLI → CELEX
```

Typed responses stay mapping-compatible (`results["totalResults"]` works) and
every typed model keeps the raw payload on `.raw`.

### Citations

The typed citation graph lives under `client.citations`:

```python
client.citations.extract("32016R0679")          # crawl + persist edges (idempotent)

inbound = client.citations.cited_by("31995L0046", citation_type="repeal", limit=50)
print(inbound.total_citations, "edges from", inbound.unique_documents, "documents")

outbound = client.citations.cites("32016R0679")
network = client.citations.network("32016R0679", limit=100)
if network.partial:  # server-side budget expired — 200, not an error
    print(network.message)

path = client.citations.path("32016R0679", "31995L0046", max_depth=4)
if path.found:
    print(" -> ".join(node.celex_number for node in path.path))

related = client.citations.related("32016R0679", limit=10)  # bibliographic coupling
stats = client.citations.stats()
print(stats.most_cited[0].celex_number)
```

### Semantic search

```python
# Case law (CJEU) — concept queries, similarity-ranked
resp = client.semantic_search(
    "transfer of personal data to third countries",
    min_score=0.5,   # widen recall past the ~0.7 default relevance floor
    limit=10,
)
for hit in resp.results:
    print(f"{hit.score:.2f}", hit.celex, hit.case_number, hit.case_name)
if resp.hint:        # present only on low-confidence (best-effort) responses
    print(resp.hint)

# HyDE query rewriting for terse/keyword queries — 15 credits instead of 5
resp = client.semantic_search("credit scoring article 22", hyde=True)
print(resp.hyde)                   # False means the LLM fell back — premium auto-refunded
print(resp.units_charged)          # 15 when HyDE ran, 5 after a fallback
print(resp.hypothetical_document)  # the LLM-drafted passage (when HyDE ran)

# Legislation — article-level matches (HyDE is case-law only)
laws = client.semantic_legislation_search("right to be forgotten", limit=5)
for hit in laws.results:
    print(hit.law_id, hit.article_ref, hit.law_title)
```

### Webhooks

```python
# The secret is returned ONLY on create — store it to verify X-Webhook-Signature
created = client.webhooks.create(
    "New CJEU judgments",
    "https://example.com/webhooks/lexapi",
    {"documentType": "judgment", "author": ["court-of-justice"]},  # /search body shape
)
secret = created.webhook.secret

hooks = client.webhooks.list()
hook = client.webhooks.get(created.webhook.id)          # incl. 20 recent deliveries
client.webhooks.update(hook.webhook.id, status="ACTIVE") # also resets failure counter
result = client.webhooks.test(hook.webhook.id)           # synchronous test delivery
if not result.ok:                                        # HTTP 200 either way
    print(result.delivery.error_message)

page = client.webhooks.deliveries(hook.webhook.id, limit=50)
for delivery in client.webhooks.iter_deliveries(hook.webhook.id):  # walks all pages
    print(delivery.status, delivery.response_status)

client.webhooks.delete(hook.webhook.id)
```

### Corpus export (BUSINESS tier)

`export()` streams NDJSON rows with constant memory; the `_meta` /
`_done` envelopes and export headers are exposed on the stream:

```python
with client.export(document_type="regulation", date_from="2024-01-01", limit=10_000) as stream:
    print(stream.total, stream.streaming)   # X-Export-Total / X-Export-Streaming
    for row in stream:                      # one dict per corpus row
        ingest(row["celex"], row.get("parsedContent"))
    print(stream.meta)                      # leading _meta envelope
    print(stream.done, stream.truncated)    # trailing _done line / row-cap flag

# Async
async with await client.export(fetched_since="2026-06-01T00:00:00Z") as stream:
    async for row in stream:
        ingest(row)
```

### Async

Every operation is also available on the async client:

```python
import asyncio
from lexapi import AsyncLexAPI

async def main():
    async with AsyncLexAPI() as client:  # LEXAPI_API_KEY from the env
        info = await client.get_info()
        print(info["service"])

asyncio.run(main())
```

### Credit visibility

Every response wrapper exposes the pricing-v2 credit envelope without
losing access to the raw body:

```python
info = client.get_info()
info["usage"]            # raw body access still works (Mapping)
info.units_charged       # credits this request cost (0 for /info)
info.credits_remaining   # credits.remaining, falling back to usage.remaining
info.resets_at           # ISO-8601 timestamp of the next quota reset
info.credits             # full typed CreditsInfo (None on legacy daily-call accounts)
```

### Typed errors

All non-2xx responses raise a typed exception mirroring the API's stable
error-code enum (both the typed envelope and the legacy bare `{"error": ...}`
shape are handled):

```python
from lexapi import (
    LexAPIError,          # base — code / message / status / details / body
    NotFoundError,        # 404 NOT_FOUND
    InvalidCelexError,    # 400 INVALID_CELEX
    RateLimitedError,     # 429 RATE_LIMITED — .retry_after (seconds)
    CreditsExhaustedError,# 402 CREDITS_EXHAUSTED — .resets_at
    TierForbiddenError,   # 403 TIER_FORBIDDEN
    UpstreamError,        # 502 UPSTREAM_ERROR
    TimeoutError,         # 504 TIMEOUT (server-side upstream timeout, retry-safe)
    AuthenticationError,  # 401 — missing/invalid/revoked key
)

try:
    client.get_info()
except RateLimitedError as err:
    print(f"rate limited, retry in {err.retry_after}s")
except LexAPIError as err:
    print(err.code, err.status, err.message)
```

### Retries

Idempotent requests are retried automatically (default: up to 3 retries on
429/502/503/504 and connect errors) with exponential backoff + full jitter,
honoring the server's `Retry-After` header when present. Non-idempotent
POSTs are only retried on 429 and connect-phase failures.

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
    timeout=60.0,                   # read/overall seconds (or an httpx.Timeout)
    connect_timeout=10.0,
    user_agent_suffix="myapp/1.0",  # appended to lexapi-python/<version>
)
```

## Point-in-time versions

> Requires a LexAPI deployment with the point-in-time endpoints (lex-api PR #72).
> Versions are LexAPI *observation snapshots* — the document as fetched — not
> legal in-force reconstructions; history begins at first ingestion.

```python
history = client.list_document_versions("32016R0679")
print(history.current_version, history.tracked_since)
for v in history.versions:
    print(v.version, v.fetched_at, v.content_hash, "(current)" if v.is_current else "")

snapshot = client.get_document_version("32016R0679", 2)
print(snapshot.document.parsed_content)

as_of = client.get_document_at_date("32016R0679", "2026-03-20")
print(as_of.as_of, as_of.document.version)
```

Each call costs 1 credit. Dates before the document entered the corpus raise
`NotFoundError` with the tracking start date in the message.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

Docs: <https://lex-api.com/docs>
