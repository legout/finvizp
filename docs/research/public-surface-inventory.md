# Frozen public Finviz surface inventory

**Inventory date:** 2026-08-27

**Purpose:** define the finite public feature set that gates `finvizp` 1.0.
Entirely new families discovered after this date do not automatically move the
1.0 goalpost. Additive fields within an existing family follow schema evolution.

## Evidence and method

The inventory used official Finviz navigation, sitemap indexes, `robots.txt`,
the Elite feature table, and bounded one-request probes of representative pages.
No account login, browser challenge bypass, sitemap crawl, or bulk collection
was performed. The audited comparison is `finvizfinance` 1.4.0 at commit
`c8d461d1991da1675edc63ea0238391e6f0ba776`.

## Frozen matrix

| Family | Representative surface | Access at inventory date | Shape | `finvizfinance` 1.4.0 | `finvizp` 1.0 decision |
|---|---|---|---|---|---|
| Symbol universe | `/sitemap.xml?t=0&p=0` | Public | XML manifest; one stock and one `ty=oc` URL per symbol | Ticker class derives via screener | Include dedicated canonical `symbols` methods; one manifest request, no URL following |
| Symbol search | `/api/suggestions?input=...` | Public | JSON, bounded ranked matches | No | Include dedicated typed `search_symbols` methods |
| Screener views/signals | `/screener.ashx?v=111&s=ta_topgainers` | Public, delayed; 20 rows/page | HTML tables | Yes | Include all reviewed views, filters, signals, ordering, custom columns, pagination |
| Screener export/API | `/export.ashx`, `/api/v1/screener-export-csv` | Elite-only; redirect/empty anonymous response | CSV/JSON | No | Defer until authenticated verification |
| Stock/quote | `/stock?t=AAPL`, fallback `/quote.ashx?t=AAPL` | Public, delayed | Sixteen HTML tables, including six snapshot tables | Partial; only first snapshot table | Include complete `QuoteBundle` and projections |
| Statements | `/api/statement.ashx?t=AAPL&s=IA` | Public JSON; advertised depth differs by tier | JSON | IA/IQ/BA/BQ/CA/CQ | Include all six forms, currency, periods, raw values |
| Stock charts | `/chart.ashx?t=AAPL&p=d` | Public image; richer layouts Elite; robots restrictions | PNG | URL/download helper | Include artifact descriptors and explicit bounded download |
| Maps | `/map.ashx` | Public delayed data; real-time Elite | Canvas UI with embedded JS data | No | Include structured constituent/hierarchy/performance data, not renderer |
| Groups | `/groups.ashx?v=110` | Public | HTML tables/images | Yes; spectrum path broken | Include dimensions, views, aggregates, and artifact descriptors |
| Group export | `/grp_export.ashx` | Elite-only | CSV | No | Defer |
| Global news | `/news.ashx` | Public | HTML tables | News/blogs | Include Finviz metadata only |
| Publisher news | `/news/<publisher>` | Public and sitemap-indexed | HTML tables/pages | No | Include explicitly requested publisher pages, no site-wide crawl |
| Ticker news | stock page relation | Public | Event table | Yes | Include in `QuoteBundle` and projection |
| Global insider | `/insidertrading.ashx` variants | Public; search query robots-restricted | HTML tables | Latest/top/buy/sale | Include explicit feed/query variants |
| Ticker insider | stock page relation | Public | Event table with SEC link | Yes | Include in `QuoteBundle` and projection |
| Fund/manager insider | `/insidertrading/funds/*`, `/insidertrading/managers/*` | Public and sitemap-indexed | HTML pages/tables | No | Include explicitly requested identifiers, no sitemap crawl |
| Earnings | before/after signals and earnings filters | Public | Ranked screener rows | Week/month partition helpers | Include normalized earnings screen data; no core CSV/Excel exporter |
| Economic calendar | `/calendar/economic` | Public | Embedded JSON/current HTML fallback | Yes | Include event table |
| Economic details | `/calendar/economic/detail/<release>` | Public and sitemap-indexed | Detail/history page data | No | Include explicitly requested release details |
| Forex | `/forex_performance.ashx`, `/forex.ashx`, charts | Public | Table, embedded tile/sparkline JSON, images | Performance/chart | Include structured data and artifacts |
| Crypto | `/crypto_performance.ashx`, `/crypto.ashx`, charts | Public | Table, embedded tile/sparkline JSON, images | Performance/chart | Include structured data and artifacts |
| Futures | `/futures.ashx` | Public, delayed | Embedded tile JSON | Legacy table parser targets now-empty page | Include current tile data; do not preserve broken empty-table assumption |
| ETF public shell | `/etf.ashx` | JavaScript shell; complete holdings/metrics Elite | SPA/UI | No | No browser support; only public reverse holder links from stock pages |
| Options | `/options.ashx` | JavaScript/Elite-oriented | SPA/UI/API when entitled | No | Defer until legitimate authenticated endpoint verification |
| Portfolio | `/portfolio.ashx` | Login required; export Elite | App/UI | No | Defer |
| Alerts/correlated/fundamental charts/publish | Elite feature set | Elite-only | UI/API/artifacts | No | Defer |

## Public 1.0 cutoff

Public 1.0 includes every structured or image family marked **Include** above,
plus a capability-manifest replacement for every `finvizfinance` method. It
does not include stubs that always fail, guessed schemas, or browser automation
for login/Elite shells.

“Support” means a bounded caller-requested function. Apart from reading the one
reviewed stock manifest without following entries, it does not mean:

- enumerating all roughly 11,500 ticker pages;
- walking thousands of publisher, fund, manager, or economic-detail URLs;
- fetching every signal/filter combination automatically;
- archiving third-party article bodies;
- bypassing robots directives, login, Cloudflare, or entitlement controls.

The client may completely paginate one explicit screen/feed under conservative
pacing and safety limits.

## Symbol-discovery verification

The public sitemap index currently lists seven child manifests. The stock child
`t=0&p=0` was fetched once and contained 23,310 URLs: 11,655 unique canonical
`/stock?t=...` URLs and 11,655 matching `ty=oc` variants in the same order. The
sets were identical. The response exposed no `ETag`, `Last-Modified`, or cache
directive. Current non-alphanumeric forms use provider dash notation such as
`BRK-B`, `AAC-U`, and `REZI-WI`.

The first-party `/api/suggestions` endpoint returned JSON objects with `ticker`,
`company`, and `exchange`, bounded to ten observed matches. Empty input with
indices enabled returned a bounded mixed prefix, not a universe. It is therefore
a search operation, never an alternative implementation of `symbols()`.

## Access evidence

Official Elite material at the inventory date advertises real-time and
extended-hours data, larger/custom screens, export/API access, complete ETF
holdings, options chains, alerts, correlated stocks, eight-year statements, and
fundamental charts. Anonymous export/group-export probes redirected to Elite or
returned no entitled data. Portfolio required an account. ETF/options exposed
SPA shells rather than verified public structured contracts.

Current `robots.txt` disallowed several export, image/chart, generic screener,
search, and API paths while explicitly allowing selected signal screens. The
approved design treats this as automation guidance: direct user-requested public
access may be implemented, but automatic crawling is prohibited.

## Known live drift from `finvizfinance`

- The stock page has sixteen tables and six snapshot tables; the legacy parser
  reads only the first snapshot table.
- The group spectrum implementation raises `AttributeError` for valid input.
- The current futures performance information lives in embedded tile JSON; the
  legacy table target returned no table.
- Economic calendar and futures have current embedded-JSON variants.
- Canonical stock URLs use `/stock?t=...`; `/quote.ashx?t=...` remains a tested
  fallback while live.
- Class shares use dash notation such as `BRK-B` and `BF-B`.

## Uncertainties and verification rule

SPA knowledge-base topics were not enumerated after a bounded browser probe hit
a Cloudflare challenge. No bypass was attempted. Public/Elite behavior can
change without versioning. Every implementation milestone must re-probe only
its bounded representative endpoints through the actual planned transport and
record any scope-impacting change for review.
