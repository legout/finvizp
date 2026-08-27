# Finviz data surface

## Scope rule

`finvizp` is **capability-complete**, not interface-compatible. Public 1.0
covers every dataset/image available through `finvizfinance` plus the complete
verified public structured/image surface frozen on 2026-08-27. The normative
cutoff is [the frozen public inventory](public-surface-inventory.md). Login- and
Elite-only families remain deferred until legitimately verified.

## Temporal-shape vocabulary

- **Snapshot:** current state at fetch time; history exists only if captures are
  retained.
- **Period history:** values attached to fiscal periods supplied by Finviz.
- **Event history:** dated rows such as analyst ratings, news, insider filings,
  or economic releases.
- **Ranked result:** a point-in-time query result whose ordering and query are
  part of its meaning.
- **Image artifact:** a chart or spectrum image, not structured numeric history.

## Endpoint-family matrix

| Family | Representative surface | Shape | `finvizfinance` coverage | Intended `finvizp` result |
|---|---|---|---|---|
| Quote snapshot | `/quote.ashx?t=AAPL` | snapshot | partial live parse | one merged typed table row plus provenance |
| Description | quote page profile region | snapshot text | yes | one-row table or quote field |
| Peers | quote page `Peers` link | relationship snapshot | yes | edge table: symbol → peer |
| ETF holders | quote page `Held by` link | relationship snapshot | yes | edge table: symbol → ETF |
| Ratings | quote ratings table | event history/window | yes | dated ratings table |
| Ticker news | quote news table | event window | yes | dated news-link table |
| Ticker insider | quote insider table | event window | yes | dated transaction table with SEC link |
| Signals | screener signal result sets | ranked snapshot | yes, inefficient per ticker | explicit signal query table |
| Stock charts | chart image URLs | image artifact | yes | artifact descriptor; optional download helper |
| Statements | `/api/statement.ashx` | period history | IA/IQ/BA/BQ/CA/CQ | normalized long table; optional wide form |
| Screener | `/screener.ashx` | ranked snapshot | all primary views | typed paginated Arrow table |
| Groups | `/groups.ashx` | aggregate snapshot | overview/valuation/performance/custom | group observation table |
| Group spectrum | groups spectrum image | image artifact | attempted, currently broken | artifact descriptor/download |
| Global news | `/news.ashx` | event window | news and blogs | two typed event tables or source-kind column |
| Global insider | `/insidertrading.ashx` | event window/ranking | latest/top/buy/sale variants | typed event table with query metadata |
| Earnings calendar | screener earnings filter | ranked/event snapshot | week/month partitions | one normalized earnings result table |
| Economic calendar | `/calendar.ashx` | event window | HTML + embedded JSON | typed release table |
| Forex | performance and chart pages | snapshot/image | yes | performance table and artifact descriptors |
| Crypto | performance and chart pages | snapshot/image | yes | performance table and artifact descriptors |
| Futures | performance page | snapshot | D/W/M/Q/HY/Y | typed performance table |

## Quote snapshot fields

The current quote page divides fields across six tables. The union observed
for AAPL includes these categories.

### Identity and classification

- company, exchange, country, sector, industry;
- index membership;
- peer symbols and ETFs holding the security;
- business description.

### Capitalization and distributions

- market capitalization and enterprise value;
- income and sales;
- book value and cash per share;
- estimated and trailing dividends;
- ex-dividend date, dividend growth, payout ratio;
- employees and IPO date.

### Valuation, liquidity, and leverage

- trailing and forward P/E, PEG, P/S, P/B, P/C, P/FCF;
- EV/EBITDA and EV/sales;
- quick and current ratios;
- debt/equity and long-term debt/equity;
- optionable and shortable status.

### Earnings, growth, and analyst estimates

- EPS TTM, next year, next quarter;
- EPS growth this year, next year, next five years, past three/five years;
- sales growth past three/five years and quarter-over-quarter/TTM growth;
- EPS surprises and revenue surprises where exposed;
- analyst recommendation and target price;
- earnings date/session label.

### Ownership and profitability

- insider ownership and transaction change;
- institutional ownership and transaction change;
- ROA, ROE, ROIC;
- gross, operating, and profit margins.

### Shares, short interest, technicals, and performance

- shares outstanding and float;
- shares short, short float percentage, short ratio;
- SMA20/SMA50/SMA200 distances;
- weekly/monthly volatility, beta, ATR, RSI;
- 52-week high/low position and range;
- average and relative volume, current volume;
- performance across week, month, quarter, half-year, YTD, year, and longer
  windows where exposed;
- current price, change, gap, and previous close.

Field names and table composition are a versioned provider contract. The
implementation must preserve raw labels and unit interpretation alongside
normalized names.

## Statements

The JSON response is a dictionary of metric arrays aligned with arrays for
`Period`, `Period End Date`, and sometimes `Period Length`. AAPL live examples
showed:

- annual labels including `TTM` and fiscal-year labels;
- quarterly labels such as `2026Q3`;
- currency at the response root;
- blanks for unavailable values;
- comma-formatted numbers and signed cash-flow values;
- statement-specific ratios and per-share values mixed with accounting lines.

The canonical representation should be long:

```text
symbol
statement_kind
periodicity
period_label
period_end_date
period_length
metric
value
raw_value
currency
fetched_at
source_url
response_hash
```

A convenience pivot can produce a wide table without making source metric
changes break the storage schema.

Official Finviz marketing currently distinguishes three years of statements
for free accounts from eight years for Elite. A public endpoint probe returned
more periods for AAPL than that table suggests. Availability is not an
entitlement guarantee; implementations must detect actual responses and never
rely on accidental exposure.

## Screener

### Views

Capability coverage includes:

- Overview
- Valuation
- Financial
- Ownership
- Performance
- Technical
- Custom
- Ticker-list extraction
- Snapshot/chart views when structurally usable

A screener row only has meaning with its query. Provenance must include:

- normalized filters and their original Finviz codes;
- signal;
- view and selected columns;
- order and direction;
- page/rank;
- entitlement/page size;
- fetch timestamp and response hash.

### Signals

The audited package enumerates 32 signals, including:

- top gainers/losers, new highs/lows, active, volatile, unusual volume;
- overbought/oversold;
- upgrades/downgrades and earnings before/after;
- recent insider buying/selling and major news;
- horizontal support/resistance, trendline support/resistance;
- wedges, triangles, channels;
- double/multiple tops and bottoms;
- head-and-shoulders and inverse head-and-shoulders.

Signals are Finviz-defined ranked classifications. They are worth exposing even
when their numeric ingredients can be calculated locally, because exact Finviz
membership and ranking are provider-specific observations.

## Groups

Group dimensions include sector, industry and sector-specific industries,
country, and capitalization. Views expose combinations of:

- stock count and employee count;
- market capitalization;
- valuation ratios;
- dividend yield;
- growth estimates and historical growth;
- profitability, liquidity, and leverage;
- short interest and analyst recommendation;
- volume and performance windows.

Groups should remain provider-native observations. Finviz membership and
aggregation formulas may differ from local security-master classifications.

## Events and relationships

### Ratings

Capture date, status/action, rating organization, rating value, and price
target. Preserve the exact displayed organization and action rather than
forcing them into Yahoo semantics.

### News

Capture display timestamp, parsed timestamp when unambiguous, title, URL,
publisher/source, associated symbol when known, feed kind (`news` or `blog`),
fetch timestamp, and URL/content hash. A page is a recent window, not a complete
historical archive.

### Insider

Capture symbol, owner, relationship, transaction date/type, cost, shares,
value, post-transaction total shares, filing display timestamp, SEC Form 4 URL,
and Finviz insider identifier when present. This is a Finviz rendering of
filing data, not the regulatory source of truth.

### Earnings

Expose earnings-period screener results directly. Partitioning into separate
DataFrames or CSV files is a presentation concern and should not be part of the
core interface.

### Economic calendar

Normalize date/time, release, impact, reference period, actual, expected, and
prior values. Preserve raw strings because units and locale vary by release.

## Images

Stock, forex, crypto, and group spectrum charts are artifact endpoints. Core
functions should return descriptors with URL, symbol/group, timeframe, chart
type, fetched time, media type, and content hash. A separate helper may write
bytes to a caller-selected path. Image downloads must never masquerade as
structured market history.

## Elite and additional current features

Official Elite material advertises:

- real-time and extended-hours data;
- custom filters and stats view;
- API/export access for screener, portfolio, groups, options, and news;
- complete ETF holdings and structural metrics;
- options chains;
- correlated stocks;
- longer statements and fundamental charts.

These remain in the research/capability manifest but do not block public 1.0.
No public function or schema is published until the authenticated behavior has
been legitimately verified. Public and authenticated implementations should
share parsers/schemas only where observed response contracts genuinely match.

## Public additions missing from `finvizfinance`

The bounded current-surface inventory also found public structured capabilities
that belong to `finvizp` 1.0:

- map constituent/hierarchy/performance data embedded in the public canvas page;
- publisher-specific news pages;
- insider fund and manager pages;
- economic-calendar detail pages;
- current futures tile/sparkline JSON (the legacy performance-table target is
  now empty);
- the complete sixteen-table stock page and canonical `/stock?t=...` route.

Maps return data, not a renderer. Publisher/fund/manager/detail functions fetch
only caller-specified identifiers and never enumerate the sitemap. News support
does not fetch third-party article bodies.
