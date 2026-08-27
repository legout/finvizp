# Architecture

## Chosen approach

A capability-complete clean client organized by endpoint family. Callers learn
module-level async functions, matching sync wrappers, immutable queries, and one
generic result envelope. Transport, pagination, caching, parsing, normalization,
and diagnostics stay local to deep modules.

## Proposed package shape

```text
src/finvizp/
  __init__.py
  client.py
  cache.py
  errors.py
  models.py
  results.py
  schemas.py
  arrow.py
  quote.py
  statements.py
  screener.py
  groups.py
  maps.py
  news.py
  insider.py
  earnings.py
  calendar.py
  forex.py
  crypto.py
  futures.py
  artifacts.py
  _parsers/
    quote.py
    statements.py
    tables.py
    embedded_json.py
    events.py
  _queries/
    screener.py
    groups.py
```

This is a design map, not an implementation plan. Combine shallow proposed
files and split implementation that becomes too large to reason about locally.

## External seam

### Client

`FinvizClient` owns transport configuration and session state:

- `fastreq.FastRequests` with the curl backend;
- explicit proxies and per-route sessions/cookies;
- timeout, per-client concurrency/rate limits, typed bounded retries;
- optional caller-supplied authenticated state;
- fixed configurable browser/TLS profile;
- proxy resolution and route-local auth state;
- byte-bounded result cache and single-flight coordination;
- access/freshness metadata and explicit raw-fixture capture hook;
- async context-manager lifecycle.

Endpoint functions accept a reusable client. If omitted, they create and close
a transient client, matching `yfin` conventions. `FinvizClient` has no shallow
domain pass-through methods and no public arbitrary-request escape hatch.

### Structured endpoint functions

Representative interface direction:

```python
await quote_async(["AAPL", "brk.b"], client=client)
await statements_async("AAPL", statement="income", period="annual", client=client)
await screen_async(query, client=client)
await groups_async(group="sector", view="valuation", client=client)
await maps_async(view="s&p500", client=client)
await news_async(client=client)
await insider_async(kind="latest", client=client)
```

Sync wrappers expose the same concepts and fail clearly when invoked inside an
active event loop.

Every network function returns immutable `FetchResult[T]`. `T` may be a
`pyarrow.Table`, compound `QuoteBundle`/`MapBundle`, or `Artifact`. `.data` is
canonical; `.table` and `.artifact` are validated convenience accessors.

### Query models

Screener and group queries deserve typed immutable models because valid fields,
filters, ordering, views, and pagination interact. The interface should accept
human-readable names and hide Finviz URL codes. It should also preserve the
resolved provider query in table provenance.

Avoid a method per filter or signal. One deep screener module should validate
and encode all supported combinations.

### Compound bundles and projections

One stock page yields multiple relations. `QuoteBundle` contains snapshot,
description, ratings, news, insider, peers, ETF-holder relationships,
signals/links, and artifact descriptors. Multi-symbol calls concatenate each
relation by kind. Projection functions such as ratings/news reuse the canonical
bundle cache and preserve original fetch/hash metadata rather than refetching.

Maps similarly return a structured `MapBundle`; `finvizp` does not reproduce
the interactive visual renderer.

## Internal seams

### Transport versus endpoint parsing

The transport returns a classified response envelope. Endpoint modules select
JSON/API, XHR/fetch JSON, embedded JSON, semantic HTML, XML, or artifact bytes
according to the normative representation policy. They pass content to pure
direct-lxml/JSON parsers. Parsers never instantiate a client, cache, or read
environment variables.

Selection is contract-first: a structured representation wins only when it is
first-party, same-tier, complete, directly callable without browser execution
or ephemeral credentials, no more request-intensive, and no less
snapshot-coherent. The capability manifest records the representation and
evidence per method. Incomplete XHR and Elite-only exports never displace a
complete public HTML contract.

### Parsing versus Arrow normalization

Pure parsers emit source-near rows with raw labels/strings. Arrow builders own:

- versioned schema-registry names and deterministic field order;
- numeric, date, timestamp, boolean, and null coercion;
- fractions/base-unit normalization and paired-field splitting;
- explicit `_raw` companions and additive `extra_fields` retention;
- physical row provenance and immutable request metadata;
- empty-table schemas.

This permits parser drift tests without pyarrow concerns and schema tests
without network/HTML concerns.

### Artifacts

Charts and spectrum images are not shoehorned into tabular rows. An `Artifact`
contains source URL, kind, symbol/group, timeframe, chart type, media type,
fetched time, content length/hash, and optional bytes/path. Downloading is a
separate explicit operation.

## Data flow

```text
caller query
  -> endpoint validation
  -> symbol normalization, safety limit, URL/query encoding
  -> cache and single-flight decision
  -> FinvizClient request on explicit route
  -> response classification (success/rate/block/entitlement/not-found)
  -> pure JSON/XML/HTML parser or artifact classifier
  -> source-near records + structural fingerprint
  -> schema-driven Arrow/bundle/artifact builder
  -> immutable FetchResult[T]
```

For paginated screens, each page is parsed and normalized independently, then
concatenated with stable schemas. Page/rank metadata is attached before
concatenation. The collector stops from provider pagination evidence and row
counts, not a hard-coded universe size.

Symbol-universe collection is not screener pagination. It reads the single
published stock sitemap manifest, extracts canonical `/stock?t=...` entries,
removes the duplicate `ty=oc` variants, validates and deterministically orders
symbols, and follows no listed page. Symbol search calls the bounded public
suggestions JSON endpoint and preserves provider ranking.

## Result and Arrow contracts

`FetchResult[T]` contains frozen metadata, typed warnings/errors, completeness
counts, safe retry facts, and `ResultStatus` (`COMPLETE`, `PARTIAL`, `EMPTY`).
`cache_hit`, `stale`, and `AccessTier` are orthogonal. Recognized empty data
requires positive endpoint evidence; an unknown missing table is parse drift.

Strict completeness is default. `allow_partial=True` returns partial data only
when at least one unit succeeded; all-failed batches raise. Strict exceptions
carry the immutable partial result. Cancellation propagates immediately.

Common physical row fields include:

```text
symbol: string
fetched_at: timestamp[us, UTC]
```

Request metadata carries resolved URL/query, access/auth tier, safe route,
browser profile, parser/schema versions, response hash, cache facts, and the
requested-to-canonical symbol mapping. Essential history never depends on Arrow
schema metadata.

Dataset-specific physical fields add:

- screen query hash, view, page, and rank;
- statement kind, periodicity, period label/end/length, currency, metric;
- event display timestamp and parsed timestamp;
- relationship source/target;
- explicit raw display/status when normalization can lose meaning.

Percentages are fractions; compact values are base units. Counts use `int64`;
continuous values use `float64`. Known missing sentinels are null. Unknown
additive fields remain in `extra_fields: map<string, string>` and warn;
`strict_schema=True` promotes recoverable drift to errors.

Named views have fixed schemas. Custom screens deterministically assemble a
schema from the reviewed checked-in column registry. A developer drift command
compares live page metadata with the registry and emits a reviewable diff.

## Cache

The per-client transparent cache stores parsed immutable results, never ordinary
raw authenticated bodies. The built-in adapter is an approximate byte-bounded
LRU with optional entry cap; callers may supply another adapter, but core ships
no disk cache. Keys isolate endpoint/query, access/auth, route, browser profile,
parser/schema version, and representation.

Cache hits retain original `fetched_at`/hash and add served-at/age facts.
Endpoint-specific TTLs, disable/refresh, and explicit stale-if-error are
supported; stale fallback is off by default. Single-flight coalesces identical
concurrent misses.

## Error handling

The public hierarchy should let callers catch broadly or narrowly:

```text
FinvizError
  FinvizTransportError
  FinvizRateLimitError
  FinvizBlockedError
  FinvizEntitlementError
  FinvizNotFoundError
  FinvizQueryError
  FinvizPartialError
  FinvizBatchError
  FinvizParseError
  FinvizDataError
```

Errors carry endpoint/query context but never cookies, authorization values,
proxy credentials, or response bodies that may contain secrets. Parser errors
should include expected headers/region and the observed structural fingerprint.

Warnings/errors are immutable typed records with stable codes and safe context.
They never contain cookies, authorization, proxy URLs/credentials, or sensitive
raw bodies. The package prints/logs nothing unsolicited; callers may supply a
typed event callback.

## Testing strategy

### Hermetic tests

- scrubbed HTML, JSON, and image-header fixtures;
- parser tests for current and specifically justified recent fallback structures;
- all six quote snapshot tables and reordered-table variants;
- statement period alignment, currency, blanks, signs, commas, TTM, and mixed
  metric types;
- screener pagination, custom columns, duplicate labels, no results, and rank;
- group dimensions/views and spectrum discovery;
- embedded JSON calendar/futures variants;
- percent/unit/date/time normalization;
- entitlement, 403 challenge, 429 retry-after, timeout, and malformed content;
- route-local cookie/proxy behavior through a fake fastreq seam;
- cache key/isolation/TTL/refresh/stale/LRU/single-flight behavior;
- multi-symbol normalization, dedupe, order, safety limits, and partial errors;
- quote/map bundles and cache-preserving projections;
- deterministic Arrow schemas and empty tables;
- sync-wrapper behavior in and outside an event loop.

### Fixture drift tooling

A developer-only command may refresh fixtures from explicitly selected public
or authenticated endpoints. It must scrub secrets and never run as part of the
default test suite.

### Live smoke tests

Opt-in tests should cover one bounded representative call per family, with
public and authenticated suites separated. Live smokes prove current access and
shape only; they do not replace fixtures or run in standard pull-request CI. A
small weekly public workflow distinguishes network/access failures from parser
drift.

## Progress and large collections

As with `yfin`, progress UI is optional and absent from core dependencies.
Endpoint functions can accept:

```python
progress: None | bool | Literal["rich", "tqdm"] = None
progress_callback: Callable[[int, int], Any] | None = None
```

Screen pagination should permit headless page progress. Per-ticker collections
should preserve requested order and partial errors. No endpoint should print
progress directly.

## Documentation architecture

The eventual user documentation should follow Diátaxis:

- tutorials: first quote, first screen, first snapshot history;
- how-to: proxies, cache, authenticated session, pagination, raw fixtures,
  caller-owned Arrow/Parquet persistence, migration from `finvizfinance`;
- reference: functions, schemas, filters/signals, errors;
- explanation: architecture, provenance/history, access policy, parser drift.
