# Changelog

All notable changes to `lexapi-client` (import `lexapi`). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/) (0.x: minors may break, documented here).

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
  with `include_text=True`), `summary`, `parties`, `decision_date`,
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
  `post_filtered_kept`; `SearchHit.celex_number`, `form`, `date_of_document`.
- **Recent documents:** list values for `document_type` / `author` /
  `subdomain`; `RecentDocumentsResponse.source` (`cellar` / `live`) and
  `fetched_at`.
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
  CI runs 3.9–3.14, mypy, and a build + `twine check`.

### Changed

- **Default read timeout 60 s → 95 s.** The API's upstream budget is 90 s;
  at 60 s the SDK cut slow live fetches off before the server could answer
  with its typed (and refunded) 504 `TIMEOUT`. Pass `timeout=60` to keep the
  old value.
- **Retry waits no longer use `RateLimit-Reset` outside a 429.** LexAPI sends
  `RateLimit-*` on every response, so a retried 502/503/504 used to sleep for
  the rate-limit window (up to the 30 s cap) instead of the computed backoff;
  `err.retry_after` on non-429 errors was set from the same header.
  `Retry-After` is still honoured on any status.
- **A legacy-plan 429 for an exhausted quota is no longer retried** (body
  carries `usage.resetsAt` and no `retryAfter`) — it cannot succeed before
  the reset. Rate-limit 429s are retried as before.
- **Cloudflare 520–524 are retried for GETs** and mapped to
  `UpstreamError` (520–523) / `TimeoutError` (524) instead of a generic
  `LexAPIError` with code `INTERNAL`.
- `get_info()` returns `InfoResponse` (an `APIResponse` subclass — mapping
  access is unchanged).

### Deprecated

- `Webhook.max_retries` — the API no longer sends `maxRetries`; read
  `Webhook.retry_policy.max_attempts` (on `webhooks.get()`).

### Fixed

- Docstrings and README now match the API: `X-Source` values include
  `cellar` / `cellar-oj`; document reads settle at 1 (corpus) / 4 (Cellar) /
  5 (browser) credits; `/export` below BUSINESS is a 403
  `TierForbiddenError` (with `upgrade_url`), not a 402; `DELETE /webhooks/{id}`
  answers 200 with a JSON body (still returns `None`); README no longer
  describes the package as an unpublished scaffold.
- `mypy` now passes on the package (two `sleep(float | None)` errors).

## [0.1.0] — 2026-07-02

First release on PyPI: sync + async clients, typed models, typed errors,
retries, credit visibility, search, documents, versions, semantic search
(with HyDE), citations, webhooks, NDJSON export.

[0.2.0]: https://github.com/Lex-API/lexapi-python/compare/523235a...v0.2.0
[0.1.0]: https://github.com/Lex-API/lexapi-python/tree/523235a
