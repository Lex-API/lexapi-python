# SDK contract

How the official LexAPI SDKs (`lexapi-client` for Python, `@lexapi/client` for TypeScript) behave against the [LexAPI](https://lex-api.com) API. Both SDKs follow the same rules; language-specific names are in §1.10. API reference: <https://lex-api.com/docs>.

Sections are numbered §1.1–§1.10. The changelog and code comments cite them by number.

## 1. Contract

### 1.1 Authentication

- The API key is sent on **every** request as the `x-api-key` header. Keys are prefixed `lex_`.
- Keys are server-side secrets: the SDKs never log the key and never put it in URLs or error messages. Do not ship a key in client-side code (the API has no public keys).
- The key is passed at client construction, with an environment-variable fallback (`LEXAPI_API_KEY`).

### 1.2 Retries — the billing-safety rule

**Rule: an SDK retries automatically only when the retry cannot charge the caller twice.** Everything else is surfaced as a typed error and the caller decides.

API behaviour this rule rests on:

- The per-key rate limiter runs **before** credits are charged. A 429 from the limiter never charged anything.
- Credits are charged when the request is admitted. A failure is refunded only when **the API itself** answers with a 4xx/5xx: a 5xx is refunded in full, a 404 down to 1 credit, other 4xx in full.
- Therefore **no refund** happens when the connection drops after the request was admitted, or when a **proxy** in front of the API answers 502/504/520/524 while the API is still working or has already answered 200. Such a call may have been charged.

Definitions:

- **App-origin response**: the response carries a non-empty `X-LexAPI-Version` header (sent on every response the API itself produces, from API 2.1.0 on) **or** a `RateLimit-Limit` header. The rate limiter adds `RateLimit-*` headers before any charge on every `/api/v1` route, so every API response that could have been charged carries them; proxies never add them to their own error pages. **The body is never evidence of origin** — an intermediary (a CDN rule, a WAF, a proxy on the caller's side) can answer JSON with an `error` key. Anything else is **proxy-origin** (unknown origin is never assumed to be the API). API responses produced before the limiter (API-key 401/403/500) were never charged and are not retryable statuses anyway.
- **Never reached the app**:
  - transport failures before the request was sent or answered: DNS / connection refused / TLS failure, connect timeout, timeout waiting for a free pooled connection, and a connection closed **before any response byte** (a stale keep-alive socket the server had already dropped). A close **after part of the response arrived** (head received, body incomplete) is a post-send failure, not this. An SDK identifies the pre-response close by exception type or connection state where the runtime exposes it, otherwise by the HTTP library's exact pre-response message. **A runtime that cannot distinguish a pre-response close from a mid-response close (e.g. `fetch`) treats every such close as a post-send failure: not retryable on billed calls;**
  - Cloudflare **521** (web server down), **522** (connection timed out), **523** (origin unreachable).
- **Free operations** (0 credits): `GET /info`, `GET /webhooks`, `GET /webhooks/{id}`, `GET /webhooks/{id}/deliveries`, `GET /national/coverage`. (On legacy daily-call plans a successful call counts one call; a retried call that had silently succeeded can count once more.)
- **Read-only POSTs**: `POST /search`, `/documentContent`, `/documentContent/batch`, `/documents/url`, `/documents/metadata`, `/resolve`, `/search/semantic`, `/legislation/semantic`, `/{jurisdiction}/search/semantic`, `/{jurisdiction}/legislation/semantic`. They change no server state.
- **Mutating calls**: `POST /webhooks`, `PUT/DELETE /webhooks/{id}`, `POST /webhooks/{id}/test` (sends a delivery), `POST /citations/extract` (persists edges).

Decision table (first matching row wins; "GET" means GET/HEAD/OPTIONS):

| Failure | GET, billed | GET, free | Read-only POST | Mutating call |
|---|---|---|---|---|
| 503 with `partialReason: "TIMEOUT"` | no | no | no | no |
| 429, not a quota (see below) | retry | retry | retry | retry |
| 429 reporting an exhausted quota | no | no | no | no |
| Never reached the app (transport or 521/522/523) | retry | retry | retry | retry |
| App-origin 502 / 503 / 504 | retry | retry | retry **only with the read-only-POST opt-in** | no |
| Proxy-origin 502 / 503 / 504, 520, 524 | no | retry | no | no |
| Transport error after the request was sent (read error, read timeout, reset mid-response) | no | retry | no | no |
| 400 / 401 / 402 / 403 / 404 / 500 and anything else | no | no | no | no |

- **Quota 429**: legacy daily-call plans (`"Daily quota exceeded"`) and the legacy monthly semantic quota answer 429 when the quota is used up. From API 2.4.0 on, the body carries a top-level `"code": "QUOTA_EXHAUSTED"` and a top-level `resetsAt` (ISO 8601), next to the legacy string `error` and the `usage` object; that `code` identifies it, whatever else the response carries. Earlier API versions send no `code`: then a 429 is a quota 429 when its body has a `usage` object with `resetsAt` and there is neither a `Retry-After` header nor a `retryAfter` body field. A quota 429 cannot succeed before `resetsAt`, so it is not retried, and the error's retry-after value is **null** (the `RateLimit-Reset` header on it describes the per-minute window, not the quota). The error exposes `resetsAt` (top-level `resetsAt`, else `usage.resetsAt`) and a quota-exhausted flag.
- **503 with `partialReason: "TIMEOUT"`**: the request ran out of its server-side time budget; a retry would wait the same budget again, so it is never retried.
- **Opt-in: read-only POSTs.** A boolean option, default **off**, that adds the "app-origin 502/503/504 on a read-only POST" row. Those responses are refunded in full, so the retry is billing-safe. It never applies to mutating calls and never to proxy-origin or unknown-origin responses.
- **Power-user override** (optional per SDK): a set of statuses retried for every non-GET call regardless of origin. It can duplicate webhooks/test deliveries and double-charge. Default: `{429}`.
- **Policy defaults:** up to 3 retries after the first attempt; exponential backoff with full jitter, `U(0, min(cap, base × 2^attempt))`, base 0.5 s, cap 30 s; `max retries = 0` disables retrying.
- **Wait before a retry** (server-directed wins over backoff; every hint is clamped to the 30 s cap):
  1. `Retry-After` header, on any status: delta-seconds or an HTTP-date. An empty or unparseable value counts as absent.
  2. On a 429 only: `RateLimit-Reset` (IETF draft-6, delta-seconds). LexAPI sends `RateLimit-Policy/-Limit/-Remaining/-Reset` on every `/api/v1` response, so on any other status it describes the rate window and must not drive the wait. `X-RateLimit-*` is never sent and is not read.
  3. On a 429 and on a 503 `NATIONAL_SEMANTIC_UNAVAILABLE` only: the JSON `retryAfter` body field.
  4. Computed backoff.
- **Numeric hints** are delta-seconds. Defensively, a value above **1e12** is read as epoch milliseconds and a value above **1e9** as epoch seconds, and either is converted to a delta (floored at 0). Values up to 1e9 are taken as delta-seconds.
- The retry-after value exposed on an error follows the same resolution (steps 1–3), and is null for a quota 429.

### 1.3 Typed errors

- Every non-2xx response raises/returns a typed error carrying `code`, `message`, `status`, optional `details`, the raw decoded `body`, and `retryAfter` (§1.2).
- **Typed envelope** `{"success": false, "error": {"code", "message", "details"}}`: `error.code` is authoritative. Codes from the API reference: `NOT_FOUND`, `INVALID_CELEX`, `INVALID_URL`, `INVALID_PARAMS`, `RATE_LIMITED`, `QUOTA_EXHAUSTED`, `TIER_FORBIDDEN`, `CREDITS_EXHAUSTED`, `UPSTREAM_ERROR`, `TIMEOUT`, `INTERNAL`. An unknown code never crashes the parser: any code not listed here is preserved as the raw string on the error.
- **Legacy shape** `{"error": "<text>", "message"?: "...", "details"?: ...}` (API-key 401s, every 429, some validation 400s, some tier gates, some 502/504 and webhook 404/500 answers): the string `error` is **human text, never a code** — even when it happens to equal a slug. `message` = the `error` text, followed by `": " + message` when `message` is present and different. The code is the body's top-level `code` when it is a string (API 2.4.0 adds it to legacy-shape bodies, e.g. `RATE_LIMITED`, `QUOTA_EXHAUSTED`); it takes precedence over the status-derived code. Without one, the code comes from the HTTP status (table below).
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
  | 503 | `SERVICE_UNAVAILABLE` (SDK-side; the API never sends this code) |
  | 504, 524 | `TIMEOUT` |
  | anything else | `INTERNAL` |

- **SDK-side codes** (never in `error.code`): `UNAUTHORIZED`, `SERVICE_UNAVAILABLE`, `INVALID_RESPONSE` (a 2xx whose non-empty body is not JSON — e.g. a proxy's HTML interstitial; never turned into an empty success), `INCOMPLETE_EXPORT` (§1.8), `CONNECTION_ERROR` (transport failure), `REQUEST_TIMEOUT` (the SDK's own timeout, distinct from the server's `TIMEOUT`).
- A 204 or an empty 2xx body is a successful empty object.
- **Typed extras:** `CREDITS_EXHAUSTED` exposes `details.operation`, `weight`, `available`, `included`, `topup`, `used`, `resetsAt`. `RATE_LIMITED` and `QUOTA_EXHAUSTED` share the rate-limit error type (its `code` tells them apart), which exposes the quota flag and `resetsAt` (§1.2). Legacy tier gates expose `upgradeUrl`.

### 1.4 Pagination helpers

- `/search` has no page cursor — only `maxPages` (fetch the first N pages, tier-bounded) — so there is no search iterator. The SDKs surface `truncated`/`truncatedAt`/`truncatedReason` and `partial`/`partialReason` instead of hiding them.
- Offset-paginated list endpoints get an iterator. None has a stable cursor, so each iterator documents its limits:
  - `/citations/cites/{celex}` and `/citations/cited-by/{celex}`: offsets count **edge rows**; the iterator advances by the sum of the page's `citationCount`s and stops at the first page shorter than the page size (it never compares offsets with `totalCitations`, which counts distinct document pairs). Edges are ordered by extraction time only, so edges extracted at the same instant or during the walk can shift: an edge may repeat or be missed. Every page is a billed call.
  - `/webhooks/{id}/deliveries` (newest first): each delivery is yielded at most once (deduplicated on `id`), because a delivery created during the walk shifts later pages by one; the iterator stops on an empty page or when the offset reaches `total`.

### 1.5 Credit visibility

- Successful responses can carry a `usage` block (`current`/`limit`/`remaining`/`unit`/`resetsAt`; credits on credit plans, daily calls on legacy plans) and, on credit-pool accounts, a `credits` block (`operation_weight`, `current`, `limit`, `remaining`, `included`, `topup`, `resetsAt`).
- Every response wrapper exposes `unitsCharged` (`credits.operation_weight`; 0 for free operations and legacy accounts), `creditsRemaining` (`credits.remaining`, falling back to `usage.remaining`) and `resetsAt`. The raw blocks stay accessible without loss.
- `operation_weight` already reflects a settlement applied before the answer (for example a document served from the LexAPI corpus settles at 1 credit). Failure refunds land after the response is sent, so an error's own figures show the pre-refund pool and the next call reflects it.
- Helpers tolerate either block being absent.

### 1.6 Timeouts

One model, per attempt:

- **Connect timeout** `C` (default **10 s**): DNS + TCP + TLS, plus the wait for a free pooled connection.
- **Response timeout** `R` (default **95 s**): an **inactivity** limit — the longest the SDK waits for the next bytes of the response: from the end of sending the request until the response head arrives, and then between consecutive body chunks (the timer restarts on every chunk). It applies to buffered and streaming (export) bodies alike. It is not a deadline for the whole call.
- There is no whole-call deadline across retries; worst case is about `(retries + 1) × (C + R)` plus backoff.
- A runtime that cannot time the connect phase separately (e.g. `fetch`) counts it inside the first `R` window; it still restarts the timer on each body chunk.
- Both values are configurable per client and per call.
- Why 95 s: it sits above the API's 90 s request deadline, so that timeout reaches the caller as the server's typed, refunded 504; Cloudflare ends any wait at 100 s with a 524. Some requests can still exceed 95 s and surface as `REQUEST_TIMEOUT` or a 524. On the public endpoint a value above ~100 s buys nothing.

### 1.7 User agent

- Every request sends `User-Agent: lexapi-<lang>/<version>` (e.g. `lexapi-python/0.2.0`, `lexapi-typescript/0.2.0`), with an optional caller-supplied suffix after a space.

### 1.8 Export stream (`GET /export`, NDJSON)

- Wire format: a leading `{"_meta": {...}}` line, one document per line, a trailing `{"_done": {"written", "truncated", "skippedStubs"}}` line. The flat `{"_meta": true, ...}` form is tolerated too. Unknown `_done` fields (e.g. `truncatedBy`, `credits`) are kept on the exposed trailer and otherwise ignored.
- A transport failure while reading rows surfaces as the SDK's `CONNECTION_ERROR` / `REQUEST_TIMEOUT` error, never a raw HTTP-library exception.
- A body that ends without its `_done` line raises `INCOMPLETE_EXPORT` **after** yielding the rows that did arrive: a cut-short export never looks complete. The stream exposes a `complete` flag and a count of rows read.
- Two more shapes mean incomplete, handled defensively (the API ends a failed export without a trailer): a `_done` line whose `complete` is exactly `false`, and any line whose only key is `_error`. Both raise `INCOMPLETE_EXPORT` after the rows that did arrive, and `complete` stays false. An `_error` line is never yielded as a row; its `code` / `message` travel on the error's details. A `_done` without a `complete` field, or with `complete: true`, is complete.
- One behaviour after the caller ends a stream: iterating a stream whose `_done` trailer was never read raises `INCOMPLETE_EXPORT`, however it was ended (closed before iterating, `break` and iterate again, leaving the `with`/`using` block early). Iterating a stream that was read to its `_done` again yields nothing and does not raise. A stream is read once; call `export()` again to re-read.
- Lines that are not JSON objects (e.g. a half-written last line) are counted (`invalidLines`), not silently dropped.
- `truncated` means matching rows remain that the export did not reach. Once the `_done` trailer has been read, `_done.truncated` is authoritative (API 2.3.0). The server sets it when the export wrote up to `limit` / the server maximum and another matching row exists past the last one read, when the credit pool could not pay for rows added since the count, or when the server ended the export early (`truncatedBy`: `limit`, `max_rows`, `credits` or `recovered`). It is never derived from the row count, and skipped stubs do not count. Before the trailer, `truncated` falls back to the `X-Export-Truncated` header (`1`/`0`), then `_meta.truncated`. Both come from the up-front count, which may be up to a minute old and never caps the stream (rows added since are streamed and reserved as they come), so they are only a hint.
- Credit cost of an export (API 2.3.0): 1 credit per row written, at least 1 per export. The call is admitted for 1 credit, the matching rows are counted, and the rest of 1 credit per row the export can return is reserved; a pool that cannot cover it answers `402 CREDITS_EXHAUSTED` before anything streams. Rows added since the count are streamed too, each reserved as it comes; if the pool cannot pay for them, the export ends with `truncatedBy: "credits"`. Unwritten rows are refunded when the stream ends; the settled charge is `_done.credits.charged`.
- Export filters (API 2.3.0): `documentType` is an exact, case-insensitive label match; `documentTypeCode` selects a type family; `dateFrom` / `dateTo` are `YYYY-MM-DD`; `fetchedSince` and `changedSince` take an ISO 8601 date or a date-time with a timezone; `limit` is a positive integer. The SDKs validate client-side only where the check mirrors the server exactly (a timezone-less date-time object, a non-positive or non-integer `limit`); strings are passed through for the server to validate.

### 1.9 Webhook signature verification

- `X-Webhook-Signature` is the lower-case hex HMAC-SHA256 of the **raw request body bytes**, keyed with the webhook secret (UTF-8). Verify against the bytes as received, never re-serialised JSON.
- Comparison is constant-time. A missing, empty, non-ASCII or otherwise non-hex header is a plain verification failure (`false` / the SDK's signature error), never an unrelated exception.
- The combined helper also enforces a replay window on the signed `timestamp` (default 300 s, configurable or disableable) and returns the parsed event. Receivers dedupe on `delivery.id`, which is stable across retries (also sent as `X-Webhook-Delivery`).

### 1.10 Language-specific names

| Concept | Python (`lexapi-client`) | TypeScript (`@lexapi/client`) |
|---|---|---|
| Read-only-POST opt-in (§1.2) | `RetryConfig(retry_read_only_posts=True)` | `retry.retryReadOnlyPosts: true` |
| Power-user non-GET status override (§1.2) | `RetryConfig.retry_statuses_non_idempotent` | not offered |
| Max retries | `max_retries=` / `RetryConfig.max_retries` | `retry.maxRetries` |
| Timeouts (§1.6) | `timeout=` (R) and `connect_timeout=` (C); per call `client.with_options(...)` | `timeoutMs` (R, connect included); per call `{ timeoutMs }` |
| 503 error type (§1.3) | `ServiceUnavailableError` (subclass of `UpstreamError` for 0.1.x compatibility) | `ServiceUnavailableError` |
| Transport error types | `LexAPIConnectionError`, `RequestTimeoutError` | `NetworkError`, `TimeoutError` with status 0 |
| 2xx non-JSON (§1.3) | `InvalidResponseError` | `InvalidResponseError` |
| Export completeness (§1.8) | `IncompleteExportError`; `stream.complete`, `stream.rows_read`, `stream.invalid_lines` | `IncompleteExportError`; `stream.complete`, `stream.rowsRead`, `stream.invalidLines` |
| Webhook helpers (§1.9) | `verify_webhook`, `verify_signature`, `compute_signature`, `WebhookSignatureError` | `verifyWebhook`, `verifySignature`, `computeSignature`, `WebhookSignatureError` |
