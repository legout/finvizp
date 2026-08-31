# Arrow schemas in 0.1

`finvizp` exposes versioned Arrow tables through `finvizp.schemas`. Field order,
nullability, units, and provenance are deterministic.

Every dataset contains non-null `symbol` and UTC `fetched_at` fields, plus one
nullable `extra_fields` map for additive provider fields.

## Read a table

```python
import finvizp

snapshot = finvizp.snapshot("AAPL")
table = snapshot.table

print(table.schema)
print(table.column("market_cap"))
```

## Units and missing values

| Provider display | Arrow representation | Example |
|---|---|---|
| Percent | Fraction | `0.5` means 50%. |
| Compact number | Absolute number | `2.5e9` means 2.5B. |
| Count | `int64` | A row count. |
| Date | `date32` | A calendar date. |
| Timestamp | UTC `timestamp("us")` | A point in time. |

Ambiguous displays keep their original text in a nullable `*_raw` column and
use null for the typed value. Timestamp fields also carry `*_status`.
Known-missing values are null, never zero or NaN.

## Registered datasets

| Dataset | Version | One row represents |
|---|---:|---|
| `symbol_universe` | 1 | One canonical symbol. |
| `symbol_search` | 1 | One ranked suggestion. |
| `statements` | 1 | One metric and period cell. |
| `quote_snapshot` | 1 | One stock identity and valuation snapshot. |
| `quote_description` | 1 | One stock description. |
| `quote_ratings` | 1 | One analyst action. |
| `quote_news` | 1 | One news event. |
| `quote_insider` | 1 | One insider transaction. |
| `quote_peers` | 1 | One peer symbol. |
| `quote_etf_holders` | 2 | One ETF holding position. |
| `quote_signals` | 1 | One signal or link descriptor. |

The full list, including later milestone datasets, is in
[Schema versioning](schema-versioning.md).

## Quote bundles and projections

`quote()` returns a `QuoteBundle` containing one table per available relation.
`None` means that relation was absent from the page.

Projection functions such as `snapshot()`, `ratings()`, and `news()` return one
relation and can reuse the cached quote page. Their metadata includes
`projected_from="quote"`.

## Versioning rules

- `dataset_version(name)` is part of the cache key and appears in
  `metadata.schema_version`.
- Additive provider labels go to `extra_fields` with a `FetchWarning`.
- `strict_schema=True` promotes recoverable drift to `FinvizDataError`.
- Parser behavior is tracked separately in `metadata.parser_version`.

## Canonical symbols

The client trims and uppercases symbols, maps reviewed dot/slash notation to
the provider's dash form (`brk.b` becomes `BRK-B`), deduplicates requests, and
preserves input positions in `metadata.symbols`.
