# Screener

The screener turns one validated query into one Arrow table. Fixed views,
custom columns, signals, and earnings windows all use the same collector,
pure parser, and pagination rules.

Queries are validated against `screener_registry.json` before network I/O.

## A fixed view and a custom view

```python
import asyncio

from finvizp import FinvizClient, screen_async
from finvizp._queries.screener import CustomColumns, Filter, ScreenerQuery


async def main() -> None:
    async with FinvizClient() as client:
        overview = await screen_async(
            ScreenerQuery(
                view="overview",
                filters=[Filter(name="Market Cap.", option="+Large (over $10bln)")],
            ),
            client=client,
        )
        prices = await screen_async(
            ScreenerQuery(
                view="custom",
                columns=CustomColumns(names=["Ticker", "Price", "Volume"]),
            ),
            client=client,
        )
        print(overview.table.num_rows)
        print(prices.table.column_names)


asyncio.run(main())
```

## Views and schemas

| Query | Schema behavior |
|---|---|
| Fixed named view | Uses registry columns in registry order. |
| Custom view | Uses the requested registry columns in the order supplied. `rank` and `symbol` come first, followed by `fetched_at` and `extra_fields`. |
| Unknown or duplicate column | Rejected before the request. |
| New provider column | Stored in `extra_fields` with a warning. |

Display conversion follows the registry: percentages become fractions,
compact values become absolute numbers, and counts become `int64`.
Unknown labels remain text.

## Pagination limits

A page is final only when Finviz says it is final:

- fewer than the registry page size of 20 rows; or
- the `#start / total Total` marker places the next page beyond the observed
  total.

The client never assumes a universe size.

| Limit | Default | If reached |
|---|---:|---|
| `max_pages` | 250 | Stop with a warning in partial mode; strict mode raises `FinvizPartialError`. |
| `max_rows` | 20,000 | Same behavior. |

A first-page failure raises because there are no rows to preserve. A failure
mid-walk returns the rows fetched so far with `allow_partial=True`.

Duplicate ranks or a changed header between pages raise `FinvizParseError`.
`on_progress(done_pages, rows)` reports progress without printing anything.
Cancellation propagates immediately.

## Signals

Signals are named projections over the same collector.

```python
from finvizp import FinvizClient, signal_async

async with FinvizClient() as client:
    result = await signal_async("Top Gainers", client=client)

table = result.table
```

Signal names are checked against the registry before network I/O.

## Earnings screens

```python
from finvizp import FinvizClient, earnings_async

async with FinvizClient() as client:
    result = await earnings_async(when="This Week", client=client)
```

Supported windows include `Today`, `Tomorrow`, `Yesterday`, `This Week`,
`Next Week`, and `This Month`. A day window can add `Before Market Open` or
`After Market Close` through `session`. Invalid combinations fail before the
request.

The result exposes:

- `earnings_date`: provider date as Arrow `date32`;
- `earnings_date_raw`: exact provider display, such as `Nov 12 BMO`;
- `earnings_session`: `BMO` or `AMC` only when Finviz states it.

## Access and provenance

The public client reads the anonymous HTML table at `/screener.ashx`. It does
not request Elite JSON/CSV export endpoints or automate login.

Each result records its canonical query, parser and schema versions, page facts,
and cache metadata. See [results](results.md) and [the registry workflow](screener-registry.md).
