# Capability matrix

This page is the human-readable view of
[`src/finvizp/capabilities.json`](https://github.com/legout/finvizp/blob/main/src/finvizp/capabilities.json).
`tests/test_capabilities.py` checks the manifest and the documentation links.
The same data is available from `finvizp.capabilities()` and
`finvizp.capability(id)`.

## Status and access

- **Implemented** means the operation is shipped, fixture-tested, and
  documented.
- **Planned** means the provider surface was identified but is not exposed.
- The inventory was frozen on 2026-08-27.
- All implemented entries below are public-tier operations.

## Implemented capabilities

Every row has a checked-in fixture, test module, and reference page. Networked
operations are async-first with a sync twin.

### Symbols and statements

| ID | Operation | Output | Replaces |
|---|---|---|---|
| `symbols.universe` | `finvizp.symbols` | Arrow table | Ticker lists derived from screener pages |
| `symbols.search` | `finvizp.search_symbols` | Arrow table | No direct legacy equivalent |
| `statements.ia` | `finvizp.statements(statement="IA")` | Arrow table | Annual income statements |
| `statements.iq` | `finvizp.statements(statement="IQ")` | Arrow table | Quarterly income statements |
| `statements.ba` | `finvizp.statements(statement="BA")` | Arrow table | Annual balance sheets |
| `statements.bq` | `finvizp.statements(statement="BQ")` | Arrow table | Quarterly balance sheets |
| `statements.ca` | `finvizp.statements(statement="CA")` | Arrow table | Annual cash flow statements |
| `statements.cq` | `finvizp.statements(statement="CQ")` | Arrow table | Quarterly cash flow statements |

### Quotes

| ID | Operation | Output | Replaces |
|---|---|---|---|
| `quote.bundle` | `finvizp.quote` | `QuoteBundle` | Quote overview |
| `quote.snapshot` | `finvizp.snapshot` | Arrow table | Ticker fundamentals |
| `quote.ratings` | `finvizp.ratings` | Arrow table | Analyst ratings |
| `quote.news` | `finvizp.news` | Arrow table | Ticker news |
| `quote.insider` | `finvizp.insider` | Arrow table | Latest insider transactions |
| `quote.peers` | `finvizp.peers` | Arrow table | Peer symbols |
| `quote.etf_holders` | `finvizp.etf_holders` | Arrow table | ETF holder links |

The quote bundle fetches the stock page once. Projection operations can reuse
that cached bundle instead of making another request.

### Screeners and groups

| ID | Operation | Output | Replaces |
|---|---|---|---|
| `screener.views` | `finvizp.screen` | Arrow table | Fixed and custom screener views |
| `screener.signals` | `finvizp.signal` | Arrow table | Signal presets |
| `earnings.screen` | `finvizp.earnings` | Arrow table | Earnings week/month helpers |
| `groups.views` | `finvizp.group` | Arrow table | Sector, industry, country, and group views |
| `maps.structured` | `finvizp.map` | `MapBundle` | No direct legacy equivalent |

### News, insider data, and calendar

| ID | Operation | Output | Replaces |
|---|---|---|---|
| `news.global` | `finvizp.global_news` | Arrow tables | News and blogs tables |
| `news.publisher` | `finvizp.publisher_news` | Arrow table | No direct legacy equivalent |
| `insider.global` | `finvizp.global_insider` | Arrow table | Latest/top/buy/sale insider feeds |
| `insider.fund_manager` | `finvizp.fund_insider`, `manager_insider` | Structured data | No direct legacy equivalent |
| `calendar.economic` | `finvizp.calendar` | Arrow table | Economic calendar parser |
| `calendar.details` | `finvizp.calendar_detail` | Arrow table | No direct legacy equivalent |

### Markets and artifacts

| ID | Operation | Output | Replaces |
|---|---|---|---|
| `charts.descriptor` | `finvizp.chart_descriptor` | `Artifact` | Chart URL helper and image download |
| `forex.structured` | `finvizp.forex.performance`, `.tiles` | Arrow/structured data | Forex performance and charts |
| `crypto.structured` | `finvizp.crypto.performance`, `.tiles` | Arrow/structured data | Crypto performance and charts |
| `futures.tile` | `finvizp.futures.futures` | Arrow table | Legacy futures performance table |

## Planned capabilities

These entries are deliberately not exported as failing stubs.

| ID | Access | Why it is planned |
|---|---|---|
| `screener.export` | ELITE | Anonymous export JSON/CSV is not entitled. |
| `groups.export` | ELITE | The anonymous group export is not requested. |
| `etf.public_shell` | UNKNOWN | The page is a JavaScript shell; complete holdings are Elite. |
| `options.deferred` | ELITE | No verified public structured contract. |
| `portfolio.deferred` | AUTHENTICATED | The page requires login. |
| `elite.extras` | ELITE | Alerts, correlated stocks, fundamental charts, and publishing are Elite features. |

## Coverage verdict

The frozen public inventory is closed. Each family is either implemented with
fixture, test, and documentation evidence or explicitly deferred with an access
reason. See [the migration guide](../how-to/migrate-from-finvizfinance.md) for
the method-level comparison.
