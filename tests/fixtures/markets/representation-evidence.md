# Markets fixtures: forex and crypto performance tables, tile pages, chart galleries

REPRESENTATION EVIDENCE (bounded live probe, 2026-08-30, four GETs + one
robots read, browser UA "Mozilla/5.0", no JS execution, no account, no crawl):

1. GET https://finviz.com/forex_performance.ashx -> 301 to /forex_performance
   -> 200 text/html (63,266 bytes).
   - Exactly one `table.groups_table`.
   - Headers: No. | Pair | Price | Perf 5Min | Perf Hour | Perf Day | Perf Week
     | Perf Month | Perf Quart | Perf Half | Perf YTD | Perf Year.
   - 10 rows (BTC/USD, USD/CHF, USD/JPY, USD/CAD, GBP/JPY, EUR/GBP, GBP/USD,
     AUD/USD, EUR/USD, NZD/USD); each perf cell is a percent display
     ("1.02%"); price is a plain decimal display ("78900.1000").
   - A `?v=1&tv=2` variant (finvizfinance's "PIPS" view) returns the identical
     table shape with pip-count displays ("67330") in the perf columns; the
     provider varies only ordering/units, not structure.
   - No sparkline payload, no embedded JSON tiles on this page.
   - Delay provenance footer: "Stock quotes delayed by 1 minute."

2. GET https://finviz.com/crypto_performance.ashx -> 301 to /crypto_performance
   -> 200 text/html (104,020 bytes).
   - One `table.groups_table`; headers add Ticker and Name after No.:
     No. | Ticker | Name | Price | Perf 5Min ... Perf Year.
   - 31 rows (UNI Uniswap, ZEC Zcash, ... POL Polygon).

3. GET https://finviz.com/forex.ashx -> 301 to /forex -> 200 (74,593 bytes).
   GET https://finviz.com/crypto.ashx -> 301 to /crypto -> 200 (112,816 bytes).
   - No HTML table. Embedded first-party JSON tile payload in an inline
     script: `window.addEventListener('Finviz:FinvizInitForex', () =>
     window.FinvizInitForex({...}), {once: true});` (crypto: FinvizInitCrypto).
   - Payload: one object per instrument keyed by provider ticker
     ("AUDUSD", "AAVEUSD", ...; 13 forex entries incl. GC/SI/CL commodity
     tiles on the forex page, 31 crypto entries).
   - Tile fields (verified identical across both families): label ("AUD/USD"),
     ticker, last, change (percent value), changeUsd, prevClose, high, low,
     sparkline (array of 300 floats, NO per-point timestamps),
     sparklineDateChanges ({index: "4PM"-style label} — render hints only).
   - Honest temporal semantics: the provider sends no per-point timestamps or
     interval contract, so no historical bars are inferred anywhere.

4. GET https://finviz.com/forex_charts.ashx?t=ALL&tf=d1 -> 301 to
   /forex_charts?t=ALL&p=d -> 200 (72,404 bytes).
   GET https://finviz.com/crypto_charts.ashx?t=ALL&tf=d1 -> 301 to
   /crypto_charts?t=ALL&p=d -> 200 (213,370 bytes).
   - Chart images are `img.charts-gal` elements whose srcset names
     `https://charts2-node.finviz.com/chart?...&t=@eurusd&tf=d&...` — a
     CROSS-ORIGIN image host the client can never fetch (canonical-origin
     contract), so the markets card resolves descriptor-only URLs taken
     verbatim from the page. 10 forex, 31 crypto gallery entries.
   - No `chart.ashx` URLs anywhere on these pages (unlike stock pages).

5. Access tier: PUBLIC, delayed. robots.txt has no forex/crypto entries in its
   disallow list. No login/Elite surface is touched.

TRANSPORT CONTRACT (verified): one GET per operation, canonical origin:
   - performance table: /forex_performance.ashx or /crypto_performance.ashx
     (query `v=1&tv=2` selects the PIPS display variant)
   - tile bundle: /forex.ashx or /crypto.ashx
   - chart descriptors: /forex_charts.ashx?t=ALL&tf=<code> or
     /crypto_charts.ashx?t=ALL&tf=<code>, descriptor for one symbol read from
     the page's own `img.charts-gal` srcset (first 1x URL, verbatim)
No JavaScript execution, no additional requests, no image byte downloads.
