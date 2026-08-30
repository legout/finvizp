# Schema versioning

Every Arrow table finvizp returns is built from a versioned, validated
registry: `src/finvizp/schema_registry.json`. The registry is the single
source of truth for dataset names, schema versions, ordered fields, Arrow
types, units, nullability, key/temporal hints, `_raw` companions, the
`extra_fields` drift map, and the provenance columns (`symbol`, `fetched_at`).
Tests enforce the registry contract continuously — see
`tests/test_schemas.py`, `tests/test_schema_contracts.py`, and
`tests/test_normalization_matrix.py`.

## Reading the registry

```python
from finvizp import schemas

schemas.dataset_names()  # deterministic dataset order
schemas.dataset("quote_news")  # one Dataset: fields, versions, hints
schemas.arrow_schema("quote_news")  # deterministic pyarrow.Schema
schemas.dataset_version("quote_etf_holders")  # 2
```

A dataset version is part of the cache key and of every result's
`metadata.schema_version`. A registry bump invalidates cached results and
tells you which schema a historical table used. Parser behavior is versioned
separately in `metadata.parser_version`.

## Current registered datasets (observed 2026-08-27)

| Dataset | Version | Rows are | Temporal fields |
|---|---|---|---|
| `symbol_universe` | 1 | one canonical symbol | — |
| `symbol_search` | 1 | one ranked suggestion | — |
| `statements` | 1 | one metric × period cell | `period_end_date` |
| `quote_snapshot` | 1 | one stock's snapshot (six tables merged) | `ex_dividend_date`, `ipo_date` |
| `quote_description` | 1 | one stock's description | — |
| `quote_ratings` | 1 | one analyst action | `published_at` (+`_raw`/`_status`) |
| `quote_news` | 1 | one news event | `published_at` (+`_raw`/`_status`) |
| `quote_insider` | 1 | one insider transaction | `transaction_date` |
| `quote_peers` | 1 | one peer symbol | — |
| `quote_etf_holders` | 2 | one ETF holding position | — |
| `quote_signals` | 1 | one signal/link descriptor | — |
| `earnings_screen` | 1 | one ranked earnings-dated symbol | `earnings_date` |
| `economic_calendar` | 1 | one scheduled release | `release_date`, `release_timestamp` (+`_status`), `reference_date` |
| `economic_details` | 1 | one release's detail row | same as calendar |
| `futures_tiles` | 1 | one futures contract tile | verbatim sparkline text, `delay_minutes` |

## Units, companions, and temporal honesty

- Typed units, not strings: `percent` columns are fractions (`0.5` means
  50%), `compact` columns are absolute numbers (`2.5e9` means 2.5B), `count`
  columns are `int64`, `date` columns are `date32`, `timestamp` columns are
  UTC `timestamp("us")`.
- `raw: true` promises a nullable `*_raw` string companion carrying the
  provider's exact display; timestamp fields with `raw: true` also carry a
  nullable `*_status` text column.
- Builder parse statuses are `anchored` (time-only display anchored to the
  provider response date in `America/New_York`), `exact` (full local
  datetime), and `ambiguous` (DST fold/gap: no unambiguous UTC instant; the
  typed value stays null and the raw display survives). Parser layers add
  `relative` and `date_only` verdicts for displays they resolve or decline to
  invent (see `docs/reference/groups-maps-events.md` for the news family).
- A known-missing value is an Arrow null — never NaN, never zero. Additive
  provider labels land in `extra_fields` with an `unknown_field`
  `FetchWarning`; `strict_schema=True` promotes drift to `FinvizDataError`.

## Registry validation rules

Loading a registry entry fails loudly when: a dataset name/version/fields
shape is invalid; a type or unit is outside the closed vocabulary; a unit and
type disagree; a temporal hint disagrees with the unit; a key is nullable;
`symbol`/`fetched_at` provenance columns are missing or mis-typed; `raw: true`
has no `*_raw` companion (or a timestamp lacks `*_status`); a `_raw` column
has no valid nullable base; or `extra_fields` is absent/duplicated. Versions
are positive integers and never reused for a different field list.

## Changing a schema

1. Add, remove, or retype fields in `schema_registry.json`.
2. Bump the dataset's `version` (additive optional fields may keep the
   version; anything that changes an existing column's meaning or a table's
   row identity must bump).
3. Update the pinned-version guard in `tests/test_schema_contracts.py`
   deliberately — it fails on accidental bumps.
4. Re-run `uv run pytest tests/test_schema_contracts.py
   tests/test_normalization_matrix.py tests/test_schemas.py`: complete,
   partial, and empty shapes must still share the registered schema, and the
   normalization matrix must stay family-independent.
5. Update this page's dataset table; `scripts/check_docs.py` and the docs
   integrity tests keep links honest.
