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

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

Docs: <https://lex-api.com/docs>
