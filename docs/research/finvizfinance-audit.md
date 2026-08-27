# `finvizfinance` 1.4.0 audit

## Purpose

`finvizfinance` is the best available executable inventory of Finviz's public
web surfaces. `finvizp` should preserve its capability breadth, not its public
interface or internal architecture.

The audited revision was commit
`c8d461d1991da1675edc63ea0238391e6f0ba776` on 2026-08-27. The package declares
version 1.4.0 and depends on pandas, requests, Beautiful Soup, and lxml.

## Capability inventory

The package implements:

- per-ticker quote snapshots, description, peers, ETF-holder links, ratings,
  news, insider rows, signals, and chart images;
- income, balance-sheet, and cash-flow statements in annual and quarterly
  forms through `/api/statement.ashx`;
- overview, valuation, financial, ownership, performance, technical, custom,
  and ticker-list screeners;
- sector/industry/country/capitalization group overview, valuation,
  performance, custom, and spectrum views;
- global news/blogs and insider-trading tables;
- earnings-period partitioning built on screener queries;
- an economic calendar parser supporting legacy HTML and current embedded JSON;
- forex and crypto performance tables and chart images;
- futures performance for daily, weekly, monthly, quarterly, half-year, and
  yearly windows.

## Strengths worth carrying forward

### A broad requirements inventory

The package names the user-visible Finviz concepts and encodes many filter,
signal, order, group, and custom-column mappings. Those constants are useful as
research input, though they must be verified against the current site rather
than copied blindly.

### Typed failure direction

Recent code distinguishes a structural parse failure from a Cloudflare/rate
wall. Required and optional regions are treated differently, and several
parsers locate tables by headers instead of global position.

### Hermetic parser fixtures

The test harness injects a fake requests-compatible session and covers valid
HTML, structural drift, challenge pages, timeouts, and selected pagination
cases. A fresh local run passed 91 tests with 14 warnings. These tests are
fixture tests, not live compatibility tests.

### The statement JSON endpoint

`Statements.get_statements()` correctly identifies the six combinations:

| Statement | Annual | Quarterly |
|---|---|---|
| Income | `IA` | `IQ` |
| Balance sheet | `BA` | `BQ` |
| Cash flow | `CA` | `CQ` |

A live AAPL probe returned structured data for `IA`, `BQ`, and `CA`.

## Live defects and architectural limitations

### Current quote pages are split into six snapshot tables

The current AAPL page contained six tables with the same `snapshot-table2`
class:

1. identity, market capitalization, enterprise value, dividends, employees;
2. valuation and liquidity;
3. earnings and sales growth;
4. ownership, returns, margins, and moving averages;
5. shares, float, short interest, price range, volume, and event fields;
6. multi-window performance and current trading fields.

`ticker_fundament()` calls `soup.find(...)`, so it reads only the first table.
A live call returned 19 fields while the other five tables remained unparsed.
`finvizp` must discover and merge every matching table by header/content rather
than selecting the first class match.

### Spectrum has a valid-input exception

`group/spectrum.py` treats `group_order_dict` as an object with an
`order_dict` attribute even though it is a dictionary. A direct valid call
raised `AttributeError`. The implementation also relies on a positional image
index, another drift-prone assumption.

### Global mutable transport

The package has one process-global requests session, proxy mapping, timeout,
and retry configuration. This prevents safe route-local cookies, concurrent
clients with different policies, and deterministic isolation between tests or
callers.

### Synchronous page-by-page collection

The screener defaults to 20 rows per page and performs pages sequentially with
a fixed sleep. It builds pandas frames incrementally. It has no async
pagination, bounded page concurrency, checkpointing, Arrow batch emission, or
per-query provenance.

### Signal fan-out is request-expensive

`ticker_signal()` runs every known signal screener and checks whether the
requested ticker appears. This creates roughly one screen request per signal
per ticker. `finvizp` should fetch each signal result set once, or let callers
query a signal explicitly. It should never implement per-ticker N-signal
probing as the default.

### Metadata and units are lost

The statement response includes top-level currency, but the package returns
only `response["data"]`. HTML percentages are converted to fractions while
other unit-bearing strings can remain untyped. Fetch time, source URL, query,
entitlement tier, response hash, and provider symbol are not first-class
output fields.

### Interface inconsistency

Return values vary among dictionaries, pandas Series/DataFrames, lists,
nested dictionaries of DataFrames, chart URLs, and downloaded files. Some
methods perform network I/O at object construction. Export helpers mix data
acquisition with Excel/CSV persistence.

## Use in `finvizp`

Treat `finvizfinance` as:

- a capability checklist;
- a source of sample URLs, visible labels, and filter concepts;
- a fixture and failure-mode reference;
- a migration-audience reference.

Do not treat it as:

- a transport dependency;
- a stable live schema;
- an interface to preserve exactly;
- evidence that bulk collection is permitted;
- a source of authoritative unit or temporal semantics.

`finvizp` will provide a migration guide mapping old methods to new endpoint
functions and Arrow-native CSV/Excel workflows. Compatibility aliases and a
pandas adapter are explicitly rejected from the public contract.
