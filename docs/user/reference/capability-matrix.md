# Capability matrix

The complete, machine-checked public surface: every `finvizp` capability, its
frozen access classification, and the `finvizfinance` capability it replaces.
This document mirrors [`src/finvizp/capabilities.json`](https://github.com/legout/finvizp/blob/main/src/finvizp/capabilities.json)
entry by entry; `tests/test_capabilities.py` fails if the manifest and this
page drift apart. Load the same data programmatically with
`finvizp.capabilities()` (one `finvizp.Capability` per row) or look one entry
up with `finvizp.capability(id)`.

- **Inventory date:** 2026-08-27 (frozen; see the
  [public-surface inventory](https://github.com/legout/finvizp/blob/main/docs/research/public-surface-inventory.md) and the
  [`finvizfinance` audit](https://github.com/legout/finvizp/blob/main/docs/research/finvizfinance-audit.md)).
- **Statuses:** `implemented` (shipped, fixture-tested, documented) and
  `planned` (deferred; claims no operation, fixture, or test).
- **Operation columns** use `module:function`; every networked implemented
  operation is async-first with a sync twin, and the curated `finvizp.__all__`
  carries every operation except the module-level forex/crypto families
  (documented in
  [markets and artifacts](markets-and-artifacts.md)).
- **`replaced`** records the audited `finvizfinance` 1.4.0
  (commit `c8d461d1991da1675edc63ea0238391e6f0ba776`) capability this entry
  covers; `finvizfinance: none` means the capability is new in `finvizp`.

## Implemented capabilities (30)

| id | family | operation | access | output | representation | schemas | fixture | tests | docs | replaces |
|---|---|---|---|---|---|---|---|---|---|---|
| symbols.universe | symbol_universe | finvizp.symbols:symbols | PUBLIC | arrow_table | xml_manifest | symbol_universe | tests/fixtures/symbols/stock-sitemap.xml | tests/test_symbols.py | docs/reference/schemas-0.1.md | finvizfinance symbols via screener ticker lists |
| symbols.search | symbol_search | finvizp.symbols:search_symbols | PUBLIC | arrow_table | json_suggestions | symbol_search | tests/fixtures/symbols/suggestions.json | tests/test_symbols.py | docs/reference/schemas-0.1.md | finvizfinance: none |
| statements.ia | statements | finvizp.statements:statements | PUBLIC | arrow_table | json_statements | statements | tests/fixtures/statements/income-annual.json | tests/test_statements.py | docs/reference/schemas-0.1.md | finvizfinance Statements.get_statements (income annual) |
| statements.iq | statements | finvizp.statements:statements | PUBLIC | arrow_table | json_statements | statements | tests/fixtures/statements/income-annual.json | tests/test_statements.py | docs/reference/schemas-0.1.md | finvizfinance Statements.get_statements (income quarterly) |
| statements.ba | statements | finvizp.statements:statements | PUBLIC | arrow_table | json_statements | statements | tests/fixtures/statements/balance-quarterly.json | tests/test_statements.py | docs/reference/schemas-0.1.md | finvizfinance Statements.get_statements (balance annual) |
| statements.bq | statements | finvizp.statements:statements | PUBLIC | arrow_table | json_statements | statements | tests/fixtures/statements/balance-quarterly.json | tests/test_statements.py | docs/reference/schemas-0.1.md | finvizfinance Statements.get_statements (balance quarterly) |
| statements.ca | statements | finvizp.statements:statements | PUBLIC | arrow_table | json_statements | statements | tests/fixtures/statements/cashflow-annual.json | tests/test_statements.py | docs/reference/schemas-0.1.md | finvizfinance Statements.get_statements (cashflow annual) |
| statements.cq | statements | finvizp.statements:statements | PUBLIC | arrow_table | json_statements | statements | tests/fixtures/statements/cashflow-annual.json | tests/test_statements.py | docs/reference/schemas-0.1.md | finvizfinance Statements.get_statements (cashflow quarterly) |
| quote.bundle | quote | finvizp.quote:quote | PUBLIC | bundle | html_stock_page | quote_snapshot, quote_description, quote_ratings, quote_news, quote_insider, quote_peers, quote_etf_holders, quote_signals | tests/fixtures/quote/stock-current.html | tests/test_quote.py | docs/reference/schemas-0.1.md | finvizfinance quote overview (first snapshot table only) |
| quote.snapshot | quote | finvizp.quote:snapshot | PUBLIC | arrow_table | html_stock_page | quote_snapshot | tests/fixtures/quote/stock-current.html | tests/test_quote_projections.py | docs/reference/schemas-0.1.md | finvizfinance ticker_fundament (first snapshot table only) |
| quote.ratings | quote | finvizp.quote:ratings | PUBLIC | arrow_table | html_stock_page | quote_ratings | tests/fixtures/quote/stock-current.html | tests/test_quote_projections.py | docs/reference/schemas-0.1.md | finvizfinance analyst ratings |
| quote.news | quote | finvizp.quote:news | PUBLIC | arrow_table | html_stock_page | quote_news | tests/fixtures/quote/stock-current.html | tests/test_quote_projections.py | docs/reference/schemas-0.1.md | finvizfinance ticker news |
| quote.insider | quote | finvizp.quote:insider | PUBLIC | arrow_table | html_stock_page | quote_insider | tests/fixtures/quote/stock-current.html | tests/test_quote_projections.py | docs/reference/schemas-0.1.md | finvizfinance insider latest |
| quote.peers | quote | finvizp.quote:peers | PUBLIC | arrow_table | html_stock_page | quote_peers | tests/fixtures/quote/stock-current.html | tests/test_quote_projections.py | docs/reference/schemas-0.1.md | finvizfinance peers table |
| quote.etf_holders | quote | finvizp.quote:etf_holders | PUBLIC | arrow_table | html_stock_page | quote_etf_holders | tests/fixtures/quote/stock-current.html | tests/test_quote_projections.py | docs/reference/schemas-0.1.md | finvizfinance ETF holder links |
| screener.views | screener_views | finvizp.screener:screen | PUBLIC | arrow_table | html_tables | registry-defined | tests/fixtures/screener/overview-page-1.html | tests/test_screener.py | docs/reference/screener.md | finvizfinance screener overview/valuation/financial/ownership/performance/technical/custom/ticker |
| screener.signals | screener_views | finvizp.screener:signal | PUBLIC | arrow_table | html_tables | registry-defined | tests/fixtures/screener/overview-page-1.html | tests/test_screener_signals.py | docs/reference/screener.md | finvizfinance signal presets (Top Gainers, ...) |
| charts.descriptor | charts | finvizp.artifacts:chart_descriptor | PUBLIC | artifact | image | none | tests/fixtures/artifacts/sample.png | tests/test_artifacts.py | docs/reference/markets-and-artifacts.md | finvizfinance chart URL helper and image download |
| maps.structured | maps | finvizp.maps:map | PUBLIC | structured_data | embedded_json | none (typed `MapBundle`) | tests/fixtures/maps/sp500-embedded.html | tests/test_maps.py | docs/reference/groups-maps-events.md | finvizfinance: none |
| groups.views | groups | finvizp.groups:group | PUBLIC | arrow_table | html_tables | registry-defined | tests/fixtures/groups/overview.html | tests/test_groups.py | docs/reference/groups-maps-events.md | finvizfinance sector/industry/country/group views |
| news.global | news | finvizp.news:global_news | PUBLIC | arrow_table | html_tables | none (news tables) | tests/fixtures/news/global.html | tests/test_news.py | docs/reference/groups-maps-events.md | finvizfinance news and blogs tables |
| news.publisher | publisher_news | finvizp.news:publisher_news | PUBLIC | arrow_table | html_tables | none (news tables) | tests/fixtures/news/publisher.html | tests/test_news.py | docs/reference/groups-maps-events.md | finvizfinance: none |
| insider.global | insider | finvizp.insider:global_insider | PUBLIC | arrow_table | html_tables | quote_insider | tests/fixtures/insider/global.html | tests/test_insider.py | docs/reference/groups-maps-events.md | finvizfinance insider latest/top/buy/sale feeds |
| insider.fund_manager | fund_manager_insider | finvizp.insider:fund_insider | PUBLIC | structured_data | embedded_json | none (typed rows) | tests/fixtures/insider/fund.html | tests/test_insider.py | docs/reference/groups-maps-events.md | finvizfinance: none |
| earnings.screen | earnings | finvizp.earnings:earnings_screen | PUBLIC | arrow_table | html_tables | earnings_screen | tests/fixtures/screener/custom-columns.html | tests/test_earnings.py | docs/reference/screener.md | finvizfinance earnings week/month partition helpers |
| calendar.economic | economic_calendar | finvizp.calendar:calendar | PUBLIC | arrow_table | embedded_json | economic_calendar | tests/fixtures/calendar/current-embedded.html | tests/test_calendar.py | docs/reference/groups-maps-events.md | finvizfinance economic calendar parser |
| calendar.details | economic_details | finvizp.calendar:calendar_detail | PUBLIC | arrow_table | embedded_json | economic_details | tests/fixtures/calendar/detail.html | tests/test_calendar.py | docs/reference/groups-maps-events.md | finvizfinance: none |
| forex.structured | forex | finvizp.forex:tiles | PUBLIC | structured_data | embedded_json | none (typed `TileBundle`) | tests/fixtures/markets/forex-tiles.html | tests/test_forex.py | docs/reference/markets-and-artifacts.md | finvizfinance forex performance tables and charts |
| crypto.structured | crypto | finvizp.crypto:tiles | PUBLIC | structured_data | embedded_json | none (typed `TileBundle`) | tests/fixtures/markets/crypto-tiles.html | tests/test_crypto.py | docs/reference/markets-and-artifacts.md | finvizfinance crypto performance tables and charts |
| futures.tile | futures | finvizp.futures:futures | PUBLIC | arrow_table | embedded_json | futures_tiles | tests/fixtures/futures/current-tiles.html | tests/test_futures.py | docs/reference/markets-and-artifacts.md | finvizfinance futures performance (broken empty-table target) |

Every implemented operation imports, names its output kind and (where
applicable) its registered Arrow schemas, and links to an existing fixture,
test module, and reference page — enforced by
`tests/test_capabilities.py`.

## Planned capabilities (6)

Planned entries claim no operation, fixture, or test: there is no
always-failing public stub to import. Each documents why it is deferred.

| id | family | access | representation | deferral |
|---|---|---|---|---|
| screener.export | screener_export | ELITE | api_json | `/export.ashx` and the screener export API are Elite-only; anonymous probes redirect or return nothing entitled. Deferred until legitimate authenticated verification. |
| groups.export | group_export | ELITE | api_json | `/grp_export.ashx` is Elite-only; the anonymous CSV is never requested. |
| etf.public_shell | etf_shell | UNKNOWN | spa_shell | `/etf.ashx` is a JavaScript shell; complete holdings/metrics are Elite. No browser automation; only the public reverse holder links via `quote.etf_holders`. |
| options.deferred | options | ELITE | spa_shell | `/options.ashx` is JavaScript/Elite-oriented; no verified public structured contract. |
| portfolio.deferred | portfolio | AUTHENTICATED | spa_shell | `/portfolio.ashx` requires login (export is Elite). Never automated. |
| elite.extras | elite_extras | ELITE | api_json | Alerts, correlated stocks, fundamental charts, publishing: the Elite feature set. See the [migration guide](../how-to/migrate-from-finvizfinance.md). |

## Coverage verdict

- Every family of the frozen public-surface inventory appears above, so the
  frozen matrix is closed: each capability is either implemented with
  fixture/test/docs evidence, or explicitly gated with a justified
  non-public access classification.
- Every page/screen/data method of the audited `finvizfinance` 1.4.0 surface
  maps to one of the implemented capabilities (or is an intentionally unported
  table helper); the method-by-method mapping with the Arrow replacement
  workflow lives in the
  [migration guide](../how-to/migrate-from-finvizfinance.md). The legacy
  `util` transport/registry plumbing is replaced wholesale by explicit
  `FinvizClient` construction options and the checked-in screener registry,
  not method-by-method.
- The one intentional signature change set — `FetchResult[T]` envelopes
  instead of pandas objects, typed queries instead of mutable filter state,
  bounded artifacts instead of implicit downloads — is documented there too.
