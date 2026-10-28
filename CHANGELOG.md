# Changelog

All notable changes to `lexapi-client` (import `lexapi`). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/) (0.x: minors may break, documented here).

## [0.3.0] — 2026-10-28

Adds support for LexAPI's national-law surface, ahead of its launch
(2026-11-03). Written against the server code directly
(`server/routes/api.js`, `server/routes/semantic.js`,
`server/middleware/jurisdiction.js`, `server/controllers/nationalController.js`,
`server/controllers/nationalSemanticController.js`,
`server/utils/jurisdictions.js`, `server/controllers/nationalCoverageController.js`
on `feat/national-launch-switch`, commit `6ec7440`) and the diffs of lex-api
PR #306 (the monthly allowance) and PR #314 (the documented contract,
openapi.yaml/docs.md, API 2.5.0). **PR #306, the launch-switch branch and
PR #314 had not merged to `main` when this was written** — re-verify against
the live API once all three deploy.

### Added

- **`client.national` / `client.national` (async)** — new resource
  namespace (`NationalResource` / `AsyncNationalResource`):
  - `coverage()` — `GET /national/coverage` (public, free). Which
    jurisdictions are live, their legislation / case-law coverage
    (`documents`, `from`, `courts`), semantic-search availability, and the
    `sources` to attribute (`NationalCoverageResponse`,
    `NationalJurisdictionCoverage`, `NationalCoverageAvailability`,
    `NationalCaseLawCoverageAvailability`, `NationalSemanticSearchAvailability`,
    `NationalSource`). Call this before any other national method — an
    unlaunched or unknown jurisdiction code answers every other national
    endpoint with a plain 404.
  - `get_document(jurisdiction, id, language=None)` — `GET /{jurisdiction}/documents/{id}`
    (`NationalDocumentResponse`, `NationalDocument`, `NationalTextBasis`).
    5 credits upfront, settled to 1 — national reads are always served from
    the corpus, never a live fetch.
  - `list_documents(jurisdiction, page=, limit=, q=, document_type=, include_repealed=)`
    — `GET /{jurisdiction}/documents` (`NationalDocumentListResponse`). 3
    credits flat per call (not per page).
  - `semantic_search(jurisdiction, query, hyde=, min_score=, limit=, language=, include_text=, filters=)`
    — `POST /{jurisdiction}/search/semantic` (`NationalSemanticSearchResponse`,
    `NationalSemanticCaseLawResult`). `hyde` is accepted for parity but never
    bills the 15-credit EU premium — the national index never rewrites.
  - `legislation_semantic_search(jurisdiction, query, min_score=, limit=, language=, include_text=, filters=)`
    — `POST /{jurisdiction}/legislation/semantic`
    (`NationalSemanticLegislationResponse`, `NationalSemanticLegislationResult`).
    No `hyde`, no `as_of` (both EU-only).
  - Every national result carries `attribution` — always show it to the end
    user next to the content.
- **`NationalAllowanceExhaustedError`** (402 `NATIONAL_ALLOWANCE_EXHAUSTED`) —
  the monthly national-call allowance of a plan below BUSINESS (FREE 30,
  STARTER 500, PROFESSIONAL 2,000 calls/UTC month) is used up. Typed
  `jurisdiction`, `limit`, `remaining`, `resets_at`, `upgrade_url`. Not
  retried.
- **`NationalSemanticNotEnabledError`** (409 `NATIONAL_SEMANTIC_NOT_ENABLED`)
  and **`NationalSemanticUnavailableError`** (503
  `NATIONAL_SEMANTIC_UNAVAILABLE`, a `ServiceUnavailableError` subclass) —
  the national semantic index does not search a jurisdiction, or is
  temporarily down. Legacy-shape bodies with a top-level `jurisdiction`
  (not nested in `details`); both are typed as that attribute. Neither
  charges anything.
- `APIResponse.national_allowance_limit` / `national_allowance_remaining` /
  `national_allowance_resets_at`, reading the `X-National-Limit` /
  `X-National-Remaining` / `X-National-Reset` headers (present only for
  FREE/STARTER/PROFESSIONAL accounts on the four non-coverage national
  endpoints).

### Fixed (post-review)

- `NationalCoverageAvailability.from_` / `NationalCaseLawCoverageAvailability.from_`
  were parsed with the string helper (`_s()`), so they were always `None`
  on a real response — the contract sends an int year or `null`. Now
  `_i()`, typed `int | None`.
- The national document 404 is the typed envelope
  (`{success: false, error: {code, message, details: {id, jurisdiction}}}`),
  not the legacy bare shape it used to be — matches lex-api commit `6ec7440`.
- `NationalSemanticLegislationResult`'s docstring overstated how narrow
  `in_force` is: it comes from the semantic index on every hit (`True` or
  `False`); only `valid_to` (and forcing `in_force` to `False`) is limited
  to LexAPI's own confirmed-repeal override.
- `semantic_search()` / `legislation_semantic_search()` docstrings now note
  `filters["jurisdiction"]` (must match the path, or be omitted) and that
  `language` must be one the jurisdiction is indexed in.

### Notes

- `NationalSemanticLegislationResult.in_force` / `.valid_to` mirror the
  server's own inconsistent wire field names for this one endpoint
  (`in_force` / `valid_to`, snake_case) — unlike every other camelCase field
  in this SDK. Intentional; do not "fix" it, it would stop matching the API.
- National semantic responses never carry a `credits` body block, the same
  gap as the existing EU `semantic_search` / `semantic_legislation_search`
  (`semantic.js` does not attach the credits envelope) — read the
  `national_allowance_*` / `rate_limit_*` headers instead. (lex-api PR #314's
  openapi.yaml lists `credits` on these response schemas — that's drift to
  fix on the API side, not here.)
- **Not in this release:** `include_national` on the citation-graph methods
  (`cited_by`/`cites`/`network`/`path`/`related`/`stats`), and national ids
  on `/citations/*` generally — both BUSINESS+, both in the documented
  contract (lex-api PR #314), both deliberately deferred here pending
  confirmation of scope. See the hardening checklist below.

### Known upcoming changes (lex-api `fix/national-api-hardening` — not implemented here)

- Withdrawn documents will answer 410 `DOCUMENT_WITHDRAWN` with
  `details {id, jurisdiction, withdrawnAt}`, serving nothing of the
  document. Needs a `DocumentWithdrawnError` (+ `withdrawn_at`), a README
  row, a test, and the `NationalDocument` / `get_document` docstrings
  rewritten so a withdrawal isn't described as "missing text."
- Listing pagination: `cursor=` on `list_documents` (sync + async);
  `next_cursor` / `has_more` / `total_exact` on `NationalDocumentListResponse`;
  `page` absent in cursor mode. Server limits: `q` 3–200 code points; `page`
  past offset 1000 is a 400. Ordering becomes `dateOfDocument` desc.
- Listing credit weight 3 → 1 (update `national.py`, the docstrings, the
  README).
- Typed national citation 404: likely no parser change needed (both shapes
  already map to `NotFoundError`) — re-check once `include_national` ships.
- `hyde` validation on legislation semantic: no bug today
  (`legislation_semantic_search` never sends `hyde`); add a regression test
  asserting the request body never has a `hyde` key, to catch a future
  regression.

## [0.2.0] — 2026-10-26

Synced with the LexAPI API as of `openapi.yaml` on lex-api `main` at
`633eb67` (PR #298). No breaking changes to signatures; behaviour changes are
listed under **Changed** — read those before upgrading.

### Added

- **`semantic_legislation_search(as_of=...)`** — the text in force on a date
  (`YYYY-MM-DD` string or `datetime.date`). New hit fields: `as_of`,
  `as_of_resolved`, `as_of_reason` (see `lexapi.AS_OF_REASONS` /
  `lexapi.AsOfReason`), `as_of_version` (`AsOfVersion`: `celex`, `date`,
  `kind`), `matched_version` (`MatchedVersion`); response fields `as_of` and
  `as_of_summary` (`AsOfSummary`: `live_fetches`, `unresolved`).
- **Consolidated-text labels on legislation hits:** `consolidated`,
  `consolidated_as_of`, `base_celex`, `consolidated_note`, `base_act`
  (`SemanticBaseAct`), plus `authentic_celex`; and the previously untyped
  `id`, `snippet`, `language`, `chapter`, `section`.
- **Case-law hits:** `snippet` (the default payload — `text` is only present
  with `include_text=True`), `summary`, `parties` (a list — the upstream
  sends one, without roles; `parties_text` joins it with `"; "` — use
  `case_name` for the citation-style title), `decision_date`,
  `jurisdiction`, `language`, `paragraph_number`. Both semantic responses:
  `max_results`, `upstream_metadata`.
- **Documents:** `accept_languages` on `get_document` / `get_documents_batch`;
  `bypass_corpus` on `get_document_by_url`; `Document.title_language`,
  `Document.text_availability` (`TextAvailability`, with
  `latest_consolidated` / `upcoming_consolidated` / `newest_consolidated`
  `ConsolidatedVersionHint`s), `Document.text_available`, `fetched_at`,
  `celex_number`, and the metadata endpoint's consolidated hints;
  `DocumentResponse.text_available` (`X-Text-Available`);
  `DocumentBatchResponse.text_unavailable`, `corpus_hits`, `cellar_served`,
  `oj_recovered`, `live_fetches`, `language_served`.
- **Search:** `fresh=` (bypass the 60-minute result cache);
  `SearchResponse.source` (`X-Search-Source` / body `source`: `soap`,
  `cellar`, `corpus`), `cache` (`X-Search-Cache`), `search_parameters`,
  `post_filtered_kept` (parsed `X-PostFiltered-Kept`: `{"author":
  PostFilterCount(kept=3, before=10), ...}`) and `post_filtered_kept_raw`;
  `SearchHit.celex_number`, `form`, `date_of_document`.
- **Recent documents:** `author` accepts a list (sent as repeated params);
  `RecentDocumentsResponse.source` (`cellar` / `live`) and `fetched_at`.
  `document_type` and `subdomain` stay **single-valued**: the endpoint has no
  multi-type fan-out and EUR-Lex honours only the first `FM_CODED` /
  `DTS_SUBDOM`, so passing more than one raises `ValueError` instead of being
  silently narrowed (a one-item list is accepted). Use `search()` for
  multi-value filters.
- **Export:** `include_stubs=`; `ExportStream.written` / `skipped_stubs`
  from the `_done` trailer; `date_from` / `date_to` / `fetched_since` accept
  `datetime` objects.
- **Webhooks:** watch lists — `create(..., watch_celex=[...])` (criteria now
  optional), `update(watch_celex=..., remove_search_criteria=True)`;
  `Webhook.watch_celex`, `criteria_errors`, `retry_policy`
  (`WebhookRetryPolicy`), `has_search_criteria`;
  `WebhookResponse.requeued_deliveries`; `WebhookDelivery.webhook_id`.
- **Webhook signature verification:** `verify_webhook()` (signature + replay
  window, returns the event), `verify_signature()`, `compute_signature()`,
  `WebhookSignatureError`.
- **Citations:** `iter_cited_by()` / `iter_cites()` (walk every edge page);
  `relation_sync` (`RelationSync`) on `cited_by` / `network`;
  `CitationExtractResponse.document`; `title_source` on path nodes and related
  documents; `edge_count` on edge pages.
- **`/info`:** `get_info()` returns `InfoResponse` (`version`, `endpoints`,
  `search_filters`, `supported_languages`, ...). Every response:
  `subscription_info` (`SubscriptionInfo`) and `rate_limit_limit` /
  `rate_limit_remaining` / `rate_limit_reset` (`RateLimit-*` headers).
- **Errors:** `CreditsExhaustedError.operation` / `weight` / `available` /
  `included` / `topup` / `used`; `RateLimitedError.quota_exhausted` /
  `resets_at` (legacy daily / semantic quotas); `LexAPIError.upgrade_url`
  (legacy tier gates); legacy bodies' top-level `details` (e.g. the list of
  rejected webhook criteria) is now kept on `err.details`.
- **`client.with_options(timeout=..., max_retries=..., retry_config=...)`** —
  per-call overrides sharing the connection pool.
- Path segments (CELEX, webhook ids, dates) are percent-encoded; CELEX
  characters such as `32008R1272R(01)` pass through unchanged.
- `LICENSE` (MIT) file, Python 3.9–3.14 classifiers, `Typing :: Typed`;
  CI runs 3.9–3.14, `ruff check`, `ruff format --check`, `mypy --strict`,
  and a build + `twine check`. The version is single-sourced in
  `lexapi/_meta.py`; `hatchling>=1.27` is pinned (PEP 639 license field).
- `RetryConfig(retry_read_only_posts=True)` — opt-in retry of the read-only
  POSTs (search, document reads, resolve, semantic search) on an app-origin
  502/503/504, which the API refunds in full. Never applies to webhook writes.
- New error types: `ServiceUnavailableError` (503, code `SERVICE_UNAVAILABLE`,
  subclass of `UpstreamError`), `InvalidResponseError` (2xx whose body is not
  JSON), `IncompleteExportError` (export without its `_done` trailer).
- `ExportStream.complete`, `rows_read`, `invalid_lines`.

### Changed

Retries follow one rule now (PLAN.md §1.2): **retry only when the retry
cannot charge you twice.** LexAPI refunds a failure only when its own app
finishes the error response, so proxy errors and dropped connections are not
refunded.

- **Billed GETs no longer retry proxy 502/504 or Cloudflare 520/524**, nor a
  connection dropped after the request was sent (both may already have been
  charged). They still retry app-origin 502/503/504 (refunded), 429, and
  failures that never reached the app. Free calls (`get_info`, webhook reads)
  retry all of these.
- **Connection failures that never reached the app are retried for every
  method**: connect errors/timeouts, pool timeouts, a stale keep-alive socket
  closed before any response byte, Cloudflare 521/522/523. Previously a stale
  keep-alive disconnect was never retried.
  A connection closed after part of the response arrived is not in that set
  (a billed call may already have been charged).
- **App origin is decided by headers only** — `X-LexAPI-Version` or the
  limiter's `RateLimit-Limit`; a JSON error body alone no longer counts.
- **Default timeouts: response 60 s → 95 s, pool 95 s → 10 s.** `timeout` is
  a per-attempt inactivity limit (response head, then each body chunk), not
  a whole-call deadline. 95 s sits above the API's 90 s budget for one
  upstream fetch and below Cloudflare's 100 s. Pass `timeout=60` to keep the
  old value.
- **A 2xx with a non-JSON body raises `InvalidResponseError`** instead of
  returning an empty, successful response.
- **503 maps to `SERVICE_UNAVAILABLE`** (`ServiceUnavailableError`, still an
  `UpstreamError`), not `UPSTREAM_ERROR`.
- **Legacy error bodies:** a string `error` is never read as an error code
  (the code comes from the HTTP status), and `details` is kept on bodies with
  only `message` / `details`.
- **Numeric retry hints:** values above 1e12 are epoch milliseconds and above
  1e9 epoch seconds (was: above 1e7 → epoch seconds). `X-RateLimit-Reset` is
  no longer read (the API doesn't send it).
- **Export streams:** iterating a stream whose `_done` trailer was never
  read raises `IncompleteExportError`, however it was ended (closed early,
  `break`, leaving the `with` block); a fully read stream yields nothing again.
  A mid-stream transport failure raises
  `LexAPIConnectionError` / `RequestTimeoutError` (was: a raw `httpx`
  exception); a missing `_done` trailer raises `IncompleteExportError`;
  `truncated` no longer reports skipped stub rows as truncation.
- **`webhooks.iter_deliveries` yields each delivery once** (deduplicated on
  `id`) when new deliveries shift the pages.
- **Empty, `.` and `..` path segments raise `ValueError`** (httpx would
  resolve `..` to the parent endpoint).
- **Retry waits no longer use `RateLimit-Reset` outside a 429.** LexAPI sends
  `RateLimit-*` on every response, so a retried 502/503/504 used to sleep for
  the rate-limit window (up to the 30 s cap) instead of the computed backoff;
  `err.retry_after` on non-429 errors was set from the same header.
  `Retry-After` is still honoured on any status.
- **A legacy-plan 429 for an exhausted quota is no longer retried** (body
  carries `usage.resetsAt` and no `retryAfter`) — it cannot succeed before
  the reset — and its `retry_after` is `None` (it used to show the 60 s rate
  window). Rate-limit 429s are retried as before.
- **Cloudflare 520–524** are mapped to `UpstreamError` (520–523) /
  `TimeoutError` (524) instead of a generic `LexAPIError` with code
  `INTERNAL`; which of them are retried follows the rule above.
- `get_info()` returns `InfoResponse` (an `APIResponse` subclass — mapping
  access is unchanged).

### Deprecated

- `Webhook.max_retries` — the API no longer sends `maxRetries`, so it is
  always `None`; reading it emits a `DeprecationWarning` (only on your own
  attribute access — not from `dataclasses.asdict`/`replace`, repr, equality,
  copy or pickle). Read
  `Webhook.retry_policy.max_attempts` (on `webhooks.get()`).

### Fixed

- Docstrings and README now match the API: `X-Source` values include
  `cellar` / `cellar-oj`; document reads settle at 1 (corpus) / 4 (Cellar) /
  5 (browser) credits; `/export` below BUSINESS is a 403
  `TierForbiddenError` (with `upgrade_url`), not a 402; `DELETE /webhooks/{id}`
  answers 200 with a JSON body (still returns `None`); README no longer
  describes the package as an unpublished scaffold.
- `mypy` now passes on the package, in strict mode.
- `verify_signature` returns `False` for a non-ASCII signature header instead
  of raising `TypeError` (anyone can send such a header to a public receiver).
- `with_options(connect_timeout=...)` alone now takes effect (it was ignored
  unless `timeout` was also passed).
- Docstrings: batch live items without text bill 1 credit; semantic case-law
  `limit` caps are FREE 10 / STARTER 20 / PROFESSIONAL 50 / BUSINESS+ 100;
  recent documents are admitted against `ceil(limit / 10)` pages; the export
  docs no longer state a per-row price (see the pricing page).
- **Export streams never report a failed export as complete.** A `_done`
  trailer that says `"complete": false`, or any line whose only key is
  `_error`, now raises `IncompleteExportError` (after the rows that did
  arrive) and leaves `stream.complete` false; before, any `_done` line counted
  as complete and an `_error` line was yielded as a document row. The error
  line's `code` / `message` are on the exception's `details` and on the new
  `stream.error`. Unknown `_done` fields (`truncatedBy`, `credits`) are kept
  on `stream.done`. (PLAN.md §1.8; lex-api #303.)
- **Top-level `code` on legacy-shape error bodies, and `QUOTA_EXHAUSTED`.**
  When an error body carries a top-level string `code` (lex-api #304 adds it
  next to the legacy text `error`), it is the error's `code`, ahead of the
  status-derived one. A 429 with `code: "QUOTA_EXHAUSTED"` (a legacy plan's
  used-up daily or monthly-semantic quota) raises `RateLimitedError` with
  `code == "QUOTA_EXHAUSTED"`, `quota_exhausted` true, `retry_after` `None`
  and `resets_at` from the body's `resetsAt`; it is never retried. Servers
  without the `code` are still recognised by the old shape check.
  (PLAN.md §1.2/§1.3.)
- **Export parameters for API 2.3.0** (lex-api #303): `document_type_code`
  (a type family) and `changed_since` (rows whose text changed), on `export()`
  of both clients. A naive `datetime` for `fetched_since` / `changed_since`
  and a `limit` that is not a positive integer raise `ValueError` before the
  request (the API answers 400 to both). Docstrings and README describe the
  per-row billing and the filter semantics of API 2.3.0. **Publish only after
  lex-api #303 is deployed.**
- **`stream.truncated` reads `_done.truncated` first once the trailer has
  been read.** From API 2.3.0 that flag is authoritative: it is set when
  matching rows remain unreached (`truncatedBy`: `limit`, `max_rows`, `recovered` or
  `credits`) and never derived from the row count. The `X-Export-Truncated`
  header and `_meta.truncated` come from an up-front count that may be a
  minute old and never caps the stream, so they are now only the fallback
  before the trailer arrives. (PLAN.md §1.8; lex-api #303.)

## [0.1.0] — 2026-07-02

First release on PyPI: sync + async clients, typed models, typed errors,
retries, credit visibility, search, documents, versions, semantic search
(with HyDE), citations, webhooks, NDJSON export.

[0.2.0]: https://github.com/Lex-API/lexapi-python/compare/523235a...v0.2.0
[0.1.0]: https://github.com/Lex-API/lexapi-python/tree/523235a
