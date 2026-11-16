# lexapi (Python)

Official Python SDK for [LexAPI](https://lex-api.com/?ref=pypi-sdk) — European legal data, made queryable. EUR-Lex, CJEU case law, national law and the Official Journal behind one REST API.

Covers every documented `/api/v1` endpoint, sync and async, with typed responses, typed errors and retries. Python 3.9+; `httpx` is the only dependency. Full API reference: [lex-api.com/docs](https://lex-api.com/docs?ref=pypi-sdk). See also the [CHANGELOG](https://github.com/Lex-API/lexapi-python/blob/main/CHANGELOG.md).

## Install

```bash
pip install lexapi-client
```

The import is `lexapi`: `from lexapi import LexAPI`.

## Quickstart

```python
from lexapi import LexAPI

client = LexAPI(api_key="lex_...")  # or set LEXAPI_API_KEY
info = client.get_info()  # free (0 credits)
print(info.subscription_info.tier, info.credits_remaining)
```

Keys come from the [LexAPI dashboard](https://lex-api.com/dashboard?ref=pypi-sdk) and are prefixed `lex_`. Keep them server-side.

### Search and documents

```python
results = client.search("cybersecurity", author=["court-of-justice"], year=2024, document_type="judgment")
for hit in results.results:
    print(hit.celex, hit.document_type_code, hit.title)

resp = client.get_document("32016R0679", language="fr")
doc = resp.document
for article in doc.content.articles[:3]:
    print(article.number, article.title)

# Only metadata + one article
resp = client.get_document("32016R0679", include=["metadata", "articles"], article_id="17")

batch = client.get_documents_batch(["32016R0679", "62018CJ0311"])
meta = client.get_document_metadata("32016R0679").metadata
celex = client.resolve("ECLI:EU:C:2020:559").celex  # CELEX/URL/ELI/ECLI → CELEX
```

Typed responses stay mapping-compatible (`results["totalResults"]` works) and every typed model keeps the raw payload on `.raw`.

### Semantic search

```python
resp = client.semantic_search("transfer of personal data to third countries", limit=10)
for hit in resp.results:
    print(f"{hit.score:.2f}", hit.celex, hit.case_number, hit.case_name)

laws = client.semantic_legislation_search("right to be forgotten", limit=5)
for hit in laws.results:
    print(hit.law_id, hit.article_ref, hit.law_title)
    if hit.consolidated:  # consolidated text: cite the authentic act
        print("  cite", hit.base_celex)

# The law as in force on a past date
laws = client.semantic_legislation_search("own funds requirements", as_of="2019-06-01")
```

### Citations

```python
client.citations.extract("32016R0679")
inbound = client.citations.cited_by("32008R1272", citation_type="corrigendum", limit=50)
outbound = client.citations.cites("32016R0679")
network = client.citations.network("32016R0679", limit=100)
path = client.citations.path("32016R0679", "31995L0046", max_depth=4)
related = client.citations.related("32016R0679", limit=10)

measures = client.citations.transposition("32016L0680", jurisdictions=["es", "cz"])
print(measures.note)
```

A notified measure (`transposition`) is not a finding that the measure transposes the act. Keep `source_cellar.attribution` with anything you republish.

### National law

```python
coverage = client.national.coverage()  # free
doc = client.national.get_document("AT", "AT-NOR-10001597").document
print(doc.title, doc.attribution)
hits = client.national.semantic_search("AT", "data breach notification duty")
```

Every national result carries `attribution` (the official source and its licence); LexAPI's Terms require it to be kept with republished content.

### Webhooks

```python
created = client.webhooks.create(
    "Chemicals compliance watch",
    "https://example.com/webhooks/lexapi",
    {"documentType": "regulation", "query": "chemical"},
    watch_celex=["32006R1907", "32008R1272"],
)
secret = created.webhook.secret  # returned only on create
```

Verifying a delivery (pass the **raw** request body bytes):

```python
from lexapi import WebhookSignatureError, verify_webhook

try:
    event = verify_webhook(raw_body, signature, secret)
except WebhookSignatureError:
    ...  # reject
```

### Corpus export (BUSINESS, SCALE, ENTERPRISE)

```python
with client.export(document_type_code="regulation", date_from="2024-01-01", limit=10_000) as stream:
    for row in stream:  # one dict per corpus row
        ingest(row["celex"], row.get("parsedContent"))
```

Using LexAPI content to train, fine-tune or evaluate ML models needs a separate licence ([Terms §6](https://lex-api.com/terms?ref=pypi-sdk#acceptable-use)). [Arieta Data](https://data.arietalabs.com/?ref=pypi-sdk), a sister product from Arieta Labs, licenses datasets drawn from the same corpus.

### Async

```python
import asyncio
from lexapi import AsyncLexAPI


async def main():
    async with AsyncLexAPI() as client:  # LEXAPI_API_KEY from the env
        info = await client.get_info()


asyncio.run(main())
```

### Errors, retries and configuration

```python
from lexapi import LexAPI, LexAPIError, CreditsExhaustedError, RateLimitedError, RetryConfig

try:
    client.semantic_search("credit scoring")
except CreditsExhaustedError as err:
    print(f"need {err.weight}, have {err.available}; resets {err.resets_at}")
except RateLimitedError as err:
    print(f"rate limited, retry in {err.retry_after}s")
except LexAPIError as err:
    print(err.code, err.status, err.message)

client = LexAPI(
    api_key="lex_...",
    timeout=95.0,
    retry_config=RetryConfig(max_retries=2),
)
```

Retries are automatic only where a retry cannot charge you twice. Every response exposes `units_charged`, `credits_remaining` and `resets_at`.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy
```

## License

MIT
