# Screener (0.2)

The screener turns one validated query into one complete, Arrow-native
table. There is one deep module, not a method per filter: every screen —
fixed view, custom columns, signal, or earnings window — is a
:class:`finvizp._queries.screener.ScreenerQuery` (validated against the
checked-in `screener_registry.json` **before any network request**) fetched
by the same collector, parsed by the same pure parser, and paginated by the
same provider-evidence rules.

```python
import asyncio

from finvizp import FinvizClient, screen_async
from finvizp._queries.screener import CustomColumns, Filter, ScreenerQuery


async def main() -> None:
    async with FinvizClient() as client:
        # A named fixed view (overview/valuation/financial/ownership/
        # performance/technical) with an optional filter.
        overview = await screen_async(
            ScreenerQuery(
                view="overview",
                filters=[Filter(name="Market Cap.", option="+Large (over $10bln)")],
            ),
            client=client,
        )

        # A custom view picks explicit registry columns.
        prices = await screen_async(
            ScreenerQuery(
                view="custom",
                columns=CustomColumns(names=["Ticker", "Price", "Volume"]),
            ),
            client=client,
        )

        print(overview.table.num_rows, prices.table.column_names)
        # 20 ('rank', 'symbol', 'price', 'volume', 'fetched_at', 'extra_fields')


asyncio.run(main())
```

## Schemas: fixed views are registered, custom views are deterministic

- **Fixed named views** render the registry's declared columns in registry
  order — the same query always produces the same schema.
- **Custom views** assemble the Arrow schema from the requested registry
  columns in the order you pass them: `rank`/`symbol` first, one typed
  column per requested label, then `fetched_at` + `extra_fields`.
  Duplicates and provider-unknown labels are rejected before any request.
- Display conversion follows the registered unit of each column label
  (`percent` → fraction, `compact` → absolute number, counts → `int64`);
  unknown labels stay text. Labels the provider adds later land in
  `extra_fields` with a warning, never dropped.

The combined table is endpoint-native and wide (one row per screener rank);
there is no CSV/Excel exporter — Arrow output is the only representation.

## Pagination, safety, and partial results

- A page is **final** only on provider evidence: it renders fewer rows than
  the registry page size (20), or its `#start / total Total` marker puts
  `start + page_size` past the observed total. The universe size is never
  assumed.
- `max_pages` (default 250) and `max_rows` (default 20,000) are **client-side
  safety stops**, not provider parameters. Hitting one yields `COMPLETE`
  with a stop warning under `allow_partial=True`; strict mode raises
  `FinvizPartialError` carrying the partial table.
- A mid-walk request failure raises the original typed error; with
  `allow_partial=True` the rows fetched so far are returned as `PARTIAL`
  (a first-page failure always raises — there is nothing to carry).
- A recognized no-results page is an `EMPTY` result, not an error.
- Duplicate ranks across pages (repeated pagination state) and mid-walk
  header drift raise `FinvizParseError`.
- Cancellation propagates immediately; `on_progress(done_pages, rows)`
  reports after each page; nothing is printed to stdout.

## Signals

Signal presets (`Top Gainers`, `New High`, `Most Active`, …) are projections
over the same collector — not a second transport:

```python
from finvizp import FinvizClient, signal_async

async with FinvizClient() as client:
    result = await signal_async("Top Gainers", client=client)
table = result.table  # rank, symbol, company, market_cap, price, change, ...
```

Every name is resolved through the checked-in signal registry before any
network I/O, so an unknown or stale signal fails safely.

## Earnings screens

Earnings windows are another projection of the same screen:

```python
from finvizp import FinvizClient, earnings_async, earnings_options

async with FinvizClient() as client:
    result = await earnings_async(when="This Week", client=client)
```

- `when` is a registry `Earnings Date` window (`Today`, `Tomorrow`,
  `Yesterday`, `This Week`, `Next Week`, `This Month`); `session` restricts
  a day window to `Before Market Open`/`After Market Close`. Invalid
  combinations are rejected before any request.
- The projection splits the provider display into three typed fields:
  `earnings_date` (Arrow `date32` — the provider's own date, no clock time),
  `earnings_date_raw` (the exact display, e.g. `Nov 12 BMO`), and
  `earnings_session` (`BMO`/`AMC` only when the provider says so — never
  invented from clock time). Date-only displays leave the session null.
- `earnings_options()` exposes the checked-in `when`/`session` → provider
  option mapping for callers composing their own queries.

## Representations and access

The public representation is the anonymous HTML table on
`/screener.ashx`. Anonymous Elite export endpoints (JSON/CSV) are **never
requested** — they stay out of scope for the public client, and there is no
login, cookie reuse, or Elite transport anywhere in the screener module.

## Limits and provenance

- One explicit screen per call; the collector is bounded by
  `max_pages`/`max_rows` and never enumerates the whole screener
  filter space.
- Every result carries `metadata.query` (canonical query JSON),
  `parser_version`, `schema_version`, per-page pagination facts in the
  warnings, and cache coherence (`cache=`, `refresh=`) identical to the
  rest of the library.
- The registry (`screener_registry.json`) is reviewed data with its own
  bounded, opt-in drift tooling — see
  [the registry and drift workflow](screener-registry.md).
