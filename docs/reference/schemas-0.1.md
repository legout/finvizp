# Arrow schemas in 0.1

finvizp tables are built from a versioned, validated registry
(`src/finvizp/schema_registry.json`) exposed through `finvizp.schemas`.
Field order is deterministic, nullability and units are declared per field,
and every dataset carries `symbol` (non-null key) and `fetched_at` (non-null
UTC timestamp) provenance columns plus exactly one nullable `extra_fields`
map for additive provider fields.

## Reading a table

```python
import finvizp

result = finvizp.quote("AAPL", client=client)
snapshot = finvizp.snapshot("AAPL", client=client)
table = snapshot.table  # Arrow table, registered schema
table.schema  # deterministic field order
table.column("market_cap")  # float64 fraction-of-compact units
```

Typed units, not strings: `percent` columns are fractions (`0.5` means
50%), `compact` columns are absolute numbers (`2.5e9` means 2.5B), `count`
columns are `int64`, dates are `date32`, timestamps are UTC
`timestamp("us")`. Where the provider display is ambiguous, the verbatim
text survives in a `*_raw` companion column and a known-missing value is an
Arrow null — never NaN, never zero.

## Registered datasets (0.1)

| Dataset | Version | Rows are | Key relations |
|---|---|---|---|
| `symbol_universe` | 1 | one canonical symbol per row | — |
| `symbol_search` | 1 | one ranked suggestion | `symbol`, `company`, `exchange` |
| `statements` | 1 | one metric × period cell | `statement_kind`, `periodicity`, `metric`, `value`/`value_raw`, `currency` |
| `quote_snapshot` | 1 | one stock's identity/valuation snapshot | 50 columns, all six snapshot tables merged |
| `quote_description` | 1 | one stock's description | `description` |
| `quote_ratings` | 1 | one analyst action | `published_at` (+`_raw`/`_status`), `rating`, `analyst`, `price_target` |
| `quote_news` | 1 | one news event | `published_at`, `title`, `url`, `publisher` |
| `quote_insider` | 1 | one insider transaction | `transaction_date`, `transaction_type`, `cost`, `shares` |
| `quote_peers` | 1 | one peer symbol | — |
| `quote_etf_holders` | 2 | one ETF holding position | — |
| `quote_signals` | 1 | one signal/link descriptor | — |

The `QuoteBundle` returned by `quote()` holds one table per relation (or
`None` when the region is absent on the page) plus signal/artifact
descriptors; projection functions (`snapshot`, `ratings`, …) return the
single relation's table, derived from the same cached page with
`projected_from="quote"` provenance.

## Versioning contract

- `dataset_version(name)` is part of the cache key and of every result's
  `metadata.schema_version`; a registry bump invalidates cached results
  and tells you which schema a historical table used.
- Additive provider labels land in `extra_fields` with a `FetchWarning`,
  never silently dropped; `strict_schema=True` promotes drift (or a
  recoverable conversion failure) to `FinvizDataError` for callers who
  want contracts enforced over best-effort.
- Parser behavior is versioned in `metadata.parser_version`.

## Canonical symbol notation

Symbols are normalized (trimmed, uppercased, reviewed dot/slash class
notation mapped to dashes: `brk.b` → `BRK-B`), deduplicated, and positions
preserved in `metadata.symbols`. The provider's dash notation is the only
canonical form on the wire.
