# Migrate from `finvizfinance`

`finvizp` covers the audited public capability range of `finvizfinance` 1.4.0,
but it does not preserve the old class and pandas interface.

The important change is the shape of the client:

| `finvizfinance` | `finvizp` |
|---|---|
| One mutable class per page | Module-level operations over one `FinvizClient` |
| Pandas objects, dicts, lists, and strings | Immutable `FetchResult[T]` |
| Hidden request state | Explicit query and client options |
| Implicit downloads and exporters | Explicit artifact downloads; caller-owned export |
| Synchronous methods only | Async-first operations plus sync twins |

## First quote

```python
# finvizfinance
from finvizfinance.quote import finvizfinance

df = finvizfinance("AAPL").ticker_fundament()

# finvizp
import finvizp

result = finvizp.quote("AAPL")
snapshot = result.data.snapshot
a_row = snapshot.to_pylist()[0]
```

The `finvizp` quote returns a complete bundle. Its snapshot is an Arrow table,
not a pandas Series.

## Quotes

| Legacy method | `finvizp` replacement | Difference |
|---|---|---|
| `Quote.get_current(ticker)` | `quote_async(symbol)` → bundle price | Typed field with explicit delayed-quote metadata. |
| `ticker_fundament()` | `snapshot_async(symbols)` | All six snapshot relations, typed units, and raw companions. |
| `ticker_description()` | `quote_async(...)` → `bundle.description` | Comes from the one-page bundle. |
| `ticker_outer_ratings()` | `ratings_async(symbols)` | Typed Arrow table; empty relation is a typed empty table. |
| `ticker_news()` | `news_async(symbols)` | Metadata only. Article bodies are not fetched. |
| `ticker_inside_trader()` | `insider_async(symbols)` | Typed insider table. |
| `ticker_peer()` | `peers_async(symbols)` | Arrow rows instead of `list[str]`. |
| `ticker_etf_holders()` | `etf_holders_async(symbols)` | Public reverse-holder relation. |
| `ticker_signal()` | `signal_async(name)` | Query one signal once, then check ticker membership locally. |
| `ticker_full_info()` | `quote_async(symbols)` | One complete bundle; no mutable `self.info`. |
| `ticker_charts(...)` | `chart_descriptor()` + `download_artifact_async()` | Description and download are separate, bounded operations. |

## Statements

All six statement codes use one operation:

```python
result = finvizp.statements("AAPL", statement="IA")
```

| Legacy | `finvizp` |
|---|---|
| `Statements.get_statements(ticker, statement, timeframe)` | `statements_async(symbol, statement=...)` or `statements(...)` |
| Repeated ticker calls | `statements_batch_async(symbols, statement=...)` |

Valid codes are `IA`, `IQ`, `BA`, `BQ`, `CA`, and `CQ`. The Arrow schema keeps
currency and raw display values instead of dropping them.

## Screeners and earnings

| Legacy | `finvizp` |
|---|---|
| `Overview.screener_view(...)` | `screen_async(ScreenerQuery(view="overview", ...))` |
| `Custom.screener_view(columns=[...])` | `screen_async(ScreenerQuery(view="custom", columns=...))` |
| `Ticker.screener_view(ticker=...)` | `screen_async(ScreenerQuery(ticker=...))` |
| `set_filter()` / `reset()` | Frozen `ScreenerQuery(filters=[Filter(...)])` |
| `Earnings.partition_days(...)` | `earnings_async(when=..., session=...)` |
| `output_csv()` / `output_excel()` | Export the returned Arrow table in caller code |

Queries are checked against the registry before a request. Pagination has
client-side `max_pages` and `max_rows` stops. See the [screener reference](../reference/screener.md).

## Groups, news, insider data, and calendar

| Legacy family | `finvizp` |
|---|---|
| Group `screener_view` methods | `group_async(GroupQuery(...))` |
| Group spectrum | `spectrum_async(GroupQuery(...))` |
| `News.get_news()` | `global_news_async()` |
| `Insider.get_insider(option)` | `global_insider_async(feed=...)` |
| `Calendar.calendar()` | `calendar_async()` |
| No legacy equivalent | `map_async()`, `publisher_news_async()`, `fund_insider_async()`, `manager_insider_async()`, `calendar_detail_async()` |

These calls use explicit identifiers where the provider needs one. They do not
crawl indexes or sitemaps.

## Forex, crypto, futures, and charts

| Legacy | `finvizp` |
|---|---|
| `Forex.performance()` / `Crypto.performance()` | `forex.performance()` / `crypto.performance()` |
| `Forex.chart()` / `Crypto.chart()` | Module chart operation returning an `Artifact` |
| `Future.performance()` | `futures.futures()` over current embedded tiles |

Sparklines remain verbatim because the provider does not supply point
timestamps. `finvizp` does not invent a history series.

## Deliberate differences

### No pandas contract

Convert at your application boundary if needed:

```python
df = result.table.to_pandas()
```

The package itself does not carry a pandas dependency.

### No exporters or persistence layer

Use Arrow or your own storage system:

```python
import pyarrow.csv as pa_csv

pa_csv.write_csv(result.table, "screen.csv")
```

Store the table together with `result.metadata.fetched_at`,
`response_hash`, `schema_version`, and `access_tier` if you are collecting
snapshots over time. See [caller-owned history](caller-owned-history.md).

### No compatibility aliases or login automation

Legacy class and method names are not re-exported. Login, portfolio, options,
Elite exports, and other deferred surfaces remain planned rather than exposed
as stubs. See the [capability matrix](../reference/capability-matrix.md).
