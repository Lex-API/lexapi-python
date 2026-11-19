# Changelog

All notable changes to `lexapi-client` (import `lexapi`). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/) (0.x: minors may break, documented here).

## [Unreleased]

### Added

- **`client.legislation_as_of(document, as_of, *, provision=None, language=None)`**
  (sync + async) — `POST /legislation/as-of`: the version of an EU
  regulation, directive or decision listed for a date and, with
  `provision`, one article, annex or recital range of it
  (`LegislationAsOfResponse`: `document`, `language`, `as_of`,
  `as_of_resolved`, `as_of_reason`, `as_of_version`, `act`
  (`LegislationAsOfAct`), `provision` (`LegislationAsOfProvision`),
  `consolidated`, `consolidated_as_of`, `base_celex`, `consolidated_note`,
  `provenance`). An answer that cannot be established is a normal return
  with `as_of_resolved == False` and an `as_of_reason`
  (`AS_OF_LOOKUP_REASONS`), never an exception. 1 credit when no text is
  fetched; when the provision's text has to be fetched, 4 (5 when it is
  fetched live from EUR-Lex), whatever the answer, and 1 if the fetch fails
  or finds no text. **Requires LexAPI API 2.26.0**;
  on an older API the call raises `NotFoundError`.
- `AsOfVersion.valid_until` / `.checked_at` (set by `legislation_as_of`).
- **`PaymentPastDueError`** (403 `PAYMENT_PAST_DUE`, API 2.27.0): a
  subscription payment failed and was not completed in time, so API requests
  are refused until it succeeds. `.billing_url` is where to pay the open
  invoice, `.access_ended_at` when access ended (`None` when not known).
  Never retried. This code was raised as the base `LexAPIError` before;
  `except LexAPIError` still catches it. It is not a `TierForbiddenError`; on
  an API before 2.27.0 a failed payment is still a code-less 403 raised as
  `TierForbiddenError`.
- **`NotFoundError.unknown_endpoint`**: True when the API (2.27.0 or later)
  has no such endpoint: a mistyped path, a method newer than the API, or a
  national route for a country that is not available. Always False on APIs
  before 2.27.0; there the message starts with "Unknown API endpoint".
- **`lexapi.passages`**: cited passages for RAG frameworks. Turns the
  responses of `semantic_legislation_search`, `semantic_search`,
  `get_document` and `legislation_as_of` into immutable `Passage` objects (a
  text with its CELEX number, the part matched, title, language, link,
  consolidated-text labels and `as_of` labels), collected in a
  `PassageResult` with the call's counts and credits
  (`from_legislation_search`, `from_case_law_search`, `from_document`,
  `from_legislation_as_of`). On an `as_of` search, the text of a hit that is
  not resolved for the date is withheld by default (`text=None`, with the
  `as_of_reason`); `unresolved="drop"` or `"keep_labelled"` change that.
  `Passage.metadata()` gives the citation as a flat dictionary (never the
  text), `Passage.id` a stable identifier, `format_citation(passage)` the
  citation as one line and `format_passage(passage)` the content followed by
  a `Source:` line; `restrict_to_celex(result, celex)` keeps the passages of
  given acts. No request is made and no dependency is added.
- **`LexAPIError.body_excerpt`**: the first 200 characters (code points) of a
  body that is neither JSON nor empty, decoded as UTF-8 with invalid bytes
  replaced (`None` otherwise); may contain document text, so do not show it to
  a model or log it by default. `LexAPIError(...)` and `error_from_response`
  take it as a keyword argument.
- **`national.legislation_semantic_search(..., as_of=...)`** (sync + async,
  API 2.29.0): a date (`YYYY-MM-DD` or a `datetime.date`, sent as `asOf`;
  a `datetime` is cut to its own calendar date, not converted to UTC) that
  labels each hit. The search runs over today's texts only and the text
  is never swapped. A hit is resolved only when the publisher's dates, as of
  LexAPI's last recorded read of the act (`checked_at`), show today's wording
  already applied on the date; an unresolved hit keeps today's wording, which
  is not established as the text for the date. LexAPI holds such dates for
  Austria and Spain. Response: `as_of`, `as_of_summary`; per hit: `as_of`,
  `as_of_resolved`, `as_of_reason` (`NATIONAL_AS_OF_REASONS` /
  `NationalAsOfReason`: `provision_amended_since`, `no_longer_in_force`,
  `provision_dates_unknown`, `asof_not_available`) and `as_of_version`, a
  `NationalAsOfVersion` (a subclass of `AsOfVersion`: `kind ==
  "current_unit"`, `valid_from`, `valid_to`, `source_id`, `checked_at`);
  a version of another kind stays an `AsOfVersion`, with everything on
  `.raw`.
- **Version snapshots** (API 2.28.0): `VersionEntry.observed_from` (when the
  version began; `None` when LexAPI has no record of it) and, on
  `get_document_at_date`, `VersionEntry.observed_until` and the response's
  `kind` (`"observation_snapshot"`), `date_in_future` and `in_force_lookup`
  (`InForceLookup`). `VersionEntry.provenance` / `.field_provenance` on
  snapshots. These are LexAPI's fetch snapshots, not the law in force on a
  date.
- **`NotFoundError.reason`**, `.before_tracking` / `.tracked_since` /
  `.in_force_lookup`, and `.boundary_unknown` / `.candidates`
  (`SnapshotCandidate`: `version`, `fetched_at`, `observed_from`) /
  `.candidates_truncated`: why `get_document_at_date` has no snapshot for the
  date (API 2.28.0). `unknown_endpoint` is unchanged.
- **In force today** (API 2.19.0): `in_force`, `not_in_force_reason`,
  `end_of_validity`, `entry_into_force` on `Document` (document reads,
  batch, by-URL, metadata) and `SemanticLegislationResult`;
  `SemanticLegislationResponse.in_force_filter` (`InForceFilter`:
  `removed_not_in_force`, `removed_unknown`).
- **Provenance** (API 2.20.0): `provenance` (`Provenance`) and
  `field_provenance` (`FieldProvenance`) on `Document`,
  `SemanticLegislationResult` and `SemanticCaseLawResult`;
  `SemanticSearchResponse.field_provenance`; `Document.version`.
- `SubscriptionInfo.max_semantic_results` /
  `.max_semantic_legislation_results` (`get_info`, API 2.24.0).
- **`semantic_legislation_search(..., in_force=...)`** (sync + async, API
  2.19.0): sent as the top-level `inForce` when not `None` (`False`
  included). `True` returns only hits whose act is known to be in force
  today; the removed hits are counted in `in_force_filter`. `NotInForceReason`
  lists the `not_in_force_reason` values.
- `lexapi.passages`: in every converter, a text that is empty or holds only
  whitespace or invisible format characters (such as a zero-width space)
  counts as no text: `text` is `None`, `has_text` is False and
  `text_status` is `"absent"` (on a hit not established for `as_of`, the
  date status, as for a missing text). A blank `fullText` of a document read
  gives no passage. A `Passage` built by hand is not normalised.

### Changed

- **Retry hints are read strictly.** `Retry-After` and `RateLimit-Reset`
  numbers must be plain ASCII decimals (`5`, `2.5`; no sign, exponent, `nan`
  or `inf`); a `Retry-After` date must be an IMF-fixdate or RFC 850 date in
  `GMT`, `UTC` or `+0000`, or an asctime date — a date in another zone
  (`Z`, `EST`, ...), or an impossible one, is ignored. The wait to a date,
  and a wait converted from an epoch value, is rounded to the nearest second
  (halves up); a time in the past is a wait of 0. A body `retryAfter` must be
  a number >= 0: the top-level field, else `error.details` on the typed
  envelope or the top-level `details` on the legacy shape. Anything else
  counts as absent: the normal backoff applies, and a national 503
  `SEARCH_INCOMPLETE` whose only hint is unusable is not retried. A 429 with
  code `QUOTA_EXHAUSTED` in `error.code` of the typed envelope is a quota 429
  too, and `RateLimitedError.quota_exhausted` always matches the retry
  decision.
- **`RetryConfig` refuses unusable waits.** `backoff_base` and `backoff_cap`
  must be finite numbers >= 0; anything else (`inf`, `nan`, a negative value,
  a number too large for a float) raises `ValueError`. 0.4.0 accepted
  `backoff_cap=float("inf")` as "no cap"; pass a large finite value instead.
- **Timeouts.** `timeout` and `connect_timeout` longer than the timers
  support (`inf` included) are clamped to 2**31 - 1 milliseconds (about 24.8
  days); `nan`, a negative value or a non-number raises `ValueError` when the
  client is created (`None` still means no limit).
- **Unusable figures read as absent.** An integer header (`RateLimit-*`,
  `X-National-*`, `X-Corpus-Version`, `X-Export-*`, the batch and post-filter
  counts) must be ASCII digits (leading zeros allowed) with a value up to
  2**53 - 1 (`+5`, `1e3`, `1_000` are absent). An integer read through a
  typed accessor (credits, usage, totals, counts) above 2**53 - 1 is absent;
  finite floats still truncate. A float field must be a finite number; a
  `bool` case-law `score` is now `None` (it was `1.0` / `0.0`).
- **Iterators raise when the server stops advancing.**
  `citations.iter_cited_by` / `iter_cites`, `webhooks.iter_deliveries` and
  `national.iter_documents` yield every page as it arrives. A walk ends at an
  empty page; at a page shorter than the echoed `limit` (when that is a
  positive integer; else than the requested page size); once the offset
  reaches a usable `total` / `totalEdges`; or after a page whose `hasMore` is
  not `true`, whose `nextCursor` is absent or empty, or whose list is empty.
  It raises `InvalidResponseError` when the server hands back a cursor
  already sent in the walk (or, when the walk would continue, one that is not
  a string), or echoes an `offset` other than the one requested. On answers
  without those position signals only, it also raises on the third page in a
  row that repeats the page before (edge pages, compared as JSON values) or
  brings no unseen delivery id. Pages already yielded stay valid; the page
  that showed the stall is on `err.body`. A `page_size` (or national `limit`)
  that is not a positive integer raises `ValueError` before any request. An
  edge group whose `citationCount` is not a count advances by the length of
  its edge list.
- A `2xx` answer that is not the documented shape now raises
  `InvalidResponseError`; before, it could be read as an empty result. To tell
  a malformed answer from an empty result, catch `InvalidResponseError`. This
  covers:
  - a `204` or an empty body, except for `webhooks.delete`;
  - a body that is not strict UTF-8 JSON. Newly rejected: `NaN` / `Infinity`,
    UTF-16 and UTF-32 bodies, and UTF-8-encoded surrogates (an invalid UTF-8
    byte and data after the JSON value already raised). One leading UTF-8 byte
    order mark is still accepted;
  - JSON that is not an object;
  - `success: false`. When the body carries the API's error envelope (`error`
    is an object or a non-empty string), the mapped API error is raised
    instead; it keeps the answer's status (`err.status == 200`) and uses code
    `INTERNAL` (`InternalServerError`) when the body names none. It is never
    retried. `webhooks.test` keeps returning `ok == False`;
  - a document or as-of answer without its documented fields: `get_document`,
    `get_document_by_url`, `national.get_document`, `get_document_version` and
    `get_document_at_date` need a `document` object (and a `document.content`
    that is an object when present, with `articles` a list of objects);
    `legislation_as_of` needs `document` (a non-empty string), `asOfResolved`
    (a boolean), `act` and `provenance` (objects);
  - a list field that is not a list or holds items that are not objects, and a
    missing `results` on `search`, `semantic_search`,
    `semantic_legislation_search` and the national semantic searches.

  Unknown extra fields are still accepted. The full list per endpoint is in
  `docs/sdk-contract.md` §1.3.
- **Construct models by keyword.** New fields on `SemanticLegislationResult`
  and `SemanticCaseLawResult` (in-force labels, `provenance`,
  `field_provenance`) come before the fields that
  `NationalSemanticLegislationResult` and `NationalSemanticCaseLawResult` add,
  and `NationalSemanticLegislationResult.in_force` now takes the position of
  the base class's field. Code that built these two national models with
  positional arguments must use keywords; on Python 3.9 the dataclass layout
  cannot keep the old order. Attribute names and types are unchanged.
- An error (non-2xx) body is read by the same strict parser: one that is not
  strict UTF-8 JSON is treated as not JSON — `err.body` is `None`, the message
  is `HTTP <status>`, the code comes from the status, and a `retryAfter` in it
  is not read; its start is on `body_excerpt`.

### Fixed

- A `Retry-After`, `RateLimit-Reset` or `retryAfter` hint that is negative or
  not finite (`nan`, `inf`, a number too large for a float) no longer makes a
  call raise `ValueError` or `OverflowError`, never makes the async client
  wait forever, and never sets `retry_after` to `nan`; no sleep receives
  `nan`, a negative or an overflowing value, whatever `max_retries` is.
- An asctime `Retry-After` date (`Sun Nov  6 08:49:37 1994`) is read as UTC;
  it was read in local time.
- On Python older than 3.12, a response body, error body, export line or
  webhook body nested deeper than 512 levels, or deeper than the recursion
  limit leaves room for where it is parsed, is refused before it is parsed
  (not JSON: `InvalidResponseError`, an error without a body, an invalid
  export line, `WebhookSignatureError`). Such a body could abort the
  interpreter. Each response body is parsed at most once (the retry decision
  and the error share it). From 3.12 the parser's own limit applies;
  `verify_webhook` now also treats a `RecursionError` as "not JSON".
- A deeply nested export line is counted in `invalid_lines` instead of
  raising `RecursionError`.
- A float field (`score`, the national coverage rates and bounds) holding a
  number too large for a float reads as `None` instead of raising
  `OverflowError`.
- Integer fields of the response models (for example `Document.version`)
  accept an integer, or a float with an integral value; a non-integral or
  non-finite number (`2.9`, `1e999`) is now `None`, and such an item of
  `WebhookRetryPolicy.delays_minutes` is left out. Before, `2.9` was read
  as `2` and `1e999` raised `OverflowError`.
- A non-finite number (`1e999`) in the `credits` / `usage` / `subscription`
  blocks, in the 402 figures or in `TimeoutError.timeout_ms` now reads as
  `None` instead of raising `OverflowError`; other values read as before.
- `InvalidResponseError` no longer quotes the response body in its message;
  the excerpt is on `body_excerpt`.
- The excerpt is decoded as UTF-8 with replacement whatever charset the
  response names (a leading byte order mark is kept), and is cut to 200 code
  points.
- An empty or JSON `null` error body leaves `err.body` as `None` (an empty one
  was `{}`) and sets no excerpt.
- A deeply nested JSON body is treated as not JSON (`InvalidResponseError`, or
  `HTTP <status>` on an error status) instead of raising `RecursionError`.
- An error body whose `code` or `message` is not a non-empty string no longer
  raises `TypeError` or puts that value in the message: it counts as absent.
- A `LexAPIError` survives a pickle round trip with its message unchanged
  (before, the `[CODE]` prefix was added twice).
- `AS_OF_REASONS` / `AsOfReason` now include `amendment_not_consolidated`,
  which `semantic_legislation_search(as_of=...)` hits can carry since LexAPI
  API 2.25.0. (The value was already passed through on `as_of_reason`.)

## [0.4.0] — 2026-11-11

Syncs the citation graph with LexAPI API 2.10.0–2.13.0. **Requires LexAPI
API 2.13.0 for the new fields**; older servers simply leave them `None`. No breaking changes: every new parameter is keyword-only and
optional, every new model field has a default.

### Added

- **`client.citations.transposition(celex, *, jurisdictions=None, resolved_only=None, language=None)`**
  (sync + async) — `GET /citations/transposition/{celex}` (5 credits, every
  plan): the measures the member states notified to the Commission for an
  EU act (CELEX sector 3), grouped by country (`TranspositionResponse`:
  `document`, `source_cellar` (`TranspositionCellarSource`), `source_lanes`,
  `note`, `jurisdictions` of `TranspositionCountry` (`jurisdiction`, `live`,
  `counts`, `measures`), `truncated`, `include_national`,
  `terms_required`; `TranspositionMeasure`: `id`, `resolved`,
  `resolution`, `resolution_basis` (`TranspositionResolutionBasis`),
  `provenance`, `citation_type`, `title`, `type_act`, `official_journal`
  (`TranspositionOfficialJournal`), `reference`, `eli`, `link`, `cellar`
  (`TranspositionCellarRecord`)). The holdings fields (`id`, `resolved`,
  `resolution`, `resolution_basis`, `counts.resolved`, `source_lanes`) are
  filled on BUSINESS, SCALE and ENTERPRISE for live jurisdictions and
  `None` otherwise; `resolved_only=True` keeps only the held measures. A
  CELEX that is not an EU act raises `NotFoundError`.
- **`client.citations.counterparts(id, *, jurisdictions=None, language=None)`**
  (sync + async) — `GET /citations/counterparts/{id}` (20 credits,
  BUSINESS and above): a national document's counterparts in the other
  live jurisdictions, through the EU acts it transposes or implements
  (`CounterpartsResponse`: `document`, `via` of `CounterpartVia` (`eu_act`
  (`CounterpartEuAct`, with `notified_measures`), `edge`, `counterparts`
  of `Counterpart` (`jurisdiction`, `id`, `resolved`, `title`,
  `citation_type`, `provenance`, `rank`), `truncated`), `truncated`). A
  lookup past its server-side budget raises `TimeoutError` (503,
  `partial`, refunded); the SDK does not retry it, since re-running waits
  the full budget again — narrow it with `jurisdictions`.
- **Filters on `cited_by`, `cites`, `network`, `path`, `related`** (and the
  `iter_cited_by` / `iter_cites` walks): `provenance`, `jurisdictions` and
  `resolution`, and `citation_type` now also takes a list — each one value
  or a list/tuple, sent comma-separated (an empty list sends no filter,
  i.e. the server default). `language` (the language of the
  titles filled in from the corpus) on every citation read. `unresolved`
  on `cites` and `network`.
- **Provenance and resolution on every edge:** `CitationEdge.provenance`
  / `.resolution`; `CitationNeighbour.edge_details` (list of
  `CitationEdgeKind`: `citation_type`, `provenance`, `resolution`).
- **Node identity:** `id`, `jurisdiction`, `document_kind` on
  `CitationDocumentGroup`, `CitationNeighbour`, `CitationPathNode` and
  `RelatedDocument` (`CitationNode` for the new endpoints' documents);
  `title_source` on groups and neighbours; `has_resume` / `has_summary`
  on inbound groups.
- **Paging totals:** `total_edges` and `counts_source` on `CitedByResponse`
  / `CitesResponse`; `cites_edge_total`, `cited_by_edge_total` and
  `counts_source` on `CitationNetworkResponse` (`None` when partial).
- **Unresolved references:** `unresolved` (list of `UnresolvedReference`:
  `kind`, `text`, `parsed_key`, `status`, `target_id`) and
  `unresolved_truncated` on `CitesResponse` / `CitationNetworkResponse`.
- **`CitationStatsResponse`:** `cached_at`, `cache_ttl_seconds`, and with
  `include_national=True` `by_jurisdiction` (`CitationJurisdictionCount`),
  `by_provenance` and `transposition` (`CitationStatsTransposition`).
- **National coverage:** `NationalJurisdictionCoverage.citation_graph`
  (`NationalCitationGraph`: `methods` (`NationalCitationGraphMethods`,
  with `unmeasured`), `edges`, `decision_resolution_rate`, `act_eu_rate`,
  `text_pattern_rate`, `transposition`, `precision`
  (`NationalCitationGraphPrecision`), `measured_at`, `nim_resolved_rate`,
  `nim_measured_at`, `floors` (`NationalCitationGraphFloors`)).
- **Errors:** `InvalidParamsError.param` / `.allowed` (an unknown citation
  filter value names the parameter and its allowed values, in either
  envelope); `TimeoutError.partial` / `.partial_reason` / `.timeout_ms`;
  new `NationalTermsNotAcceptedError` (403 `NATIONAL_TERMS_NOT_ACCEPTED`,
  `.terms_version`, `.accept_url`, `.terms_url`).
- **Type aliases:** `CitationType`, `CitationProvenance`,
  `CitationResolution`, `CountsSource`, `GraphDocumentKind`,
  `UnmeasuredReason`, with the tuples `CITATION_TYPES` (now including
  `transposition`), `CITATION_PROVENANCES`, `CITATION_RESOLUTIONS`. Values
  are not validated client-side; the server answers an unknown one with
  `InvalidParamsError`.
- **Incomplete national semantic searches:** `partial` / `partial_reason`
  (`"SEARCH_INCOMPLETE"`) on `NationalSemanticSearchResponse` and
  `NationalSemanticLegislationResponse` — the national index could not
  complete the search and the results may be missing better matches;
  `hint` says whether a retry or a rephrased query / changed filters may
  complete it. With nothing left to return the call raises instead:
  `NationalSemanticUnavailableError` (503) gains `.partial_reason`, and
  its `retry_after` falls back to the body's `retryAfter` when the
  header is missing.
- **`NationalSemanticFilterUnreachableError`** (a subclass of
  `InvalidParamsError`; 422 `NATIONAL_SEMANTIC_FILTER_UNREACHABLE`,
  `.jurisdiction`, `.partial_reason`): the national index cannot search
  this slice (jurisdiction, document kind, filters) completely and there
  is nothing left to return. Not retryable as is — rephrase the query or
  change the filters. Refunded.
- **`ResolveResponse.verified`** (`None` on servers that do not send it)
  and the `resolved_via` value `eli-cellar`.

### Changed

- A 503 whose body carries `partialReason: "TIMEOUT"` (a fixed
  server-side budget ran out) is no longer retried automatically. Other
  503s keep the existing retry policy.
- A 422 `NATIONAL_SEMANTIC_FILTER_UNREACHABLE` is now a
  `NationalSemanticFilterUnreachableError`, a subclass of
  `InvalidParamsError` (previously the base `LexAPIError`): `except
  InvalidParamsError` now also catches it. The request may be valid — it
  is a coverage limit of the national index, not a malformed request, and
  `.param` is `None`.
- An incomplete national semantic search 503 (`partialReason:
  "SEARCH_INCOMPLETE"`, top-level or in `error.details`) with no retry wait
  (the same search has repeatedly come back incomplete) is not retried
  automatically, even with `retry_read_only_posts`. With a wait it keeps
  the existing 503 policy, but is retried at most once per call. A
  `backoff_cap` below the server's wait shortens that wait.
  A 503 is never a `RateLimitedError`, whatever `Retry-After` /
  `retryAfter` it carries — only a 429 is.
- `iter_cited_by` / `iter_cites` stop once `offset` reaches `total_edges`
  when the server sends it, saving the final empty page.

## [0.3.0] — 2026-10-28

Adds support for LexAPI's national-law surface. **Requires LexAPI API 2.8.0.**

### National document shape (API 2.8.0)

The national models follow the normalized cross-jurisdiction contract — a
client written for one country now works for every other:

- `NationalDocument` (listing rows, full documents, and every semantic hit's
  new `document`) gains `document_kind` (`"act"` | `"decision"`, type
  `NationalDocumentKind`) with `is_act` / `is_decision`; every summary field
  (`title`, `document_type`, `ecli`, `eli`, `date_of_document` `YYYY-MM-DD`,
  `valid_to`, `in_force`) is always sent, `None` when the source has nothing.
- **`decision`** (`NationalDecision`: `court`, `chamber`, `case_numbers`,
  `cited_norms` of `NationalCitedNorm(text, document_id)`) on a decision,
  `None` for acts — served also when the decision has no text.
- `content` is now `NationalDocumentContent` (`full_text`, `articles` of
  `NationalArticle(number, title, section, text, in_force_from, in_force_to,
  details)`, `text_languages`, `text_language`, `embedded_foreign_text`,
  `repeal` (`NationalRepeal`), `source`, `details`) instead of the EU
  `DocumentContent`. Source-specific keys (Austria's `gericht`, Germany's
  `aktenzeichen`, unit ids, …) live only under `content.details` /
  `article.details` — read `decision` instead. `text_basis` is `None` for
  every source that does not label its text.
- `NationalSemanticCaseLawResult.document` / `NationalSemanticLegislationResult.document`
  (the matched document's summary); legislation hits add `jurisdiction`,
  `valid_from` and `law_type`, and the index fields are always sent (`None`
  when the index has nothing).

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
  - `list_documents(jurisdiction, page=, cursor=, limit=, q=, document_type=, include_repealed=)`
    / `iter_documents(...)` (a ready-made cursor walk, sync + async) —
    `GET /{jurisdiction}/documents` (`NationalDocumentListResponse`). 3
    credits flat per call (not per page). `page` only reaches the first
    1,000 rows (a 400 past that — `InvalidParamsError`); `cursor` (from
    `next_cursor`) works at any depth. New response fields `total_exact`,
    `has_more`, `next_cursor`; `page` is `None` in cursor mode. Newest-first
    by the document's own date. A slow page raises `TimeoutError` (504,
    refunded, retry-safe).
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
- **`DocumentWithdrawnError`** (410 `DOCUMENT_WITHDRAWN`) —
  a national document its source withdrew (a takedown, a pseudonymisation
  fix) is served nowhere: no title, no metadata, no text. Raised by
  `get_document` (typed envelope, `details: {id, jurisdiction, withdrawnAt}`)
  and by the citation methods for a withdrawn national id (legacy shape, the
  same three fields as top-level siblings instead). `.id`/`.jurisdiction`/
  `.withdrawn_at` read whichever shape answered. Not retryable; nothing is
  ever charged for it.
- `APIResponse.national_allowance_limit` / `national_allowance_remaining` /
  `national_allowance_resets_at`, reading the `X-National-Limit` /
  `X-National-Remaining` / `X-National-Reset` headers (present only for
  FREE/STARTER/PROFESSIONAL accounts on the four non-coverage national
  endpoints).
- **`include_national`** on all six citation-graph methods (`cited_by`,
  `cites`, `network`, `path`, `related`, `stats`, sync and async): also
  returns/follows national documents of live jurisdictions — BUSINESS,
  SCALE and ENTERPRISE only (`TierForbiddenError` below that; no code
  change needed, it already raises the existing error). `celex` (and
  `path`'s `from_celex`/`to_celex`) accept a national document id directly
  (e.g. `"AT-NOR-10001597"`); such an id whose jurisdiction isn't live
  raises a plain `NotFoundError`. `stats()` gained an optional
  `include_national` keyword (back-compatible — `stats()` with no
  arguments still works). Every response class gained an
  `include_national: bool | None` property, set once the flag was
  honoured.

### Fixed

- `NationalCoverageAvailability.from_` / `NationalCaseLawCoverageAvailability.from_`
  were parsed with the string helper (`_s()`), so they were always `None`
  on a real response — the contract sends an int year or `null`. Now
  `_i()`, typed `int | None`.
- The national document 404 is the typed envelope
  (`{success: false, error: {code, message, details: {id, jurisdiction}}}`),
  not the legacy bare shape it used to be.
- `NationalSemanticLegislationResult`'s docstring overstated how narrow
  `in_force` is: it comes from the semantic index on every hit (`True` or
  `False`); only `valid_to` (and forcing `in_force` to `False`) is limited
  to LexAPI's own confirmed-repeal override.
- `semantic_search()` / `legislation_semantic_search()` docstrings now note
  `filters["jurisdiction"]` (must match the path, or be omitted) and that
  `language` must be one the jurisdiction is indexed in.
- Typed national citation 404 already carries a top-level `code: NOT_FOUND`;
  added a regression test.

### Notes

- `NationalSemanticLegislationResult.in_force` / `.valid_to` mirror the
  API's snake_case wire field names for this endpoint (`in_force` /
  `valid_to`), unlike the camelCase fields elsewhere in this SDK.
- National semantic responses carry a `credits` body block, like the other
  credit-metered routes.

## [0.2.0] — 2026-10-26

Synced with the LexAPI API (API 2.0.0; later items name their API
version). No breaking changes to signatures; behaviour changes are
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

Retries follow one rule now (docs/sdk-contract.md §1.2): **retry only when the retry
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
  on `stream.done`. (SDK contract §1.8.)
- **Top-level `code` on legacy-shape error bodies, and `QUOTA_EXHAUSTED`.**
  When an error body carries a top-level string `code` (API 2.4.0 adds it
  next to the legacy text `error`), it is the error's `code`, ahead of the
  status-derived one. A 429 with `code: "QUOTA_EXHAUSTED"` (a legacy plan's
  used-up daily or monthly-semantic quota) raises `RateLimitedError` with
  `code == "QUOTA_EXHAUSTED"`, `quota_exhausted` true, `retry_after` `None`
  and `resets_at` from the body's `resetsAt`; it is never retried. Servers
  without the `code` are still recognised by the old shape check.
  (SDK contract §1.2/§1.3.)
- **Export parameters for API 2.3.0**: `document_type_code`
  (a type family) and `changed_since` (rows whose text changed), on `export()`
  of both clients. A naive `datetime` for `fetched_since` / `changed_since`
  and a `limit` that is not a positive integer raise `ValueError` before the
  request (the API answers 400 to both). Docstrings and README describe the
  per-row billing and the filter semantics of API 2.3.0.
- **`stream.truncated` reads `_done.truncated` first once the trailer has
  been read.** From API 2.3.0 that flag is authoritative: it is set when
  matching rows remain unreached (`truncatedBy`: `limit`, `max_rows`, `recovered` or
  `credits`) and never derived from the row count. The `X-Export-Truncated`
  header and `_meta.truncated` come from an up-front count that may be a
  minute old and never caps the stream, so they are now only the fallback
  before the trailer arrives. (SDK contract §1.8.)

## [0.1.0] — 2026-07-02

First release on PyPI: sync + async clients, typed models, typed errors,
retries, credit visibility, search, documents, versions, semantic search
(with HyDE), citations, webhooks, NDJSON export.

[0.2.0]: https://github.com/Lex-API/lexapi-python/compare/523235a...v0.2.0
[0.1.0]: https://github.com/Lex-API/lexapi-python/tree/523235a
