# Migrating from `finvizfinance` to `finvizp`

`finvizp` preserves the capability breadth of `finvizfinance` 1.4.0 (audited at
commit `c8d461d1991da1675edc63ea0238391e6f0ba776`) but not its interface. This
guide maps every legacy page/screen/data method to its Arrow-native replacement
and documents the intentional differences; the legacy `util` transport/registry
plumbing (`set_proxy`, `get_filters`, …) has no per-method twin — explicit
`FinvizClient` options and the checked-in registry replace it wholesale. The
capability-level ledger lives in the
[capability matrix](../reference/capability-matrix.md).

## The shape change in one paragraph

`finvizfinance` exposes one class per page with methods returning pandas
objects (`pd.DataFrame`, `pd.Series`, `dict`, `list`, raw `str`) and
downloaded files. `finvizp` exposes module-level functions that return an
immutable `FetchResult[T]` whose `.data` is a PyArrow table, a compound
bundle, or an artifact descriptor. Every operation is async-first with a
sync twin (`.resume`-free naming: `screen_async`/`screen`), shares one
`FinvizClient` for transport, and never prints, writes files, or keeps
mutable request state behind your back.

```python
# finvizfinance
from finvizfinance.quote import finvizfinance

df = finvizfinance("AAPL").ticker_fundament()  # pandas Series, first snapshot table only

# finvizp
import finvizp

async with finvizp.FinvizClient() as client:
    result = await finvizp.quote_async("AAPL", client=client)
    snapshot = result.data[0].snapshot  # complete Arrow table, all six snapshot tables
    row = snapshot.to_pylist()[0]  # plain Python dicts, provider units typed
```

## Quote page

`quote.bundle`, `quote.snapshot`, `quote.ratings`, `quote.news`,
`quote.insider`, `quote.peers`, `quote.etf_holders`:

| `finvizfinance` (quote.py) | `finvizp` replacement | Differences that matter |
|---|---|---|
| `Quote.get_current(ticker)` | `finvizp.quote_async(symbol)` → `bundle.price` | Returns a typed table field, not a scraped string; delayed-quote semantics are explicit in metadata. |
| `finvizfinance.ticker_fundament()` | `finvizp.snapshot_async(symbols)` | All six snapshot tables merged (legacy reads only the first, 19 fields); Arrow `quote_snapshot` schema, `_raw` companions, percent columns as decimal fractions. |
| `finvizfinance.ticker_description()` | `finvizp.quote_async(...)` → `bundle.description` | Text rides the one-page bundle instead of a second parse. |
| `finvizfinance.ticker_outer_ratings()` | `finvizp.ratings_async(symbols)` | Arrow `quote_ratings`, never `None` (empty relation = typed empty table). |
| `finvizfinance.ticker_news()` | `finvizp.news_async(symbols)` | Arrow `quote_news` with typed datetimes; Finviz metadata only, third-party bodies are never fetched. |
| `finvizfinance.ticker_inside_trader()` | `finvizp.insider_async(symbols)` | Arrow `quote_insider`, values typed from displays. |
| `finvizfinance.ticker_peer()` | `finvizp.peers_async(symbols)` | Arrow `quote_peers` (rows, not a bare `list[str]`). |
| `finvizfinance.ticker_etf_holders()` | `finvizp.etf_holders_async(symbols)` | Arrow `quote_etf_holders` — the public reverse relationship only. |
| `finvizfinance.ticker_signal()` | `finvizp.signal_async(name, ...)` | **Inverted on purpose.** Legacy probes ~33 screener requests per ticker; `finvizp` fetches one signal's result set once and you check membership locally. |
| `finvizfinance.ticker_full_info()` | `finvizp.quote_async(symbols)` | One bundle with every relation; no `self.info` dict accumulation, no network I/O at construction. |
| `finvizfinance.ticker_charts(...)` | `finvizp.chart_descriptor(symbol)` + `finvizp.download_artifact_async(descriptor)` | URL is described, never implicitly downloaded; downloads are explicit and bounded (`min(8 MiB, cache budget)`). |

## Statements

`statements.ia` / `statements.iq` / `statements.ba` / `statements.bq` /
`statements.ca` / `statements.cq` — `Statements.get_statements(ticker,
statement, timeframe)` becomes one operation over the six statement codes:

| `finvizfinance` | `finvizp` replacement | Differences that matter |
|---|---|---|
| `Statements.get_statements(ticker, statement, timeframe)` | `finvizp.statements_async(symbol, statement=...)` | `statement` is `"IA" | "IQ" | "BA" | "BQ" | "CA" | "CQ"` (same codes). Arrow `statements` schema; top-level currency and raw string values are preserved instead of dropped; `statements_batch_async` fans a bounded symbol list out under one envelope. |

## Screener, signals, earnings

`screener.views`, `screener.signals`, `earnings.screen`:

| `finvizfinance` | `finvizp` replacement | Differences that matter |
|---|---|---|
| `Overview/Valuation/Financial/Ownership/Performance/Technical.screener_view(...)` | `finvizp.screen_async(ScreenerQuery(view=...))` | `view` is `"overview" | "valuation" | "financial" | "ownership" | "performance" | "technical"`. The immutable query replaces `set_filter`/`reset` mutation; validation happens against the checked-in registry before any request. |
| `Custom.screener_view(columns=[...])` | `finvizp.screen_async(ScreenerQuery(view="custom", columns=CustomColumns(names=[...])))` | Schema is assembled from the requested registry columns in your order; unknown labels are rejected up front, late provider additions land in `extra_fields` with a warning. |
| `Ticker.screener_view(ticker=...)` | `finvizp.screen_async(ScreenerQuery(ticker="AAPL"))` | Same collector, same schema rules. |
| `Base.set_filter(...)` / `Base.reset()` | `ScreenerQuery(filters=[Filter(name=..., option=...)])` | No mutable request state; queries are frozen dataclasses, cache-safe and replayable. |
| `Base.compare(ticker, compare_list, ...)` | `finvizp.screen_async(ScreenerQuery(ticker=..., filters=[Filter(name="Sector" | "Industry" | "Country", ...)]))` | There is no dedicated `compare`; express the peer screen as a query (see the screener reference for the filter form). |
| `order=`/`ascend=`/`select_page=`/`limit=`/`sleep_sec=`/`verbose=` | `ScreenerQuery(order=...)`, `page=`, `max_rows=`/`max_pages=`, client pacing | Pagination completes one explicit screen under client-side safety stops; progress is an opt-in typed callback, nothing prints. |
| `Earnings.partition_days(...)` | `finvizp.earnings_async(when=..., session=...)` | One normalized Arrow `earnings_screen` table (rank, symbol, typed date/session) instead of a dict of per-day DataFrames; `earnings_options()` lists the registry windows. |
| `Earnings.output_csv(path)` / `Earnings.output_excel(path)` | caller-owned export (below) | Persistence helpers are deliberately not shipped. |

## Groups and spectrum

`groups.views` (and the group spectrum path):

| `finvizfinance` | `finvizp` replacement | Differences that matter |
|---|---|---|
| `group/Overview/Valuation/Performance.screener_view(...)` | `finvizp.group_async(GroupQuery(dimension=..., view=...))` | One `screener_view(group=..., order=...)` on each class; `Sector`/`Industry`/`Country`/`Capitalization` are `group=` argument values, not classes. Dimensions `sector`/`industry`/`country`/`capitalization`; views `overview`/`valuation`/`performance`/`custom`. Arrow tables with typed units. |
| `group/Custom.screener_view(...)` | `finvizp.group_async(GroupQuery(view="custom", columns=...))` | Registry-validated column set, deterministic schema. |
| `group/Spectrum.screener_view(...)` | `finvizp.spectrum_async(GroupQuery(...))` | Returns an `Artifact` descriptor without downloading bytes (the legacy path crashed with `AttributeError` on valid input); bytes via `download_artifact_async`. |

## News, insider, calendar

`news.global`, `insider.global`, `calendar.economic`:

| `finvizfinance` | `finvizp` replacement | Differences that matter |
|---|---|---|
| `News.get_news()` | `finvizp.global_news_async()` | Arrow table (news + blogs rows with typed times); one bounded request, no archive crawling. |
| `Insider.get_insider(option)` | `finvizp.global_insider_async(feed=...)` | Typed feed names replace the free-string option; rows normalize to the `quote_insider` schema. |
| `Calendar.calendar()` | `finvizp.calendar_async()` | Reads the current embedded JSON (legacy HTML fallback retained upstream only); Arrow `economic_calendar` with explicit release-timestamp status. |
| — (no legacy equivalent) | `finvizp.calendar_detail_async(release)` | New in `finvizp`: one explicitly requested release's detail table. |

## Forex, crypto, futures

`forex.structured`, `crypto.structured`, `futures.tile`, `charts.descriptor`:

| `finvizfinance` | `finvizp` replacement | Differences that matter |
|---|---|---|
| `Forex.performance()` / `Crypto.performance()` | `finvizp.forex.performance_async()` / `finvizp.crypto.performance_async()` | Module-level families (names would collide at top level). Deterministic wide Arrow tables; percent columns are decimal fractions with `_raw` companions. |
| `Forex.chart(symbol, timeframe)` / `Crypto.chart(...)` | `finvizp.forex.chart_async(symbol, timeframe=...)` / `finvizp.crypto.chart_async(...)` | `Artifact` descriptors resolved verbatim from the gallery; download through the artifact helpers. Timeframes are `5m`/`1h`/`d`/`w`/`m`. |
| `Future.performance()` | `finvizp.futures.futures_async()` | The legacy table target has been empty for years; `finvizp` reads the current embedded tile JSON into the registered `futures_tiles` Arrow schema. Sparklines stay verbatim — the provider sends no timestamps, so none are invented. |

## New in `finvizp` (no legacy method)

These capabilities have no `finvizfinance` method to replace — the manifest
records them with `finvizfinance: none`:

- `symbols.universe` — `finvizp.symbols_async()`: the canonical security
  universe from the published stock sitemap, one request, no URL following
  (legacy derived tickers from screener lists).
- `symbols.search` — `finvizp.search_symbols_async(query)`: bounded typed
  suggestions from the public JSON endpoint.
- `maps.structured` — `finvizp.map_async()`: the structured S&P 500 bundle
  (hierarchy tree + flat constituents), where legacy has no map support.
- `news.publisher` — `finvizp.publisher_news_async(publisher)`: explicitly
  requested publisher pages, never a site-wide crawl.
- `insider.fund_manager` — `finvizp.fund_insider_async(fund)` /
  `finvizp.manager_insider_async(manager)`: explicit fund/manager insider
  pages.
- `calendar.details` — `finvizp.calendar_detail_async(release)`: one
  requested release's detail table.

Each is documented in the [capability matrix](../reference/capability-matrix.md).

## Deliberately not ported

- **`output_csv` / `output_excel` (and every exporter).** `finvizp` returns
  Arrow tables and ships no persistence or export helpers. Export yourself
  with Arrow-native tooling (below): `pyarrow.csv.write_csv` produces CSV
  directly (`to_csv` on the table is deliberately not provided), and any
  XLSX writer consumes `to_pylist()` rows for Excel (`to_excel` likewise).
- **Pandas objects.** No DataFrame/Series contract; convert once at the edge
  if you need pandas (`table.to_pandas()`), paying that cost in your code,
  not in the library.
- **Compatibility aliases.** Legacy class/method names are not re-exported.
- **Global mutable transport.** One process-global session/proxy/timeout
  becomes explicit, route-isolated `FinvizClient` state.
- **`ticker_signal()`'s per-ticker fan-out.** Roughly one request per signal
  per ticker is replaced by one request per signal you actually want.
- **Login/Elite surfaces** (options, portfolio, exports, alerts, correlated
  stocks, fundamental charts, publishing): no always-failing stubs are
  exported; see the deferred rows in the
  [capability matrix](../reference/capability-matrix.md). They stay
  `planned` until legitimately verified.

## Arrow-native CSV/Excel export (caller-owned)

No pandas or exporter dependency is required — the tables are PyArrow
objects and standard Arrow tooling writes them out:

```python
import pyarrow.csv as pa_csv
import pyarrow.dataset as pa_ds
from openpyxl import Workbook

table = result.table  # Arrow table

# CSV: one call, no pandas.
pa_csv.write_csv(table, "screen.csv")

# Excel: Arrow -> plain dicts -> any XLSX writer (openpyxl shown).
rows = pa_ds.dataset([table]).to_table().to_pylist()
workbook = Workbook()
sheet = workbook.active
sheet.append([f.name for f in table.schema])
for row in rows:
    sheet.append(list(row.values()))
workbook.save("screen.xlsx")
```

Keep the envelope's provenance (`result.metadata.fetched_at`,
`response_hash`, symbols) alongside any snapshot you persist — repeated
captures collected with those fields are what make honest point-in-time
history; the package itself never invents history (see the
[results reference](../reference/results.md) for the envelope contract).
