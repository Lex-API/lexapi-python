# lexapi-python — Implementation Plan

Official Python SDK for [LexAPI](https://lex-api.com) (`https://lex-api.com/api/v1`, docs at <https://lex-api.com/docs>). Package name: **`lexapi`** (PyPI). Source of truth: `openapi.yaml` in the LexAPI server repo.

## 1. SDK Contract (shared across all LexAPI SDKs)

This section is identical in `lexapi-python`, `lexapi-typescript`, `lexapi-kotlin`, and `lexapi-swift`. Any change here must be propagated to all four repos in the same release cycle.

Source of truth: `openapi.yaml` (LexAPI 2.0.0, OpenAPI 3.1.0). Production base URL: `https://lex-api.com/api/v1`. Docs: <https://lex-api.com/docs>.

### 1.1 Authentication

- Per the OpenAPI `securitySchemes.ApiKeyAuth`: `type: apiKey`, `in: header`, `name: x-api-key`.
- The SDK sends the key on **every** request as the `x-api-key` header. Keys are prefixed `lex_`.
- Keys are server-side secrets: SDKs must never log the key, never embed it in URLs, and README/quickstart must warn against shipping keys in client-side code (the API explicitly does not support public keys).
- Key is provided at client construction time (constructor arg) with an environment-variable fallback (`LEXAPI_API_KEY`).

### 1.2 Retries and rate limiting

- Default retry policy: up to 3 retries with exponential backoff + full jitter (base 500 ms, cap 30 s).
- Retryable: HTTP 429 (`RATE_LIMITED`), 502 (`UPSTREAM_ERROR`), 504 (`TIMEOUT` — spec marks this retry-safe), and transport-level network errors. Non-retryable: all other 4xx (including 402 `CREDITS_EXHAUSTED` — retrying cannot help until top-up/reset) and 500 `INTERNAL` by default.
- Server-directed wait wins over computed backoff. Resolution order for the wait duration on 429:
  1. `Retry-After` response header (seconds; documented in the spec's `RateLimited` response),
  2. `RateLimit-*` headers (`RateLimit-Remaining`, `RateLimit-Reset` / draft IETF forms) when present,
  3. the JSON `retryAfter` field on the 429 error body, when present,
  4. computed exponential backoff as fallback.
- Retry behaviour is configurable (max attempts, disable entirely). Idempotent GETs retry by default; non-GET calls retry only on 429/network-error-before-send unless the caller opts in.

### 1.3 Typed errors

- All non-2xx responses map to a typed exception/error hierarchy mirroring the API error envelope:

  ```json
  { "success": false, "error": { "code": "NOT_FOUND", "message": "...", "details": { } } }
  ```

- `error.code` is a stable slug enum (from the spec): `NOT_FOUND`, `INVALID_CELEX`, `INVALID_URL`, `INVALID_PARAMS`, `RATE_LIMITED`, `TIER_FORBIDDEN`, `CREDITS_EXHAUSTED`, `UPSTREAM_ERROR`, `TIMEOUT`, `INTERNAL`. SDK error types carry `code`, `message`, optional `details`, and the HTTP status. Unknown codes must not crash the parser (forward compatibility: keep raw code string).
- **Legacy tolerance:** some legacy endpoints return a bare `{ "error": "..." }` body without the `success`/typed envelope. The error parser must accept both shapes: if `error` is an object, read `code`/`message`/`details`; if `error` is a string, use it as `message` and derive `code` from the HTTP status (404→`NOT_FOUND`, 429→`RATE_LIMITED`, 401→unauthorized, etc.).
- 401 (missing/invalid/revoked key) surfaces as a distinct authentication error type.

### 1.4 Pagination helpers

- Search endpoints are page-based (`page`/`maxPages`, tier-bounded per the `subscription` block). SDKs provide an iterator/stream helper that walks pages transparently and stops at the tier cap, surfacing `truncated`/`truncatedAt`/`truncatedReason` and `partial`/`partialReason` flags rather than hiding them.
- `/webhooks/{id}/deliveries` and other list endpoints get the same helper where applicable.

### 1.5 Credit visibility

- Every successful response can include a `usage` block (`current`/`limit`/`remaining`/`unit`/`resetsAt`; dual-mode credits-vs-daily-calls) and, on credit-pool (v2) accounts, a `credits` block (`operation_weight`, `current`, `limit`, `remaining`, `included`, `topup`, `resetsAt`).
- SDKs expose a credit-visibility helper on every response wrapper: `unitsCharged` (mapped from `credits.operation_weight`; 0 for free ops like `/info`), `creditsRemaining` (from `credits.remaining`, falling back to `usage.remaining`), and `resetsAt`. The raw `usage`/`credits` blocks stay accessible untyped-loss-free.
- Helpers must tolerate the blocks being absent (legacy daily-call accounts omit `credits`).

### 1.6 Timeouts

- Defaults: 10 s connect, 60 s read/overall (the API's upstream hard timeout is 90 s; staying under it means the SDK sees the typed 504 rather than cutting the connection). Both configurable per-client and per-call.

### 1.7 User agent

- Every request sends `User-Agent: lexapi-<lang>/<version>` (e.g. `lexapi-python/0.1.0`, `lexapi-typescript/0.1.0`, `lexapi-kotlin/0.1.0`, `lexapi-swift/0.1.0`), with an optional caller-supplied suffix.

## 2. Endpoint coverage matrix

Priority order: **P0** first (`info` + `search` + `documentContent`), then P1 → P3. One group per release increment where practical.

| Status | Group | Operation | Method + Path | Priority |
| --- | --- | --- | --- | --- |
| [x] | user/info | `getInfo` | `GET /info` | **P0** |
| [x] | search | `search` | `POST /search` | **P0** |
| [x] | documents/content | `getDocumentContent` | `POST /documentContent` | **P0** |
| [x] | documents/content | `getDocumentContentBatch` | `POST /documentContent/batch` | P1 |
| [x] | documents/content | `getRecentDocuments` | `GET /documents/recent` | P1 |
| [x] | documents/content | `getDocumentByUrl` | `POST /documents/url` | P1 |
| [x] | documents/content | `getDocumentMetadata` | `POST /documents/metadata` | P1 |
| [x] | resolve | `resolveIdentifier` | `POST /resolve` | P1 |
| [x] | citations | `extractCitations` | `POST /citations/extract` | P2 |
| [x] | citations | `getCitedBy` | `GET /citations/cited-by/{celexNumber}` | P2 |
| [x] | citations | `getCites` | `GET /citations/cites/{celexNumber}` | P2 |
| [x] | citations | `getCitationNetwork` | `GET /citations/network/{celexNumber}` | P2 |
| [x] | citations | `getCitationStats` | `GET /citations/stats` | P2 |
| [x] | citations | `getCitationPath` | `GET /citations/path/{from}/{to}` | P2 |
| [x] | citations | `getRelatedDocuments` | `GET /citations/related/{celexNumber}` | P2 |
| [x] | semantic | `semanticCaseLawSearch` | `POST /search/semantic` | P2 |
| [x] | semantic | `semanticLegislationSearch` | `POST /legislation/semantic` | P2 |
| [x] | export | `exportCorpus` | `GET /export` | P3 |
| [x] | webhooks | `listWebhooks` / `createWebhook` | `GET/POST /webhooks` | P3 |
| [x] | webhooks | `getWebhook` / `updateWebhook` / `deleteWebhook` | `GET/PUT/DELETE /webhooks/{id}` | P3 |
| [x] | webhooks | `testWebhook` | `POST /webhooks/{id}/test` | P3 |
| [x] | webhooks | `getWebhookDeliveries` | `GET /webhooks/{id}/deliveries` | P3 |
| [x] | versions | `listDocumentVersions` | `GET /documents/{celex}/versions` | P3 |
| [x] | versions | `getDocumentVersion` | `GET /documents/{celex}/versions/{version}` | P3 |
| [x] | versions | `getDocumentAtDate` | `GET /documents/{celex}/at/{date}` | P3 |

Cross-cutting model work per group: `ErrorResponse`, `SubscriptionInfo`, `UsageInfo`, `CreditsInfo` (P0); search filter enums driven from `/info` capability map where possible instead of hardcoding.
## 3. Language-specific architecture

- **Layout:** `src/lexapi/` (src layout), built with **hatchling** via `pyproject.toml`.
- **HTTP:** `httpx` — one shared `httpx.Client` per `LexAPI` instance; an `AsyncLexAPI` twin sharing all request-building/parsing code lands with P0 (`httpx` makes sync/async symmetric).
- **Modules:**
  - `lexapi/client.py` — `LexAPI` client, `_request()` core (auth header, UA, retries, timeout), one method per operation grouped by resource namespaces (`client.search(...)`, `client.documents.content(...)`, `client.citations.cited_by(...)`, `client.webhooks.list(...)`).
  - `lexapi/errors.py` — `LexAPIError` base carrying `code/message/details/status`; subclasses per stable code (`NotFoundError`, `RateLimitedError` with `retry_after`, `CreditsExhaustedError`, ...). Tolerates typed envelope and legacy bare `{error}`.
  - `lexapi/models.py` — typed dataclasses/`TypedDict`s for `SubscriptionInfo`, `UsageInfo`, `CreditsInfo`, search hits, `DocumentContent`; generated-then-curated from the OpenAPI schema (no runtime codegen dependency).
  - `lexapi/pagination.py` — `iter_pages()` / `iter_results()` generators honoring tier caps and surfacing `truncated`/`partial`.
  - `lexapi/_meta.py` — version + user-agent string (`lexapi-python/<version>`).
- **Responses:** thin `APIResponse` wrapper exposing `.data`, `.usage`, `.credits`, `.units_charged`, `.credits_remaining`, `.resets_at`.
- **Typing:** fully typed, `py.typed` marker, mypy strict in CI. Python ≥ 3.9.
- **Lint/format:** ruff (lint + format), configured in `pyproject.toml`.

## 4. Testing strategy

- **Unit tests (pytest):** request construction (auth header, UA, timeouts), error-envelope parsing (typed + legacy bare-`{error}` shapes, unknown codes), retry/backoff decisions (`Retry-After`, `RateLimit-*`, JSON `retryAfter` precedence), pagination iteration incl. truncation flags, credit helpers with and without the `credits` block.
- **Mocked HTTP:** `httpx.MockTransport` for hermetic tests (no live sockets, no test-only HTTP server). Recorded fixtures: real (sanitized) response bodies checked into `tests/fixtures/*.json`, replayed through the mock transport; a `respx`-based layer if matching needs grow.
- **Contract tests:** validate fixture bodies against `openapi.yaml` schemas in CI (schemathesis or openapi-core) so drift is caught when the spec changes.
- **Smoke (manual/nightly, not PR-blocking):** `GET /info` against production with a scoped key from CI secrets.
- Coverage gate: 90% on `client.py`/`errors.py`/`pagination.py`.

## 5. Publishing pipeline (PyPI) — plan only, nothing published yet

1. Reserve the `lexapi` name on PyPI (manual first upload of 0.1.0 once ready — **not now**).
2. GitHub Actions release workflow on tag `v*`: build sdist+wheel with `hatch build`, publish via **PyPI Trusted Publishing (OIDC)** — no long-lived tokens.
3. TestPyPI dry-run job on `main` merges.
4. CHANGELOG.md (keep-a-changelog), tag = version, GitHub Release generated from changelog section.
5. Post-publish verification job: `pip install lexapi==<v>` in a clean venv + import smoke test.

## 6. Versioning policy

- **Semver.** `0.x` until the LexAPI v1 surface freeze; during 0.x, minor bumps may break (documented in CHANGELOG), patch bumps never break.
- `1.0.0` is tagged only when: P0+P1 endpoint groups are covered, the shared SDK contract (section 1) is fully implemented, and the API team declares the v1 freeze.
- API additions (new fields/endpoints) → minor. Error-envelope or auth changes → coordinated minor across all four SDKs in the same week.
- Each release records the `openapi.yaml` revision it was generated/validated against.
