# Groups, maps, and events (0.3)

The 0.3 surface adds five public families over the same client seam as 0.1
and 0.2: group aggregates and spectrum descriptors, the structured S&P 500
map, global and publisher news metadata, insider feed families, and the
economic calendar with release details. Every operation is async-first with
a sync wrapper, returns the immutable `FetchResult` envelope, and honors the
shared cache (`cache=`, `refresh=`), single-flight, retries, and typed
errors. Every enumerated-page surface stays caller-requested and bounded:
publisher, fund, manager, and release operations require one explicit
identifier and never enumerate their sitemaps.

```python
import asyncio

from finvizp import FinvizClient, calendar_async, group_async
from finvizp._queries.groups import GroupDimension, GroupQuery, GroupView

async def main() -> None:
    async with FinvizClient() as client:
        # Group aggregates: typed query, wide Arrow table.
        groups = await group_async(
            GroupQuery(dimension=GroupDimension.SECTOR, view=GroupView.OVERVIEW),
            client=client,
        )
        # Economic calendar: embedded-JSON events, registered schema.
        events = await calendar_async(client=client)
        print(groups.table.num_rows, events.table.num_rows)

asyncio.run(main())
```

## Groups: typed queries, header-driven aggregates

`finvizp.groups.group_async(query, client=...)` fetches one validated
`finvizp._queries.groups.GroupQuery` (dimension `SECTOR`/`INDUSTRY`/
`COUNTRY`/`CAPITALIZATION` plus sub-industries, view `OVERVIEW`/
`VALUATION`/`PERFORMANCE`/`CUSTOM`/`SPECTRUM`, optional order, explicit
custom columns) and returns a deterministic wide Arrow table: one row per
group with `name`, provider aggregate columns typed from their display
units, verbatim `_raw` companions, and `extra_fields`/`fetched_at`.

- The query validates against the checked-in registry **before any network
  I/O**; a successfully constructed query never fails validation later.
- A recognized empty page is an `EMPTY` result; structural drift (missing
  header, arity mismatch, duplicate tables) raises `FinvizParseError`.
- `spectrum_async(query, client=...)` returns an immutable `Artifact`
  descriptor (source URL, media type, group) **without downloading bytes**;
  raw-byte downloads arrive with 0.4. The legacy `finvizfinance` defect
  (crashing on `.order_dict`) is structurally impossible on this path.
- The anonymous Elite `/grp_export` CSV is never requested.

## Maps: structured bundles, never a renderer

`finvizp.maps.map_async(client=...)` performs exactly two requests on a cold
call — `/map.ashx` and the hierarchy asset named by the page's own
`data-chunk-id="map_base_sec"` preload link (never constructed locally) —
and joins them into one immutable `finvizp.MapBundle`:

- `root` is the provider's own Root → sectors → industries → symbol-leaf
  tree; `constituents` flattens the same leaves in hierarchy order with
  sector/industry context and the embedded `perf` values joined by ticker.
- `unmapped_perf` records perf-only share-class symbols (verified
  FOX/GOOG/NWS drift) instead of inventing placements.
- `delay_minutes` and `access_tier` carry the page's own provenance; no
  JavaScript runs and the canvas renderer is not reproduced.
- Each document caches under its own route: a warm call replays the bundle
  with zero requests. A recognized empty page replays as a typed EMPTY
  bundle. `map` is the public `sp500` surface only.

## News: global categories and explicit publishers

`finvizp.global_news_async(client=...)` fetches `/news` (the canonical Time
view) once and returns `{"news": table, "blogs": table}` with provider
ranking preserved verbatim. `finvizp.publisher_news_async(slug,
client=...)` fetches one explicitly caller-named `/news/<slug>` page into
one ordered table.

Every row carries `title`, `url`, `publisher`, the exact provider temporal
display (`published_at_raw`), the typed instant (`published_at`), and the
parse verdict `published_at_status`:

- clock-time displays (`09:20AM`) anchor to the response's own date in New
  York (`anchored`);
- relative displays (`46 min`) resolve against the fetch instant
  (`relative`);
- date-only displays (`Aug-29`) keep a null typed instant (`date_only`) —
  no clock time is ever invented.

Article URLs are never requested, related-ticker badges stay decorative
text, and the publisher sitemap is never enumerated (the caller names the
slug; unknown slugs surface the provider's typed not-found).

## Insider feeds: one global window, explicit disclosures

`finvizp.global_insider_async(client=..., feed=...)` reads one window of the
`/insidertrading.ashx` Form 4 event table. `INSIDER_FEEDS` names the nine
reviewed variants (latest / top week / top 10% owner × all, buys, sales).
Rows normalize into the registered `quote_insider` contract — the same
columns as the ticker insider projection. The provider repeats identical
rows on its `b=N` continuation links (live evidence 2026-08-30), so the
operation never paginates: one request, no repeated state.

`finvizp.fund_insider_async(slug, client=...)` and
`finvizp.manager_insider_async(slug, client=...)` each require one explicit
provider sitemap slug (e.g. `na-0000002230`,
`kingdon-capital-management-llc-1000097`; validated pre-network) and return
the page's embedded first-party portfolio JSON (N-PORT fund / 13F manager:
filer identity, latest-quarter summary, top buy/sell observations,
allocation history, report dates). These are quarterly relationship
disclosures — never Form-4 events — and never enumerate or search the
underlying sitemap families.

## Economic calendar and release details

`finvizp.calendar_async(client=...)` fetches `/calendar.ashx` and returns
the registered `economic_calendar` Arrow table from the page's embedded
`route-init-data` JSON (the verified complete representation; there is no
HTML fallback). `finvizp.calendar_detail_async(release, client=...)` takes
exactly one explicit release slug (the provider URL form, e.g. `USACPI`,
validated pre-network) and returns that release's history as
`economic_details` — one request for one caller-chosen release, the detail
sitemap never enumerated.

Temporal fields follow one contract across both tables: `release_date`
(`date32`) with verbatim `release_date_raw`, `release_timestamp` (typed UTC)
with `release_timestamp_raw` and a `release_timestamp_status` verdict;
date-only / all-day rows keep a null timestamp. Recognized zero-entry
payloads return `EMPTY`; structurally broken payloads raise
`FinvizParseError`.

## Representations, access, and evidence

All five families are anonymous public HTML/first-party-JSON surfaces; no
login, cookie reuse, or Elite transport exists anywhere in them. The Elite
export endpoints (`/grp_export`, screener export JSON/CSV) are never
requested. Representations were verified with bounded one-request live
probes (2026-08-30, recorded in the test-module docstrings and fixture
builders); fixtures are scrubbed synthetic reconstructions, and the opt-in
bounded smokes in `tests/live/test_public_0_3.py` re-verify current access
and shape without ever replacing fixtures.
