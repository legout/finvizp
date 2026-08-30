S&P 500 Map (public delayed canvas page, `/map.ashx`)

REPRESENTATION EVIDENCE (bounded live probe, 2026-08-30, two GETs + one robots read, browser_profile=none, no JS execution):

1. GET https://finviz.com/map.ashx -> 200 text/html (47,874 bytes).
   - <title>S&P 500 Map</title>
   - Embedded first-party JSON in an inline script passed to window.FinvizInitCanvas:
       initialPerf: {"nodes":{...},"additional":{},"subtype":"d1","version":15,"hash":"9FE2..."}
     * nodes: 503 entries, ticker -> number (percent performance, e.g. CF:0.064, NWS:-1.331)
     * subtype "d1" = 1-day regular-session performance; version/hash = payload identity
     * initData: undefined (canvas init only, no further embedded data)
   - <link rel="preload" as="script" href="/assets/dist/8119.v1.b9696f52.js" data-chunk-id="map_base_sec">
     -> the sector/industry hierarchy is a static first-party data asset the page
        itself preloads (chunk id "map_base_sec").
   - Delay provenance: footer states "Stock quotes delayed by 1 minute."
   - No /map.ashx entry in robots.txt disallow list (public navigation page).

2. GET https://finviz.com/assets/dist/8119.v1.b9696f52.js -> 200 (34,983 bytes).
   - webpack module 10163: e.exports = {name:"Root",children:[...]} object literal
     (unquoted JS keys; string values double-quoted).
   - Structure verified: Root -> 11 sectors -> 108 industries -> 500 leaf nodes.
   - Leaf node keys: name (ticker), description (company), value (numeric; treemap
     area weight, e.g. AAPL 4521277, MSFT 3559806, JPM 963513 -- market-cap scaled).
   - Shared-class tickers appear only once per hierarchy (FOX/GOOG/NWS as leaf
     class siblings exist in perf nodes but only FOXA/GOOGL/NWSA-style leaves in
     this chunk; the renderer joins perf by ticker at runtime).

3. PERF vs HIERARCHY (live cross-check):
   - perf nodes: 503; hierarchy leaves: 500; hierarchy-only: none.
   - perf-only: FOX, GOOG, NWS (share-class variants of leaf FOXA/GOOGL/NWSA whose
     perf the renderer folds in). This is the known incomplete-embedded-data
     drift the card requires handling: constituents missing perf stay null;
     perf without a hierarchy leaf is reported as drift, never invented.

4. Access tier: PUBLIC, delayed (public page, no auth). Real-time is Elite per
   the frozen inventory; no authenticated surface is touched.

TRANSPORT CONTRACT (verified): two GETs to the canonical origin:
   1. /map.ashx                  -> embedded initialPerf JSON + preload link
   2. the preloaded /assets/dist/*.js chunk (URL taken from the page's own
      data-chunk-id="map_base_sec" preload element; never constructed locally)
No JavaScript execution, no canvas rendering, no additional requests.
