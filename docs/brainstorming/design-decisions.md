# Confirmed design decisions

**Confirmed:** 2026-08-27 after nine grilling rounds

This register summarizes the decisions governing implementation plans. The
normative contract is the
[approved foundation spec](../superpowers/specs/2026-08-27-finvizp-foundation-design.md).

## Product and releases

- Public open-source PyPI package named `finvizp`, meaning “Finviz for Python.”
- Support Python 3.11-3.14.
- Finviz-only; no Yahoo import, merge, fill, or canonical truth layer.
- Public endpoints first. Elite is non-blocking and unsupported until verified
  with legitimate caller-supplied authenticated state.
- Public 1.0 covers the complete frozen 2026-08-27 public inventory, not merely
  `finvizfinance` parity.
- 0.x milestones deliver core, screeners, events/maps, alternative assets, then
  parity hardening. 1.0 requires a machine-readable manifest with every entry
  implemented, documented, and tested.
- Capability compatibility, not legacy signature compatibility. Migration guide
  only; no pandas or `finvizfinance` shim.
- Post-1.0 deprecation requires at least one minor warning period and removal in
  a major release, except urgent security/access fixes.
- Eventual publishing is tag-driven trusted publishing after explicit approval;
  never automatic on merge.

## Public interface

- Module-level async endpoint functions plus matching sync wrappers.
- `FinvizClient` owns transport/session concerns and is passed to endpoint
  functions; no hidden global client and no endpoint pass-through methods.
- One-shot calls use a transient client; repeated work uses an explicit reusable
  context-managed client.
- Symbol endpoints accept one symbol or an iterable, apply a configurable safety
  limit, normalize reviewed dot/slash class notation to Finviz dashes,
  deduplicate canonical symbols, and preserve input-resolution metadata.
- One generic immutable `FetchResult[T]` with `.data` plus typed `.table` and
  `.artifact` accessors. Status is `COMPLETE`, `PARTIAL`, or recognized `EMPTY`;
  cache-hit/stale/tier are orthogonal.
- Strict completeness is default. `allow_partial=True` returns a partial result
  only when something succeeded; all-failed batches still raise.
- Compound pages produce bundles. `QuoteBundle` owns snapshot, ratings, news,
  insider, peers, ETF-holder relationships, description, signals/links, and
  artifacts. Projection functions reuse the bundle/cache/provenance.
- Typed immutable screen/group query models and reviewed registries hide Finviz
  URL codes. No public arbitrary raw-request escape hatch.
- Dedicated `symbols_async()`/`symbols()` read the single published stock
  sitemap without following entries. Dedicated `search_symbols_async()`/
  `search_symbols()` use bounded public JSON suggestions.

## Data and schemas

- Endpoint-native table shapes: wide quote/screener/group/map observations,
  long statements, and event-row news/rating/insider/calendar data.
- Central checked-in versioned schema registry generates/validates Arrow
  contracts and reference docs.
- Stable semantic `snake_case`; canonical `symbol` and UTC `fetched_at` in rows.
- Request/source/access/parser/schema/hash/route facts live in immutable result
  metadata unless row cardinality requires a physical field.
- Percentages are decimal fractions; compact values are base units; paired
  values split into explicit fields.
- `int64` for real counts; `float64` for prices, ratios, percentages, and mixed
  statement metrics.
- Known missing sentinels become Arrow null. Unexpected conversions preserve raw
  values and warn; required conversion failures can raise.
- Known lossy fields have explicit `_raw` companions. Unknown additive fields
  survive in `extra_fields: map<string, string>` and warn.
- `strict_schema=True` promotes recoverable unknown/conversion/optional-region
  drift to errors. Missing required structure always raises.
- Relative/time-only values anchor to the response date in
  `America/New_York`, convert to UTC only when unambiguous, and retain raw and
  parse status. Date-only values remain `date32`; BMO/AMC remain separate.
- Additive reviewed fields may ship in a minor schema version; semantic
  changes/removals require a major release after 1.0.

## Transport, cache, and access

- Core dependencies: `fastreq[curl]`, PyArrow, and direct lxml.
- Representation selection is structured-first but contract-first: prefer
  public first-party JSON/API, then XHR/fetch JSON, embedded JSON, semantic
  HTML, and artifacts, only when the higher-priority source is same-tier,
  complete, direct, request-efficient, and snapshot-coherent.
- Elite exports, incomplete XHR, and third-party HTML-to-JSON services never
  replace a complete public method contract.
- Fixed configurable browser/TLS profile per client; no identity randomization.
- Proxy precedence: explicit > `FINVIZP_PROXY` > standard fastreq environment >
  direct. Explicit false/empty forces direct.
- Public proxy failover only for transport errors. Never switch route after
  403/429/challenge/entitlement. Authenticated state is route-pinned.
- Caller supplies cookies/session state; no username/password, environment
  credential discovery, login automation, browser, JavaScript, or challenge
  solving.
- Transparent endpoint-specific TTL cache: parsed immutable results, bounded
  in-memory LRU, optional caller adapter, no core disk implementation.
- Cache keys isolate query, auth/access, route, browser, parser, schema, and
  representation. Cache hits retain original fetch time/hash and report serve
  time/age. Stale-if-error is explicit and off by default.
- Per-client single-flight coalesces identical misses. Cancellation propagates
  and is never retried or recorded as a provider failure.
- Raw bodies are SHA-256 hashed then discarded; only explicit scrubbed fixture
  capture retains content.
- Typed bounded retry policy; no retry for parser/query/entitlement/access/
  cancellation failures.
- Exact TTL/rate/concurrency/retry/safety defaults come from bounded probes.

## History and persistence

- Distinguish provider period/event history, accumulated current-value snapshots,
  and locally derived history.
- `fetched_at` never substitutes for provider period/event/as-of time.
- Cache hits retain original `fetched_at`; they do not create false observations.
- No database, Parquet helper, sink protocol, scheduler, or persistence layer.
  Callers own storage, append/change suppression, and cross-provider comparison.

## Responsible-use boundary

- Public 1.0 is bounded caller-requested access, not site-wide crawling.
- No sitemap/ticker/publisher/fund/manager/archive crawler. The narrow reviewed
  exception reads the single stock sitemap as a symbol manifest and follows no
  entry or sibling sitemap.
- Finite endpoint pagination is supported under conservative limits.
- News ends at Finviz metadata; no third-party article bodies.
- Maps return structured data, not a renderer.
- Robots directives constrain automation. Direct caller-requested public routes
  may be supported; automatic crawling of disallowed routes is not.
- Login/Elite-only features remain manifest-gated until verified; no stubs or
  guessed schemas.
- No telemetry or automatic external reporting.

## Testing and operations

- Pure lxml parsers with minimal scrubbed structural fixtures.
- Current structure plus justified recent fallback variants; no indefinite
  obsolete-markup burden.
- Separate typed diagnostic and progress callbacks; optional Rich/tqdm adapters,
  no unsolicited printing/logging.
- Hermetic ordinary tests; separately gated public and authenticated live tests.
- Small weekly public smoke reports access/network failures separately from
  parser drift and does not gate pull-request CI.
- Ordinary CI covers Python 3.11-3.14, pytest, Ruff, ty, build, capability
  manifest, schemas, fixtures, and docs links.

## Empirical facts deferred to implementation probes

These are intentionally not open product decisions:

- endpoint-specific cache TTLs;
- default concurrency and requests per second;
- retry counts/backoff caps;
- symbol safety-limit count;
- endpoint-specific response/page-size limits;
- authenticated Elite schemas and behavior.
