# lexapi-python — Implementation Plan

Official Python SDK for [LexAPI](https://lex-api.com) (`https://lex-api.com/api/v1`, docs at <https://lex-api.com/docs>). PyPI distribution: **`lexapi-client`**; import package: **`lexapi`**. Source of truth: `openapi.yaml` in the LexAPI server repo (0.2.0 was synced against lex-api `main` @ `633eb67`, PR #298).

## 1. SDK Contract (shared across all LexAPI SDKs)

This section is the single shared contract for `lexapi-python`, `lexapi-typescript`, `lexapi-kotlin` and `lexapi-swift`, and is copied **verbatim** into each repo's `PLAN.md`. A change here is made in all repos in the same release cycle. Language-specific spellings live only in §1.10.

Source of truth: `openapi.yaml` (LexAPI 2.x, OpenAPI 3.1.0) plus the server code where the spec is silent. Production base URL: `https://lex-api.com/api/v1`. Docs: <https://lex-api.com/docs>. Every rule below that depends on server behaviour cites it, so it can be re-checked when the server changes.

### 1.1 Authentication

- Per the OpenAPI `securitySchemes.ApiKeyAuth`: `type: apiKey`, `in: header`, `name: x-api-key`.
- The SDK sends the key on **every** request as the `x-api-key` header. Keys are prefixed `lex_`.
- Keys are server-side secrets: SDKs never log the key, never put it in URLs or error messages, and the README/quickstart warns against shipping keys in client-side code (the API does not support public keys).
- The key is passed at client construction, with an environment-variable fallback (`LEXAPI_API_KEY`).

### 1.2 Retries — the billing-safety rule

**Rule: an SDK retries automatically only when the retry cannot charge the caller twice.** Everything else is surfaced as a typed error and the caller decides.

Server facts this rule rests on (lex-api `server/`):

- The per-key rate limiter runs **before** credits are charged (`routes/api.js`: `createRateLimiter()` precedes `consumeCredits`). A 429 from the limiter never charged anything.
- Credits are charged when `consumeCredits` admits the request. A failure is refunded only when **the app itself finishes** a 4xx/5xx response (`middleware/credits.js` `scheduleFailureRefund`, `res.on('finish')`): a 5xx is refunded in full, a 404 down to 1 credit, other 4xx in full.
- Therefore **no refund** happens when the connection drops after the request was admitted, or when a **proxy** (Cloudflare, the gateway) answers 502/504/520/524 while the app is still running or has already answered 200. Such a call may have been charged.

Definitions:

- **App-origin response**: the response carries a non-empty `X-LexAPI-Version` header (set on every app response from lex-api #299 on) **or** a `RateLimit-Limit` header. The per-key rate limiter runs with standard headers before any charge on every `/api/v1` route (`middleware/rateLimit.js` `standardHeaders: true`, mounted in `routes/api.js` ahead of `consumeCredits`), so every app response that could have been charged carries `RateLimit-*`; Cloudflare, the gateway and the load balancer never add it to their own error pages. **The body is never evidence of origin** — an intermediary (a Cloudflare rule or Worker, a WAF, a customer-side proxy) can answer JSON with an `error` key. Anything else is **proxy-origin** (unknown origin is never assumed to be the app). App responses produced before the limiter (`validateApiKey` 401/403/500) were never charged and are not retryable statuses anyway.
- **Never reached the app**:
  - transport failures before the request was sent or answered: DNS / connection refused / TLS failure, connect timeout, timeout waiting for a free pooled connection, and a connection closed **before any response byte** (a stale keep-alive socket the server had already dropped). A close **after part of the response arrived** (head received, body incomplete) is a post-send failure, not this. SDKs identify the pre-response close by exception type or connection state where the runtime exposes it, otherwise by the HTTP library's exact pre-response message (httpcore: `"Server disconnected without sending a response"`, matched as a prefix — never a looser substring, since h11's mid-body `"peer closed connection without sending complete message body"` shares words with it). **Runtimes that cannot distinguish a pre-response close from a mid-response close (e.g. `fetch`/undici) MUST treat every such close as a post-send failure: not retryable on billed calls;**
  - Cloudflare **521** (web server down), **522** (connection timed out), **523** (origin unreachable).
- **Free operations** (0 credits): `GET /info`, `GET /webhooks`, `GET /webhooks/{id}`, `GET /webhooks/{id}/deliveries`. (On legacy daily-call plans a successful call counts one call; a retried call that had silently succeeded can count once more.)
- **Read-only POSTs**: `POST /search`, `/documentContent`, `/documentContent/batch`, `/documents/url`, `/documents/metadata`, `/resolve`, `/search/semantic`, `/legislation/semantic`. They change no server state.
- **Mutating calls**: `POST /webhooks`, `PUT/DELETE /webhooks/{id}`, `POST /webhooks/{id}/test` (sends a delivery), `POST /citations/extract` (persists edges).

Decision table (first matching row wins; "GET" means GET/HEAD/OPTIONS):

| Failure | GET, billed | GET, free | Read-only POST | Mutating call |
|---|---|---|---|---|
| 429, not a quota (see below) | retry | retry | retry | retry |
| 429 reporting an exhausted quota | no | no | no | no |
| Never reached the app (transport or 521/522/523) | retry | retry | retry | retry |
| App-origin 502 / 503 / 504 | retry | retry | retry **only with the read-only-POST opt-in** | no |
| Proxy-origin 502 / 503 / 504, 520, 524 | no | retry | no | no |
| Transport error after the request was sent (read error, read timeout, reset mid-response) | no | retry | no | no |
| 400 / 401 / 402 / 403 / 404 / 500 and anything else | no | no | no | no |

- **Quota 429**: legacy daily-call plans (`"Daily quota exceeded"`) and the legacy monthly semantic quota answer 429 with a `usage` object carrying `resetsAt`, and with neither a `Retry-After` header nor a `retryAfter` body field. It cannot succeed before `resetsAt`, so it is not retried, and the error's retry-after value is **null** (the `RateLimit-Reset` header on it describes the per-minute window, not the quota). The error exposes `resetsAt` and a quota-exhausted flag.
- **Opt-in: read-only POSTs.** A boolean option, default **off**, that adds the "app-origin 502/503/504 on a read-only POST" row. Those responses are refunded in full, so the retry is billing-safe. It never applies to mutating calls and never to proxy-origin or unknown-origin responses.
- **Power-user override** (optional per SDK): a set of statuses retried for every non-GET call regardless of origin. If an SDK offers it, its docs must say it can duplicate webhooks/test deliveries and double-charge. Default: `{429}`.
- **Policy defaults:** up to 3 retries after the first attempt; exponential backoff with full jitter, `U(0, min(cap, base × 2^attempt))`, base 0.5 s, cap 30 s; `max retries = 0` disables retrying.
- **Wait before a retry** (server-directed wins over backoff; every hint is clamped to the 30 s cap):
  1. `Retry-After` header, on any status: delta-seconds or an HTTP-date. An empty or unparseable value counts as absent.
  2. On a 429 only: `RateLimit-Reset` (IETF draft-6, delta-seconds). LexAPI sends `RateLimit-Policy/-Limit/-Remaining/-Reset` on every `/api/v1` response, so on any other status it describes the rate window and must not drive the wait. `X-RateLimit-*` is never sent (the limiter's legacy headers are disabled) and is not read.
  3. On a 429 only: the JSON `retryAfter` body field.
  4. Computed backoff.
- **Numeric hints** are delta-seconds. Defensively, a value above **1e12** is read as epoch milliseconds and a value above **1e9** as epoch seconds, and either is converted to a delta (floored at 0). Values up to 1e9 are taken as delta-seconds.
- The retry-after value exposed on an error follows the same resolution (steps 1–3), and is null for a quota 429.

### 1.3 Typed errors

- Every non-2xx response raises/returns a typed error carrying `code`, `message`, `status`, optional `details`, the raw decoded `body`, and `retryAfter` (§1.2).
- **Typed envelope** `{"success": false, "error": {"code", "message", "details"}}`: `error.code` is authoritative. Codes from the spec: `NOT_FOUND`, `INVALID_CELEX`, `INVALID_URL`, `INVALID_PARAMS`, `RATE_LIMITED`, `TIER_FORBIDDEN`, `CREDITS_EXHAUSTED`, `UPSTREAM_ERROR`, `TIMEOUT`, `INTERNAL`. An unknown code never crashes the parser: keep the raw string on the base error type.
- **Legacy shape** `{"error": "<text>", "message"?: "...", "details"?: ...}` (API-key 401s, every 429, search/batch/webhook validation 400s, the `/export` and legacy-semantic tier gates, semantic upstream 502/504, webhook 404/500): the string `error` is **human text, never a code** — even when it happens to equal a slug. `message` = the `error` text, followed by `": " + message` when `message` is present and different. The code comes from the HTTP status (table below).
- **`details`**: `error.details` on the typed envelope; otherwise the top-level `details` member (validation errors put a list of reasons there), including on bodies that have no `error` member at all (`{"message", "details"}`).
- **No usable body** (non-JSON, empty): `message` = `"HTTP <status>"`, code from the status.
- **Status → code** when the body has no typed code:

  | Status | Code |
  |---|---|
  | 400 | `INVALID_PARAMS` |
  | 401 | `UNAUTHORIZED` (SDK-side; distinct authentication error type) |
  | 402 | `CREDITS_EXHAUSTED` |
  | 403 | `TIER_FORBIDDEN` |
  | 404 | `NOT_FOUND` |
  | 429 | `RATE_LIMITED` |
  | 500 | `INTERNAL` |
  | 502, 520, 521, 522, 523 | `UPSTREAM_ERROR` |
  | 503 | `SERVICE_UNAVAILABLE` (SDK-side; the API never sends this code — its 503s are drain/health answers and proxy pages) |
  | 504, 524 | `TIMEOUT` |
  | anything else | `INTERNAL` |

- **SDK-side codes** (never in `error.code`): `UNAUTHORIZED`, `SERVICE_UNAVAILABLE`, `INVALID_RESPONSE` (a 2xx whose non-empty body is not JSON — e.g. a proxy's HTML interstitial; never turned into an empty success), `INCOMPLETE_EXPORT` (§1.8), `CONNECTION_ERROR` (transport failure), `REQUEST_TIMEOUT` (the SDK's own timeout, distinct from the server's `TIMEOUT`).
- A 204 or an empty 2xx body is a successful empty object.
- **Typed extras:** `CREDITS_EXHAUSTED` exposes `details.operation`, `weight`, `available`, `included`, `topup`, `used`, `resetsAt`. `RATE_LIMITED` exposes the quota flag and `usage.resetsAt`. Legacy tier gates expose `upgradeUrl`.

### 1.4 Pagination helpers

- `/search` has no page cursor — only `maxPages` (fetch the first N pages, tier-bounded) — so there is no search iterator. SDKs surface `truncated`/`truncatedAt`/`truncatedReason` and `partial`/`partialReason` instead of hiding them.
- Offset-paginated list endpoints get an iterator. None has a stable cursor, so each iterator documents its limits:
  - `/citations/cites/{celex}` and `/citations/cited-by/{celex}`: offsets count **edge rows**; advance by the sum of the page's `citationCount`s and stop at the first page shorter than the page size (never compare offsets with `totalCitations`, which counts distinct document pairs). Edges are ordered by extraction time only, so edges extracted at the same instant or during the walk can shift: an edge may repeat or be missed. Every page is a billed call.
  - `/webhooks/{id}/deliveries` (newest first): yield each delivery at most once (dedupe on `id`), because a delivery created during the walk shifts later pages by one; stop on an empty page or when the offset reaches `total`.

### 1.5 Credit visibility

- Successful responses can carry a `usage` block (`current`/`limit`/`remaining`/`unit`/`resetsAt`; credits on v2 plans, daily calls on legacy plans) and, on credit-pool (v2) accounts, a `credits` block (`operation_weight`, `current`, `limit`, `remaining`, `included`, `topup`, `resetsAt`).
- Every response wrapper exposes `unitsCharged` (`credits.operation_weight`; 0 for free ops and legacy accounts), `creditsRemaining` (`credits.remaining`, falling back to `usage.remaining`) and `resetsAt`. The raw blocks stay accessible without loss.
- `operation_weight` already reflects a settlement the handler applied before answering (corpus read 1, Cellar 4, HyDE fallback 5). Failure refunds land after the response is sent, so an error's own figures show the pre-refund pool and the next call reflects it.
- Helpers tolerate either block being absent.

### 1.6 Timeouts

One model, per attempt:

- **Connect timeout** `C` (default **10 s**): DNS + TCP + TLS, plus the wait for a free pooled connection.
- **Response timeout** `R` (default **95 s**): an **inactivity** limit — the longest the SDK waits for the next bytes of the response: from the end of sending the request until the response head arrives, and then between consecutive body chunks (the timer restarts on every chunk). It applies to buffered and streaming (export) bodies alike. It is not a deadline for the whole call.
- There is no whole-call deadline across retries; worst case is about `(retries + 1) × (C + R)` plus backoff.
- A runtime that cannot time the connect phase separately (e.g. `fetch`) counts it inside the first `R` window; it must still restart the timer on each body chunk.
- Both values are configurable per client and per call.
- Why 95 s: the server's budget for **one** upstream EUR-Lex fetch is 90 s (`FETCH_HARD_TIMEOUT`), so that common timeout reaches the caller as the server's typed, refunded 504; Cloudflare ends any origin wait at 100 s with a 524. A request that needs several upstream fetches (multi-page search, browser page acquisition) can still exceed 95 s and surface as `REQUEST_TIMEOUT` or a 524. On the public endpoint a value above ~100 s buys nothing.

### 1.7 User agent

- Every request sends `User-Agent: lexapi-<lang>/<version>` (e.g. `lexapi-python/0.2.0`, `lexapi-typescript/0.2.0`), with an optional caller-supplied suffix after a space.

### 1.8 Export stream (`GET /export`, NDJSON)

- Wire format: a leading `{"_meta": {...}}` line, one document per line, a trailing `{"_done": {"written", "truncated", "skippedStubs"}}` line. Tolerate the flat `{"_meta": true, ...}` form too.
- A transport failure while reading rows surfaces as the SDK's `CONNECTION_ERROR` / `REQUEST_TIMEOUT` error, never a raw HTTP-library exception.
- A body that ends without its `_done` line raises `INCOMPLETE_EXPORT` **after** yielding the rows that did arrive: a cut-short export never looks complete. The stream exposes a `complete` flag and a count of rows read.
- One behaviour after the caller ends a stream: iterating a stream whose `_done` trailer was never read raises `INCOMPLETE_EXPORT`, however it was ended (closed before iterating, `break` and iterate again, leaving the `with`/`using` block early). Iterating a stream that was read to its `_done` again yields nothing and does not raise. A stream is read once; call `export()` again to re-read.
- Lines that are not JSON objects (e.g. a half-written last line) are counted (`invalidLines`), not silently dropped.
- `truncated` (the row cap was hit) is read from the `X-Export-Truncated` header (`1`/`0`, cap-only) first, then `_meta.truncated`. The `_done.truncated` flag is a last resort and is discounted by `skippedStubs`, because the server sets it to `written < total`, where `total` includes metadata-only rows that `includeStubs=false` then skips.
- Credit cost of an export: SDKs do not state a per-row or per-call price in their docs until the API side settles it; they point to the pricing page and the response's credit fields.

### 1.9 Webhook signature verification

- `X-Webhook-Signature` is the lower-case hex HMAC-SHA256 of the **raw request body bytes**, keyed with the webhook secret (UTF-8). Verify against the bytes as received, never re-serialised JSON.
- Comparison is constant-time. A missing, empty, non-ASCII or otherwise non-hex header is a plain verification failure (`false` / the SDK's signature error), never an unrelated exception.
- The combined helper also enforces a replay window on the signed `timestamp` (default 300 s, configurable or disableable) and returns the parsed event. Receivers dedupe on `delivery.id`, which is stable across retries (also sent as `X-Webhook-Delivery`).

### 1.10 Language-specific names

Names marked *(to add)* do not exist yet in that SDK; when it implements the rule it uses that name.

| Concept | Python (`lexapi-client`) | TypeScript (`@lexapi/client`) |
|---|---|---|
| Read-only-POST opt-in (§1.2) | `RetryConfig(retry_read_only_posts=True)` | `retry.retryReadOnlyPosts: true` |
| Power-user non-GET status override (§1.2) | `RetryConfig.retry_statuses_non_idempotent` | not offered |
| Max retries | `max_retries=` / `RetryConfig.max_retries` | `retry.maxRetries` |
| Timeouts (§1.6) | `timeout=` (R) and `connect_timeout=` (C); per call `client.with_options(...)` | `timeoutMs` (R, connect included); per call `{ timeoutMs }` |
| 503 error type (§1.3) | `ServiceUnavailableError` (subclass of `UpstreamError` for 0.1.x compatibility) | `ServiceUnavailableError` |
| Transport error types | `LexAPIConnectionError`, `RequestTimeoutError` | `NetworkError`, `TimeoutError` with status 0 |
| 2xx non-JSON (§1.3) | `InvalidResponseError` | `InvalidResponseError` *(to add)* |
| Export completeness (§1.8) | `IncompleteExportError`; `stream.complete`, `stream.rows_read`, `stream.invalid_lines` | `IncompleteExportError`; `stream.complete`, `stream.rowsRead`, `stream.invalidLines` *(to add)* |
| Webhook helpers (§1.9) | `verify_webhook`, `verify_signature`, `compute_signature`, `WebhookSignatureError` | `verifyWebhook`, `verifySignature`, `computeSignature`, `WebhookSignatureError` *(to add)* |

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
| [x] | semantic | `semanticLegislationSearch` (incl. `asOf`, consolidated labels) | `POST /legislation/semantic` | P2 |
| [x] | export | `exportCorpus` | `GET /export` | P3 |
| [x] | webhooks | `listWebhooks` / `createWebhook` | `GET/POST /webhooks` | P3 |
| [x] | webhooks | `getWebhook` / `updateWebhook` / `deleteWebhook` | `GET/PUT/DELETE /webhooks/{id}` | P3 |
| [x] | webhooks | `testWebhook` | `POST /webhooks/{id}/test` | P3 |
| [x] | webhooks | `getWebhookDeliveries` | `GET /webhooks/{id}/deliveries` | P3 |
| [x] | versions | `listDocumentVersions` | `GET /documents/{celex}/versions` | P3 |
| [x] | versions | `getDocumentVersion` | `GET /documents/{celex}/versions/{version}` | P3 |
| [x] | versions | `getDocumentAtDate` | `GET /documents/{celex}/at/{date}` | P3 |
| [x] | helpers | `iterCitedBy` / `iterCites` / `iterDeliveries` | offset pagination over the list endpoints | P3 |
| [x] | helpers | `verifyWebhook` | HMAC-SHA256 `X-Webhook-Signature` + replay window | P3 |

Deliberately **not** covered: the national-law routes (`/{jurisdiction}/documents…`, `/{jurisdiction}/search/semantic`, `/{jurisdiction}/legislation/semantic`) — dark until launch and absent from `openapi.yaml`; the deprecated `POST /parse` alias of `/search`; `/corpus/*` (feed-key, internal).

Cross-cutting model work per group: `ErrorResponse`, `SubscriptionInfo`, `UsageInfo`, `CreditsInfo` (P0); search filter enums driven from `/info` capability map where possible instead of hardcoding.
## 3. Language-specific architecture

- **Layout:** `src/lexapi/` (src layout), built with **hatchling** via `pyproject.toml`.
- **HTTP:** `httpx` — one shared `httpx.Client` per `LexAPI` instance; an `AsyncLexAPI` twin sharing all request-building/parsing code lands with P0 (`httpx` makes sync/async symmetric).
- **Modules:**
  - `lexapi/client.py` — `LexAPI` client, `_request()` core (auth header, UA, retries, timeout), one method per operation grouped by resource namespaces (`client.search(...)`, `client.documents.content(...)`, `client.citations.cited_by(...)`, `client.webhooks.list(...)`).
  - `lexapi/errors.py` — `LexAPIError` base carrying `code/message/details/status`; subclasses per stable code (`NotFoundError`, `RateLimitedError` with `retry_after`, `CreditsExhaustedError`, ...). Tolerates typed envelope and legacy bare `{error}`.
  - `lexapi/models.py` — typed dataclasses/`TypedDict`s for `SubscriptionInfo`, `UsageInfo`, `CreditsInfo`, search hits, `DocumentContent`; generated-then-curated from the OpenAPI schema (no runtime codegen dependency).
  - Pagination lives on the resources (`citations.iter_cited_by` / `iter_cites`, `webhooks.iter_deliveries`). `/search` has no page cursor — `maxPages` is the whole request — so there is no search iterator; capping is surfaced via `truncated`/`partial`.
  - `lexapi/_meta.py` — version + user-agent string (`lexapi-python/<version>`).
- **Responses:** thin `APIResponse` wrapper exposing `.data`, `.usage`, `.credits`, `.units_charged`, `.credits_remaining`, `.resets_at`.
- **Typing:** fully typed, `py.typed` marker, `mypy --strict` (configured in `pyproject.toml`) in CI. Python ≥ 3.9.
- **Lint/format:** ruff — `ruff check` and `ruff format --check` both run in CI; configured in `pyproject.toml`.

## 4. Testing strategy

- **Unit tests (pytest):** request construction (auth header, UA, timeouts), error-envelope parsing (typed + legacy bare-`{error}` shapes, unknown codes), retry/backoff decisions (`Retry-After`, `RateLimit-*`, JSON `retryAfter` precedence), pagination iteration incl. truncation flags, credit helpers with and without the `credits` block.
- **Mocked HTTP:** `httpx.MockTransport` for hermetic tests (no live sockets, no test-only HTTP server). Recorded fixtures: real (sanitized) response bodies checked into `tests/fixtures/*.json`, replayed through the mock transport; a `respx`-based layer if matching needs grow.
- **Contract tests:** validate fixture bodies against `openapi.yaml` schemas in CI (schemathesis or openapi-core) so drift is caught when the spec changes.
- **Smoke (manual/nightly, not PR-blocking):** `GET /info` against production with a scoped key from CI secrets.
- Coverage gate (target, not yet enforced in CI): 90% on `client.py`/`errors.py`/`_retry.py`.

## 5. Publishing pipeline (PyPI) — plan only, nothing published yet

1. ~~Reserve the name on PyPI~~ — `lexapi-client` 0.1.0 was uploaded manually on 2026-07-02.
2. GitHub Actions release workflow on tag `v*`: build sdist+wheel with `hatch build`, publish via **PyPI Trusted Publishing (OIDC)** — no long-lived tokens.
3. TestPyPI dry-run job on `main` merges.
4. CHANGELOG.md (keep-a-changelog), tag = version, GitHub Release generated from changelog section.
5. Post-publish verification job: `pip install lexapi-client==<v>` in a clean venv + import smoke test.

## 6. Versioning policy

- **Semver.** `0.x` until the LexAPI v1 surface freeze; during 0.x, minor bumps may break (documented in CHANGELOG), patch bumps never break.
- `1.0.0` is tagged only when: P0+P1 endpoint groups are covered, the shared SDK contract (section 1) is fully implemented, and the API team declares the v1 freeze.
- API additions (new fields/endpoints) → minor. Error-envelope or auth changes → coordinated minor across all four SDKs in the same week.
- Each release records the `openapi.yaml` revision it was generated/validated against.
