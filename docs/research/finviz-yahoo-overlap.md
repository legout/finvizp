# Finviz and Yahoo overlap

## Decision

`finvizp` remains a **Finviz-only client**. It should expose Finviz observations
for every supported surface even when Yahoo exposes a similar field. It must not
import `yfin`, join Yahoo data into Finviz tables, or choose a canonical winner.
Consumers can compare providers above the package seam.

This decision preserves a clear interface and makes provenance unambiguous:
`finvizp` answers “what did Finviz expose?” rather than “what is the merged
market truth?”

## Why overlapping data can still be valuable

### Independent observation history

A daily capture of Finviz beta, float, margins, short interest, estimates, and
valuation creates a provider-specific point-in-time series. Yahoo may expose the
same labels, but values can differ because of:

- calculation methodology;
- source vendor and update schedule;
- fiscal-period selection;
- treatment of extraordinary items or restatements;
- share-count and float methodology;
- price timestamp and quote delay;
- rounding and unit conventions;
- missing-data and fallback behavior.

Capturing both providers outside `finvizp` supports reconciliation and change
detection without erasing those differences.

### Coverage fallback without hidden merging

One provider may omit a field for a security or instrument type. Separate
Finviz tables can fill analytical coverage at the consumer layer while keeping
source and observation time explicit.

### Detecting revisions

Repeated snapshots reveal when Finviz revises a historical-looking statistic,
such as trailing EPS, beta, analyst target, float, or margin. That revision
history is valuable even when only the latest value is visible upstream.

### Reproducing Finviz screens

A locally computed ratio or Yahoo value may not select the same symbols as a
Finviz screen. Persisting Finviz's own values and signal membership is required
if the goal is to reproduce what a Finviz user saw at that time.

## Verified Yahoo overlap

The local `yfin` 1.4.0 Arrow flattener already supports `financialData` and
`defaultKeyStatistics`, although the current marketdata pipeline does not store
them in its tracked max-set.

A live AAPL request on 2026-08-27 returned the following examples.

### `financialData`

- current price;
- target high, low, mean, and median price;
- recommendation score/key and analyst count;
- cash, EBITDA, debt, revenue, free and operating cash flow;
- quick/current ratio and debt/equity;
- ROA and ROE;
- earnings and revenue growth;
- gross, EBITDA, operating, and profit margins;
- financial currency.

### `defaultKeyStatistics`

- enterprise value and forward P/E;
- float and outstanding shares;
- current and prior-month shares short;
- short-interest dates, short ratio, and short percent of float;
- insider and institutional ownership percentages;
- book value, P/B, PEG, EV/revenue, and EV/EBITDA;
- trailing/forward EPS and quarterly earnings growth;
- fiscal-period dates, split metadata, dividend metadata, beta, and 52-week
  change.

The current `yfin` module allowlist does not include `earningsTrend`; support
for Yahoo forward trend history must be researched in `yfin` separately. This
does not reduce `finvizp`'s requirement to expose the Finviz growth fields.

## Domain-by-domain comparison

| Domain | Finviz form | Yahoo/yfin form | Historical meaning in `finvizp` |
|---|---|---|---|
| Beta | quote/screener snapshot | quote or key-statistics snapshot | repeated Finviz captures only |
| EPS TTM/forward | quote snapshot; statement periods | quote/key statistics; fundamentals | snapshot plus Finviz statement periods |
| EPS/sales growth | quote/screener snapshot | financial data; possible trend module | repeated Finviz captures unless period-labeled |
| Shares outstanding/float | quote/screener snapshot | key-statistics snapshot; selected share series | repeated Finviz captures |
| Short interest | quote/screener snapshot | key-statistics snapshot with dates | retain Finviz observation and any displayed source date |
| Margins/ROA/ROE | quote/screener snapshot; statement ratios | financial-data snapshot; derivable fundamentals | repeated Finviz captures plus period statement ratios |
| Valuation ratios | quote/screener snapshot; statement ratios | quote/fundamental snapshots/time series | Finviz observation series, not merged history |
| Analyst targets | quote snapshot/ratings | financial-data snapshot and actions | targets as snapshots; ratings as events |
| Earnings dates | earnings screener and quote snapshot | calendar event candidates | Finviz ranked/event captures |
| Earnings results | statement lines and surprises | earnings history/fundamentals | retain source-specific period/event rows |
| Insider transactions | ticker and site-wide Finviz windows | quoteSummary recent transactions | Finviz event rows with SEC URLs and capture time |
| Ownership | quote/screener percentages | holder/ownership modules | repeated Finviz captures |
| Technicals | quote/screener snapshot and signals | locally derivable from Yahoo bars | Finviz values/signals preserved for reproducibility |
| News | ticker/global recent window | other Yahoo news surfaces, not current `yfin` | observed event window; no completeness claim |

## Three meanings of “history”

### 1. Provider-supplied period or event history

Examples:

- annual and quarterly statements;
- dated analyst ratings;
- insider transactions;
- news rows;
- economic releases.

Finviz supplies dates or periods. `finvizp` normalizes them and also records
when they were fetched.

### 2. Collector-built snapshot history

Examples:

- beta;
- trailing/forward EPS;
- float and shares outstanding;
- margins and returns;
- valuation ratios;
- analyst consensus and target price;
- short float and ownership percentages;
- signal membership and ranks.

Finviz normally exposes the current value. `finvizp` cannot retrieve old
snapshots retroactively. History begins when a collector starts retaining
successive observations.

### 3. Derived history

Examples:

- moving averages;
- RSI and ATR;
- multi-window returns;
- some valuation/profitability ratios.

These can be computed from raw bars/statements, but Finviz's displayed value is
still a separate provider observation. `finvizp` exposes it; it does not need
to implement a local technical-analysis engine in core.

## Storage and reconciliation boundary

Core `finvizp` returns source-pure Arrow tables inside immutable
`FetchResult`/bundle envelopes. It includes enough provenance for callers to
append observations safely, but has no DuckDB, DuckLake, Delta Lake, pandas,
Parquet-writer, scheduler, or marketdata-pipeline dependency.

A consumer may build comparison views such as:

```text
(canonical_symbol, observed_at, metric)
    -> finviz_value
    -> yahoo_value
    -> absolute/relative difference
    -> source timestamps and raw hashes
```

That comparison belongs in the consumer. `finvizp` must never silently replace
Finviz nulls with Yahoo values or label Yahoo values as Finviz.

## Consequence for implementation planning

Capability overlap is not a reason to omit an endpoint. It is a reason to:

- make source and timing explicit;
- define units precisely;
- keep raw/displayed values available where normalization is ambiguous;
- document whether a field is current, period-dated, or event-dated;
- add fixture cases for provider revisions and missing values;
- avoid claiming that similarly named Yahoo and Finviz fields are equivalent.
