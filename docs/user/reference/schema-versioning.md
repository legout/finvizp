# Schema versioning

`finvizp` builds every Arrow table from the validated registry at
`src/finvizp/schema_registry.json`.

The registry defines:

- dataset names and versions;
- ordered fields, Arrow types, units, and nullability;
- key and temporal hints;
- `_raw` companions and the `extra_fields` drift map;
- provenance columns such as `symbol` and `fetched_at`.

Tests keep the registry and the normalizer in sync:
`tests/test_schemas.py`, `tests/test_schema_contracts.py`, and
`tests/test_normalization_matrix.py`.

## Read the registry

```python
from finvizp import schemas

schemas.dataset_names()                 # deterministic order
schemas.dataset("quote_news")          # fields, versions, hints
schemas.arrow_schema("quote_news")     # pyarrow.Schema
schemas.dataset_version("quote_etf_holders")  # 2
```

A dataset version is part of the cache key and appears in
`result.metadata.schema_version`. Parser behavior has its own
`metadata.parser_version`.

## Registered datasets

| Dataset | Version | One row represents |
|---|---:|---|
| `symbol_universe` | 1 | One canonical symbol. |
| `symbol_search` | 1 | One ranked suggestion. |
| `statements` | 1 | One metric and period cell. |
| `quote_snapshot` | 1 | One stock snapshot. |
| `quote_description` | 1 | One stock description. |
| `quote_ratings` | 1 | One analyst action. |
| `quote_news` | 1 | One news event. |
| `quote_insider` | 1 | One insider transaction. |
| `quote_peers` | 1 | One peer symbol. |
| `quote_etf_holders` | 2 | One ETF holding position. |
| `quote_signals` | 1 | One signal or link descriptor. |
| `earnings_screen` | 1 | One earnings-dated ranked symbol. |
| `economic_calendar` | 1 | One scheduled release. |
| `economic_details` | 1 | One release detail row. |
| `futures_tiles` | 1 | One futures contract tile. |

## Units and missing values

| Unit | Stored representation | Example |
|---|---|---|
| `percent` | Fraction | `0.5` means 50%. |
| `compact` | Absolute number | `2.5e9` means 2.5B. |
| `count` | `int64` | A row count. |
| `date` | Arrow `date32` | A calendar date. |
| `timestamp` | UTC `timestamp("us")` | A point in time. |

When the provider's display is ambiguous, the typed value is null and the
exact display stays in a nullable `*_raw` column. Timestamp fields also carry
a `*_status` column.

Known-missing values are Arrow nulls. They are never represented as zero or
NaN.

Additive provider labels go into `extra_fields` with a `FetchWarning`.
`strict_schema=True` turns recoverable drift into `FinvizDataError`.

## Temporal status values

The normalizer uses these statuses for time displays:

- `anchored`: time-only display anchored to the response date in
  `America/New_York`;
- `exact`: complete local date and time;
- `ambiguous`: DST fold or gap, so the typed instant stays null;
- `relative`: relative display resolved against the fetch time;
- `date_only`: date known, time intentionally unknown.

## Change a schema

1. Edit `schema_registry.json`.
2. Bump the dataset version when an existing field changes meaning, type, or
   row identity. Additive optional fields may keep the version.
3. Update the pinned-version guard in `tests/test_schema_contracts.py`.
4. Run the focused contract and normalization tests:

   ```bash
   uv run pytest tests/test_schema_contracts.py \
     tests/test_normalization_matrix.py tests/test_schemas.py
   ```

5. Update this page's dataset table. The docs and integrity tests catch broken
   links and missing references.
