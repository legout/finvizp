# finvizp foundation design spec

**Date:** 2026-08-27

**Status:** Approved after design grilling

**Execution Path:** Dedicated dormant Hermes Kanban board backed by the
[documentation index](../../index.md)

## Purpose

Build `finvizp`—“Finviz for Python”—as a public, async-first, Arrow-native
client for the complete verified public Finviz product surface. It must replace
every capability in `finvizfinance` 1.4.0, fix its current parser and transport
defects, add public features missing from that package, and preserve enough
source and temporal provenance for callers to build honest point-in-time
history.

The package remains Finviz-only. Yahoo overlap is useful for external
reconciliation, but `finvizp` never imports, joins, fills, or relabels `yfin`
data.

## Context

- `finvizfinance` 1.4.0 is a broad capability inventory built on synchronous
  requests, Beautiful Soup/lxml, pandas, global mutable transport, and mixed
  return types.
- The current stock page contains sixteen structured tables, including six
  snapshot tables; the audited package reads only the first snapshot table.
- The statements endpoint exposes income, balance-sheet, and cash-flow data in
  annual and quarterly forms (`IA`, `IQ`, `BA`, `BQ`, `CA`, `CQ`) plus currency.
- Current public Finviz also exposes structured maps, publisher news pages,
  fund/manager insider pages, economic-calendar detail pages, and futures tile
  JSON that `finvizfinance` does not cover correctly.
- Finviz publishes a finite stock sitemap containing one canonical stock URL
  and one `ty=oc` variant for each current security. Its public suggestions API
  provides bounded JSON search results but is not a complete universe feed.
- `fastreq` 3.2 supports Python 3.11+, async curl transport, fixed browser
  impersonation, explicit proxy routing, retries, rate limits, and route-local
  sessions.
- Public product inventory is frozen at 2026-08-27 for the 1.0 release gate.

## Requirements

### Functional requirements

- **FR-1:** Distribution and import package are named `finvizp`.
- **FR-2:** Public 1.0 supports every capability in the frozen public-surface
  inventory and every audited `finvizfinance` capability or an explicit Arrow
  replacement workflow.
- **FR-3:** Public 1.0 includes stock/quote bundles, statements, screeners,
  signals, earnings screens, groups, maps data, ratings, peers, ETF-holder
  reverse relationships, ticker/global/publisher news metadata,
  ticker/global/fund/manager insider data, economic calendar/detail data,
  forex, crypto, current futures data, public chart/spectrum artifacts, and
  dedicated symbol-universe and symbol-search operations.
- **FR-4:** Login- or Elite-only options, portfolio, export/API, complete ETF
  holdings, alerts, correlated stocks, fundamental charts, and publishing are
  deferred until legitimately authenticated and verified. No always-failing
  public stubs or guessed schemas are published.
- **FR-5:** Endpoint functions accept a symbol or finite iterable of symbols,
  normalize reviewed class-share notation, deduplicate canonical symbols in
  first-occurrence order, and preserve the requested-to-provider resolution.
- **FR-6:** Every network operation returns an immutable generic
  `FetchResult[T]`; `T` may be an Arrow table, a compound bundle, or an artifact.
- **FR-7:** Strict completeness is the default. Recoverable partial results are
  returned only with `allow_partial=True`; otherwise a typed exception carries
  the immutable partial result.
- **FR-8:** Compound pages are fetched and parsed once into bundles. A
  `QuoteBundle` contains separate snapshot, ratings, news, insider, peer,
  ETF-holder, description, signal/link, and artifact relations. Projection
  functions reuse the bundle cache and original provenance.
- **FR-9:** Repeated current-value captures can form collector-built history,
  but the package never claims unavailable retroactive history. Provider
  period/event time remains distinct from network fetch time.
- **FR-10:** The package supplies no database, Parquet snapshot writer,
  scheduler, or persistence abstraction. Callers own persistence and dedupe.
- **FR-11:** One checked-in machine-readable capability manifest maps every
  legacy and frozen-public capability to its `finvizp` interface, output kind,
  access tier, documentation, implementation status, and tests.
- **FR-12:** Sync wrappers exist for every public async operation and fail
  clearly when called inside an active event loop.
- **FR-13:** `symbols_async()`/`symbols()` return the unique canonical Finviz
  security-page universe from the published stock sitemap without following
  any listed URL. `search_symbols_async()`/`search_symbols()` return bounded
  typed suggestions from the public JSON endpoint. Neither operation is
  implemented as an unfiltered screener crawl.

### Non-functional requirements

- **NFR-1:** Support and test Python 3.11, 3.12, 3.13, and 3.14.
- **NFR-2:** Core runtime dependencies are `fastreq[curl]`, PyArrow, and lxml.
  Core has no pandas, browser, database, terminal-UI, or disk-cache dependency.
- **NFR-3:** Structured outputs use deterministic versioned Arrow schemas;
  artifacts use immutable descriptors and explicit byte-download helpers.
- **NFR-4:** Parsers are pure, lxml-based, fixture-tested, header/structure
  driven, and independent of transport and environment configuration.
- **NFR-5:** Essential row provenance survives concatenation/storage; request
  provenance lives in immutable result metadata rather than relying on Arrow
  schema metadata.
- **NFR-6:** No automated login, browser execution, JavaScript execution,
  challenge solving, free-proxy discovery, identity randomization, sitemap
  crawling, or entitlement bypass.
- **NFR-7:** The library prints and logs nothing unsolicited. Typed progress and
  diagnostic callbacks are opt-in; Rich/tqdm adapters are optional extras.
- **NFR-8:** No telemetry or automatic external reporting.
- **NFR-9:** Public interfaces and dataset schemas follow semantic versioning
  after 1.0. Deprecations warn for at least one minor and remove only in a major
  release, except urgent security/access-control fixes.
- **NFR-10:** No commit, remote, push, release, or publication is part of this
  foundation documentation task.

## Public 1.0 scope

The scope is the verified public structured/image surface as of 2026-08-27.
Entirely new families discovered later do not automatically move the 1.0 goal;
they enter a reviewed later release unless explicitly promoted. Additive fields
within an existing family follow schema-evolution policy.

Supporting a public feature means bounded caller-requested access, not a
site-wide crawler. `finvizp` may completely paginate one explicit screen/feed,
but does not expose `fetch_every_ticker()`, sitemap enumeration, or bulk
publisher/archive crawling. News support stops at Finviz metadata and never
fetches third-party article bodies. Maps support returns structured map data,
not a visual renderer.

The one reviewed exception is symbol-universe discovery: `symbols()` may read
the single published `sitemap.xml?t=0&p=0` stock manifest and extract canonical
`/stock?t=...` entries. It must not follow those URLs, treat their `ty=oc`
duplicates as optionability evidence, or walk any other sitemap family. Reading
this finite manifest is not permission for sitemap-driven page crawling.

Robots directives are treated as meaningful automation guidance rather than
authentication. Direct caller-requested public routes may be implemented with
conservative pacing, but robots-disallowed routes are never used for automatic
crawling. Login/Elite redirects produce typed entitlement errors.

## Architecture

### Deep modules and seams

- `FinvizClient` owns `fastreq` transport, route/session state, browser profile,
  rate limiting, retries, cache, single-flight coordination, and optional
  caller-supplied authenticated state. It does not contain shallow endpoint
  pass-through methods.
- Endpoint modules expose module-level async functions and matching sync
  wrappers. They own query validation, canonical symbols, URL construction,
  pagination, batching, and bundle projection.
- Immutable typed query models encode screen/group filters, views, orders,
  signals, and requested columns using reviewed human-readable registries.
- Pure lxml parsers emit source-near records and structural fingerprints.
- A central versioned schema registry defines names, fields, Arrow types,
  units, nullability, raw companions, key hints, and temporal semantics.
- Arrow builders normalize records. Artifact builders describe chart/spectrum
  bytes without pretending they are structured history.

### Representation selection

Sources are selected per capability, with structured representations preferred
only when they preserve the complete contract. The priority is:

1. public first-party JSON/API;
2. public first-party XHR/fetch responses;
3. structured JSON embedded in the server response;
4. semantic HTML;
5. image/artifact responses.

A higher-priority representation is eligible only when it is available at the
same access tier, semantically complete for the method, directly callable
without browser execution or ephemeral credentials, no less snapshot-coherent,
and no more request-intensive than the selected lower-priority source. Every
choice is recorded in the capability manifest with fixture and bounded-live
evidence.

This is structured-first, not blindly JSON-first. Elite screener exports cannot
replace the public HTML screener; incomplete quote XHR cannot replace the
coherent stock-page bundle; third-party HTML-to-JSON services are never used.
Statements and symbol suggestions use their public JSON endpoints. Futures and
economic-calendar implementations prefer complete embedded structured data.
Maps use public first-party data endpoints only after per-endpoint verification.

No public arbitrary-request escape hatch is exposed. Advanced unsupported
requests use `fastreq` directly.

### Data flow

```text
caller input
  -> symbol/query validation and safety limit
  -> canonical query and cache key
  -> cache/single-flight decision
  -> route-explicit FinvizClient request
  -> response classification
  -> pure JSON/XML/HTML parser or artifact classifier
  -> schema-driven Arrow/bundle/artifact normalization
  -> immutable FetchResult[T]
```

One-shot functions create and close a transient client. Repeated work should
pass an explicit context-managed client so cookies, pacing, cache, routes, and
single-flight state are shared. One batch operation always uses one client.

### Result contract

`FetchResult[T]` is a frozen, slotted generic model with canonical `.data` and
validated `.table`/`.artifact` convenience accessors. It contains:

- `ResultStatus`: `COMPLETE`, `PARTIAL`, or recognized `EMPTY`;
- frozen request metadata;
- typed warning and unit-error records;
- completeness counts and safe attempt/retry information;
- access tier and freshness/cache facts.

`cache_hit` and `stale` are orthogonal to completeness. A valid empty result is
possible only when the parser positively recognizes the endpoint's no-results
state. Missing or unknown required structure raises parse drift.

Invalid input fails before network access. In strict mode any required
symbol/page failure raises with the partial result. With `allow_partial=True`,
at least one successful unit returns `PARTIAL`; an all-failed batch still
raises. `asyncio.CancelledError` propagates immediately and is never converted
to a provider error or retried.

### Symbols and result order

Inputs are trimmed and uppercased. Reviewed dot/slash class notation maps to
Finviz dash notation (`brk.b`, `BRK/B`, `BRK-B` -> `BRK-B`). Unknown punctuation
is rejected rather than repaired heuristically. Each canonical symbol is
fetched once; tables follow first canonical occurrence. Result metadata records
every input position, requested spelling, and canonical symbol.

### Arrow and normalization contract

- Public columns use stable semantic `snake_case` names.
- Physical rows use canonical `symbol` and UTC `fetched_at`; request aliases,
  source URL/query, access facts, parser version, schema version, response hash,
  and safe route facts live in result metadata unless row cardinality requires
  them physically.
- Percentages normalize to decimal fractions; compact numbers normalize to
  base units. Paired display values become separate typed fields.
- True counts use `int64`; prices, ratios, percentages, and heterogeneous
  statement values use `float64`.
- Known missing sentinels become Arrow null, not NaN. Unexpected conversions
  preserve the raw display and emit typed warnings; required conversion failure
  may raise `FinvizDataError`.
- Known lossy/ambiguous fields have explicit `_raw` companions. Additional
  unknown fields survive in `extra_fields: map<string, string>` and emit drift
  warnings. `strict_schema=True` promotes recoverable unknown/conversion/
  optional-region drift to typed errors.
- Quote, screener, group, and map observations are endpoint-native wide tables.
  Statements are long by metric/period. News, ratings, insider, and calendar
  outputs are event-row tables. Named screener views have fixed schemas; custom
  views assemble deterministic schemas from the checked-in column registry.
- The symbol-universe table has one non-null unique canonical `symbol` column,
  deterministically ordered. Symbol search has `symbol`, `company`, and
  `exchange` columns and preserves provider result ranking.
- Relative/time-only values anchor to the response date in
  `America/New_York`, convert to UTC only when unambiguous, and retain raw
  display plus parse status. Date-only values remain Arrow `date32`; BMO/AMC
  session labels remain separate.

Additive reviewed fields increment a dataset schema version and may ship in a
minor release. Semantic changes/removals require a major release after 1.0.
Provider surprises remain in `extra_fields` until reviewed.

## Cache and request policy

Ordinary requests use transparent endpoint-specific TTL caching:

- built-in approximate byte-bounded LRU memory cache with an optional entry
  cap;
- caller-supplied cache adapter seam, but no core disk implementation;
- parsed immutable results only—never ordinary raw authenticated bodies;
- cache key includes normalized endpoint/query, access/auth scope, safe route
  identity, browser profile, parser version, schema version, and representation;
- original `fetched_at` and response hash survive cache hits;
- `served_at`, cache age/hit/stale facts live in result metadata;
- per-call/client cache disable, refresh, and optional `stale_if_error` controls;
- stale fallback is disabled by default and always explicit in metadata;
- per-client single-flight coalesces identical concurrent misses;
- clear, invalidate, and safe statistics operations.

Raw response bytes are hashed with SHA-256 before parsing and then discarded.
Only an explicit scrubbed fixture-capture callback may retain raw bodies.

Retries use a typed bounded policy, honor `Retry-After`, and apply only to
reviewed transient transport/5xx failures and configured 429 behavior. Query,
parser, entitlement, access-control, and cancellation errors are not retried.
Exact TTL, concurrency, request-rate, retry, and symbol-safety defaults are
empirical implementation facts to establish through bounded probes.

## Proxies, authentication, and browser identity

Proxy precedence is:

```text
explicit client/per-call proxy
  > FINVIZP_PROXY
  > standard fastreq proxy environment
  > direct connection
```

`proxy=False` or `proxies=[]` forces direct access. Metadata contains only a
safe route fingerprint. Public pools may fail over only for transport failure;
403, 429, challenges, and entitlement responses stop/cool down instead of
switching identity. Authenticated state is caller-supplied, never discovered or
persisted, and remains pinned to one route. `finvizp` never accepts account
username/password or automates login.

One fixed configurable browser/TLS profile is pinned per client and included by
safe identifier in cache/request metadata. Identity randomization is forbidden.
Access tier is inferred from response evidence as `PUBLIC`, `AUTHENTICATED`,
`ELITE`, or `UNKNOWN`; cookies alone never prove Elite. Displayed delay/as-of
facts are preserved rather than inferred.

## Errors and diagnostics

The public hierarchy distinguishes transport, rate limit, blocked/challenge,
entitlement, not found, query, partial/batch, parse drift, and invalid data.
Immutable warning/error records carry stable codes and endpoint/symbol/page
context without credentials, cookie values, proxy URLs, authorization data, or
sensitive raw response bodies.

The library does not print or emit unsolicited application logs. Callers may
provide separate typed diagnostic and progress callbacks. Optional Rich/tqdm
adapters remain outside core dependencies and suppress terminal UI in cron/CI
while preserving headless callbacks.

## Testing and verification

- Default tests are hermetic and use minimal scrubbed structural fixtures.
- Current live structures and specifically justified recent fallback variants
  are covered; obsolete variants may be removed in a major release.
- Fixtures never include cookies, account/portfolio data, tracking identifiers,
  ads, unnecessary article text, or unapproved authenticated content.
- Parser, schema, bundle, projection, pagination, cache, single-flight, symbol,
  proxy, retry, cancellation, strict/partial, empty-result, and error-redaction
  behavior receive focused tests.
- Symbol tests cover XML namespace handling, duplicate `ty=oc` removal,
  canonical dash forms, unexpected URL shapes, deterministic order, bounded
  suggestion results, and the prohibition on following sitemap entries.
- `pytest -m live_public` performs one bounded sequential example per public
  family. `pytest -m live_elite` requires explicit legitimate session state.
- A small weekly public workflow reports network/access failures separately
  from parser drift and does not gate normal pull-request CI.
- Ordinary CI validates Python 3.11-3.14, tests, Ruff, ty, source/wheel builds,
  capability manifest, schemas, fixtures, and documentation links.

## Release milestones

1. **0.1:** client, errors/results/provenance/cache, symbol discovery, quote
   bundles, statements.
2. **0.2:** screener/query registries, pagination, signals, earnings screens.
3. **0.3:** groups, maps, ratings, news, insider, economic calendar/details.
4. **0.4:** charts/spectrum, forex, crypto, current futures data.
5. **0.5:** frozen public-surface and legacy parity audit, migration guide,
   schema hardening, and live-smoke matrix.
6. **1.0:** stable complete public release after every manifest entry is
   implemented, documented, and tested.

No compatibility classes or pandas adapter are planned. The migration guide
maps old methods to new functions and documents Arrow-native CSV/Excel
replacement workflows. Later publishing should use explicit tag-driven GitHub
Actions and PyPI trusted publishing, never automatic publication on merge.

## Out of scope

- Yahoo or multi-provider merging;
- database, Parquet, scheduler, crawler, or storage framework;
- third-party article-body collection;
- browser/SPA automation or JavaScript rendering;
- automated authentication, challenges, or entitlement bypass;
- broad sitemap/ticker/publisher crawling beyond the single reviewed symbol
  manifest read;
- pandas/legacy compatibility interface;
- arbitrary raw Finviz request interface;
- telemetry;
- unavailable pre-collection snapshot history;
- implementation plans, commits, remotes, pushes, releases, or publication in
  this foundation task.

## Open questions

None. Exact operational default values are deliberately deferred to bounded
implementation probes because they are empirical facts rather than product
decisions.
