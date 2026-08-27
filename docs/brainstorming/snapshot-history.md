# Snapshot history and provenance

## Problem

Finviz exposes many useful statistics only as their current value: beta,
trailing and forward EPS, shares and float, short interest, margins, returns,
valuation, ownership, analyst targets, and screen membership. Users naturally
ask for their history, but a client cannot retrieve provider snapshots that the
provider does not publish.

`finvizp` makes future history possible and honest through typed observation
metadata. Persistence remains entirely caller-owned.

## History taxonomy

### Provider period history

Finviz supplies fiscal periods or event dates. Examples:

- annual and quarterly statements;
- ratings/action rows;
- insider transactions;
- economic releases;
- news timestamps.

Store both the provider's period/event time and `fetched_at`. Re-fetching can
reveal corrections or changed windows.

### Accumulated snapshot history

The upstream page supplies the current observation. Repeated captures create a
history starting at the first run. Examples:

- quote statistics;
- screener metrics and ranks;
- group aggregates;
- signal membership;
- peers and ETF-holder links;
- forex/crypto/futures performance snapshots.

The observation identity must not be confused with the business date. A value
captured at 18:00 UTC may reflect a delayed quote, an hourly-recalculated
fundamental, or a source date displayed elsewhere.

### Derived history

A consumer calculates values from bars or statements. `finvizp` does not own
that derivation, though it can expose Finviz's displayed values for comparison.

## Required temporal fields

Every structured row that may be concatenated across captures needs
`fetched_at` as a timezone-aware UTC timestamp.
When the provider supplies more timing, retain it separately:

- `as_of_date` or `period_end_date`;
- `event_at` / `event_date`;
- `filing_at`;
- `quote_at` if the page exposes it;
- `displayed_time_raw` when parsing is ambiguous;
- `is_estimate` where Finviz distinguishes estimates.

Do not infer a fiscal or source date from `fetched_at`. A transparent-cache hit
retains the original network fetch time; `served_at`, cache age, and stale state
belong in `FetchResult.metadata` and never create a false new observation.

## Provenance and deduplication

Caller storage can use these identity ingredients:

```text
endpoint family
provider symbol
resolved query/view
provider period or event identity, if any
fetched_at or collection run ID
SHA-256 response hash
normalized row hash
parser and dataset schema version
```

Two complementary representations are useful:

1. **raw capture identity:** one hash for the response body, suitable for
   replaying parsers and suppressing byte-identical responses;
2. **row observation identity:** a hash of normalized source fields excluding
   volatile collection metadata, suitable for detecting changed values.

A storage consumer can choose:

- append every capture;
- append only when response hash changes;
- append only when a row's value hash changes;
- maintain current and history tables separately.

Core `finvizp` exposes the ingredients and imposes no database merge strategy.

## Candidate table shapes

### Wide quote observations

One row per symbol and capture with stable commonly used columns. This is
convenient for analytics but requires versioned schema evolution as Finviz adds
fields.

### Long metric observations

```text
symbol
metric
value_numeric
value_text
unit
provider_as_of
fetched_at
response_hash
```

This handles field drift but is less convenient and can erase relationships
among paired values. It is not the canonical quote result; additive unknowns
belong in the reviewed `extra_fields` seam.

### Confirmed direction

Return deterministic endpoint-native tables. Quote/screener/group/map
observations are wide; statements are long; news/ratings/insider/calendar are
event rows. Known lossy fields have explicit `_raw` companions. Additive
unknowns remain in `extra_fields: map<string, string>` until reviewed.

Statements should be long by default because source metric rows vary naturally
by company and statement type.

## Persistence boundary

`finvizp` returns provenance-rich Arrow tables and immutable request metadata.
It does **not** provide:

- a Parquet snapshot helper;
- a database or object-store adapter;
- a pluggable sink protocol;
- merge/upsert/change-suppression policy;
- scheduling or retention.

Callers use PyArrow Dataset, DuckDB, Polars, a lakehouse, or their own storage.
Documentation may show examples, but no persistence interface belongs to core.

## Point-in-time correctness

To support honest historical analysis:

- never overwrite an old observation merely because a newer fetch exists;
- retain revisions to period rows when their normalized value changes;
- preserve the source's displayed estimate/actual status;
- retain query and rank for screen results;
- avoid filling Finviz nulls from another provider inside `finvizp`;
- retain canonical Finviz `symbol` and the requested-to-provider mapping;
- record access tier because free/Elite values can differ in delay and depth;
- record parser/schema version to explain historical structural changes.

## Example use cases

### Beta and float history

A daily quote/screener capture creates beta, shares outstanding, float, and
short-interest observations. No pre-first-capture history is implied.

### Earnings revisions

Repeated captures of forward EPS, revenue growth, analyst targets, or earnings
dates reveal changes. Period-labeled statement rows remain a separate history.

### Insider transactions

The natural event identity should prefer the SEC URL/filing identifier plus
transaction details. `fetched_at` records when Finviz exposed the event. If a
recent-window page drops an older event later, absence is not deletion.

### Signal membership

Capture the resolved signal query, rank, and timestamp. A symbol entering or
leaving a signal is derived from successive result sets; the package should not
interpret the transition as a trade recommendation.

## Confirmed temporal and metadata rules

- Physical rows carry canonical `symbol`, UTC `fetched_at`, and provider
  period/event/as-of fields when applicable.
- Request URL/query, access tier, safe route, browser profile, parser/schema
  versions, response hash, cache facts, and input-resolution map live in frozen
  result metadata unless their cardinality requires a row field.
- Essential history does not depend on Arrow schema metadata because common
  concatenation/storage operations may discard it.
- Relative/time-only display values anchor to the response date in
  `America/New_York`; convert to UTC only when unambiguous and retain raw plus
  parse status.
- Date-only fields remain Arrow `date32`; earnings session labels such as BMO
  and AMC remain distinct from dates/times.
- Access tier is inferred from response evidence as public, authenticated,
  Elite, or unknown. Cookies alone never prove Elite.
- Raw response bytes are SHA-256 hashed before parsing and discarded unless an
  explicit scrubbed-fixture callback captures them.
