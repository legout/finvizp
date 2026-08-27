# Research source ledger

**Observation date:** 2026-08-27

This ledger separates inspected source, live observations, and official Finviz
statements. It is intentionally specific so implementation plans can reproduce
or refresh the research.

## Source repositories

### `finvizfinance`

- Repository: <https://github.com/lit26/finvizfinance>
- Audited clone: `/tmp/finvizfinance`
- Revision: `c8d461d1991da1675edc63ea0238391e6f0ba776`
- Declared version: 1.4.0
- Key files:
  - `finvizfinance/util.py`
  - `finvizfinance/quote.py`
  - `finvizfinance/constants.py`
  - `finvizfinance/screener/base.py`
  - `finvizfinance/group/*`
  - `finvizfinance/news.py`
  - `finvizfinance/insider.py`
  - `finvizfinance/earnings.py`
  - `finvizfinance/calendar.py`
  - `finvizfinance/forex.py`
  - `finvizfinance/crypto.py`
  - `finvizfinance/future.py`
  - `test/conftest.py`
- Verification: `91 passed, 14 warnings` with pandas, requests, Beautiful Soup,
  lxml, pytest, and pytest-mock installed in an isolated uv environment.

### `yfin`

- Local repository: `/home/volker/coding/yfin`
- Inspected revision at research time: `31f04f7`
- Relevant files:
  - `src/yfin/client.py`
  - `src/yfin/arrow.py`
  - `src/yfin/summary.py`
  - `docs/reference/arrow-schemas.md`
  - `docs/reference/module-groups.md`
- Live probes confirmed `financialData` and `defaultKeyStatistics` Arrow output
  and confirmed that the current allowlist rejects `earningsTrend`.

### `fastreq`

- Local repository: `/home/volker/coding/fastreq`
- Declared version at research time: 3.2.0
- Relevant features: curl backend, browser TLS impersonation, explicit proxy
  pools, route-isolated cookies, token-bucket rate limiting, bounded retries,
  `Retry-After`, health cooldown, async context manager, optional progress.

## Official and live Finviz URLs

| Purpose | URL |
|---|---|
| Quote page | <https://finviz.com/quote.ashx?t=AAPL> |
| Canonical stock page | <https://finviz.com/stock?t=AAPL> |
| Statement JSON pattern | <https://finviz.com/api/statement.ashx?t=AAPL&s=IA> |
| Screener help/formulas | <https://finviz.com/help/screener> |
| Screener | <https://finviz.com/screener.ashx> |
| Groups | <https://finviz.com/groups.ashx?v=110> |
| News | <https://finviz.com/news.ashx> |
| Insider | <https://finviz.com/insidertrading.ashx> |
| Economic calendar | <https://finviz.com/calendar.ashx> |
| Elite feature/tier table | <https://finviz.com/elite> |
| Robots directives | <https://finviz.com/robots.txt> |
| Sitemap index | <https://finviz.com/sitemap.xml> |
| Package documentation | <https://finvizfinance.readthedocs.io/en/latest/> |

Additional source patterns taken from the audited package:

```text
/forex_performance.ashx
/forex_charts.ashx
/crypto_performance.ashx
/crypto_charts.ashx
/futures_performance.ashx
```

## Live findings

### Quote

- The current AAPL page had six `snapshot-table2` tables with 28 cells each.
- The first began with Index/Market Cap/Enterprise Value.
- Subsequent tables began with P/E, EPS TTM, Insider Own, Shares Outstanding,
  and Performance Week respectively.
- `finvizfinance.ticker_fundament(raw=False)` returned only the first table's
  fields because it uses `find`, not `find_all`.
- Description, peers, ETF holders, ticker news, ratings, and ticker insider
  methods returned structured results in the bounded live probe.

### Statements

Bounded `finvizfinance` calls returned:

```text
IA: 31 metric rows × 9 periods
BQ: 39 metric rows × 12 periods
CA: 38 metric rows × 9 periods
```

Direct JSON observations showed root currency, period labels/end dates, compact
formatted values, blanks, ratios, and TTM/fiscal labels.

### Screener

A bounded Overview query returned columns:

```text
Ticker, Company, Sector, Industry, Country,
Market Cap, P/E, Price, Change %, Volume
```

The package's full constant registry reported 67 filters and 32 signals at the
audited revision. These counts are implementation evidence, not promises that
the current site will retain exactly those registries.

### Group and global feeds

Public extraction showed sector/group tables and current global news and insider
rows. The economic calendar page may render through embedded JSON rather than
legacy tables; the audited package now handles both shapes.

### Frozen public-surface inventory

A second bounded pass used official navigation, sitemap indexes, robots and the
Elite comparison table plus one-request probes of representative pages. It
found:

- public map pages with embedded ticker/performance data;
- public publisher news pages;
- public insider fund/manager pages;
- public economic-calendar detail pages;
- current futures tile JSON while the old performance-table target returned no
  table;
- login/Elite gating for portfolio, exports/APIs, options data, complete ETF
  holdings, alerts, correlated stocks, and fundamental charts.

Two SPA probes (ETF/options) encountered a Cloudflare challenge. The research
stopped without bypass. The full evidence-backed matrix is in
[`public-surface-inventory.md`](public-surface-inventory.md).

### Structured-source and symbol-discovery follow-up

A bounded direct-HTTP follow-up verified the representation-selection and
symbol-discovery decisions:

- `/api/statement.ashx?t=AAPL&s=IA` redirected canonically to
  `/api/statement?t=AAPL&s=IA` and returned `application/json` with currency and
  statement data;
- anonymous `/api/v1/screener-export-csv` returned
  `{"status":"success","data":[]}`, so it is not a public screener replacement;
- the current first-party JavaScript bundle calls `/api/suggestions` with
  `input`, optional `instrument`, and optional `withIndices=1`;
- `/api/suggestions?input=AAPL` returned ten ranked JSON records with `ticker`,
  `company`, and `exchange`; empty input with indices enabled also returned only
  ten records, proving it is lookup rather than a universe feed;
- the public sitemap index exposed seven child manifests;
- one fetch of `sitemap.xml?t=0&p=0` returned 23,310 URLs: 11,655 unique
  canonical stock URLs plus an identical ordered set of 11,655 `ty=oc`
  variants;
- the stock sitemap response exposed no `ETag`, `Last-Modified`, or
  `Cache-Control` header in the probe;
- no listed ticker URL or sibling sitemap entry was followed for symbol data.

These observations support structured-first per-method selection, dedicated
symbol universe/search methods, and the narrow manifest-read exception to the
no-sitemap-crawling rule.

### Parser candidates

Under Python 3.14, direct lxml 6.1.2, selectolax 0.4.11, and Beautiful Soup
4.15.0 all parsed the 30 audited HTML fixtures without exception and exposed
the required quote/script selectors. In a local 3,000-fixture parse loop:

```text
direct lxml: 0.097 seconds
selectolax: 0.168 seconds
Beautiful Soup + lxml: 1.632 seconds
```

Direct lxml was selected for core. Package metadata showed `fastreq` requires
Python 3.11+ and PyArrow requires Python 3.10+, establishing Python 3.11 as the
public support floor.

## Caveats

- Public observations can change without versioning.
- Live values and row counts are examples, not fixtures to hard-code.
- Official marketing tier limits and accidentally reachable response depth can
  disagree; entitlement controls implementation behavior.
- Web extraction tools can render dynamic pages differently from fastreq, so
  implementation spikes must probe through the actual planned transport.
- No secret, authenticated cookie, or proxy credential was used or recorded in
  this research.
