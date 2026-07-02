# lexapi (Python)

Official Python SDK for [LexAPI](https://lex-api.com) — European legal data, made queryable. EUR-Lex, CJEU case law, and the Official Journal behind one REST API.

> **Status: pre-release scaffold (0.x).** API coverage and installation instructions below are placeholders until the first PyPI release. See [PLAN.md](PLAN.md).

## Install

```bash
pip install lexapi   # not yet published
```

## Quickstart

```python
from lexapi import LexAPI

client = LexAPI(api_key="lex_...")  # or set LEXAPI_API_KEY
info = client.get_info()
print(info["subscription"]["tier"], info["usage"]["remaining"])
```

Keys come from the [LexAPI dashboard](https://lex-api.com) and are prefixed `lex_`. Keep them server-side — never ship them in client-side code.

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

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

Docs: <https://lex-api.com/docs>
