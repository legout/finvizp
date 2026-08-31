# Groups, maps, news, and events

Milestone 0.3 adds five public families over the same `FinvizClient` seam:

1. group aggregates and spectrum descriptors;
2. the structured S&P 500 map;
3. global and publisher news;
4. global, fund, and manager insider feeds;
5. the economic calendar and release details.

All operations are async-first with sync wrappers. They return immutable
`FetchResult` values and use the shared cache, single-flight, retries, and typed
errors.

Publisher, fund, manager, and release calls take one explicit identifier. They
do not enumerate sitemaps.

## Groups

```python
from finvizp import FinvizClient, group_async
from finvizp._queries.groups import GroupDimension, GroupQuery, GroupView

async with FinvizClient() as client:
    result = await group_async(
        GroupQuery(
            dimension=GroupDimension.SECTOR,
            view=GroupView.OVERVIEW,
        ),
        client=client,
    )
```

The table has one row per group. Provider aggregate columns are typed from
their display units, with raw companions, `extra_fields`, and `fetched_at`.

Supported dimensions include `SECTOR`, `INDUSTRY`, `COUNTRY`, and
`CAPITALIZATION`. Views include `OVERVIEW`, `VALUATION`, `PERFORMANCE`,
`CUSTOM`, and `SPECTRUM`.

A query is validated before network I/O. An intentionally empty page returns
`EMPTY`; a missing header, arity mismatch, or duplicate table raises
`FinvizParseError`.

`spectrum_async()` returns an `Artifact` descriptor. It does not download
bytes. Use the artifact download helpers described in
[Markets and artifacts](markets-and-artifacts.md).

## Maps

```python
from finvizp import FinvizClient, map_async

async with FinvizClient() as client:
    result = await map_async(client=client)

bundle = result.data
```

A cold map call makes two requests:

- `/map.ashx`;
- the hierarchy asset named by the page's own `data-chunk-id="map_base_sec"`
  preload link.

The result is a `MapBundle`:

| Field | Contents |
|---|---|
| `root` | Provider hierarchy: root, sectors, industries, and symbol leaves. |
| `constituents` | Flat leaves in hierarchy order with sector/industry context and performance values. |
| `unmapped_perf` | Performance symbols that have no matching hierarchy leaf. |
| `delay_minutes`, `access_tier` | Page provenance. |

The bundle is cached by route. The parser does not run JavaScript or reproduce
the provider's canvas renderer.

## News

```python
from finvizp import FinvizClient, global_news_async, publisher_news_async

async with FinvizClient() as client:
    all_news = await global_news_async(client=client)
    publisher = await publisher_news_async("reuters", client=client)
```

`global_news_async()` reads `/news` once and returns `news` and `blogs` tables.
`publisher_news_async(slug)` reads one explicitly named publisher page.

Rows preserve the title, URL, publisher, raw time display, typed time, and time
status:

- `anchored`: a clock time attached to the response date in New York;
- `relative`: a relative time resolved against the fetch instant;
- `date_only`: a date with no invented clock time.

Article bodies and related-ticker links are not fetched. Publisher sitemaps are
not enumerated.

## Insider feeds

`global_insider_async(feed=...)` reads one Form 4 window from
`/insidertrading.ashx`. `INSIDER_FEEDS` lists the nine reviewed feed variants.
The operation does not follow repeated continuation links.

`fund_insider_async(slug)` and `manager_insider_async(slug)` each read one
explicit fund or manager page. They return quarterly portfolio disclosures,
not Form 4 events, and never search the underlying sitemap.

## Economic calendar

```python
from finvizp import FinvizClient, calendar_async, calendar_detail_async

async with FinvizClient() as client:
    calendar = await calendar_async(client=client)
    detail = await calendar_detail_async("USACPI", client=client)
```

The calendar reads the complete embedded JSON on `/calendar.ashx`.
`calendar_detail_async(release)` reads one explicit release slug.

Both tables use the same temporal contract:

- `release_date` plus `release_date_raw`;
- `release_timestamp` plus its raw value and status;
- null timestamps for date-only or all-day releases.

Recognized zero-entry payloads return `EMPTY`. Broken payloads raise
`FinvizParseError`.

## Access boundaries

These families use anonymous public HTML or first-party JSON. The client does
not use login cookies, Elite transport, export endpoints, or sitemap crawls.
