# Keep your own history

The public Finviz surfaces are mostly snapshots. `finvizp` returns each snapshot
with provenance, but it does not store data for you.

## Two kinds of history

| Kind | What it means |
|---|---|
| Provider history | A historical series the provider serves directly. The public client currently exposes this mainly through economic-calendar release details. |
| Collector history | Snapshots you capture repeatedly and store yourself. |

Forex, crypto, and futures sparklines have no point timestamps or intervals.
`finvizp` keeps those arrays verbatim and does not present them as time series.
Quotes are delayed snapshots.

## Capture a snapshot

```python
from finvizp import FinvizClient, quote_async

async with FinvizClient() as client:
    result = await quote_async("AAPL", client=client)

table = result.table
record = {
    "fetched_at": result.metadata.fetched_at,
    "response_hash": result.metadata.response_hash,
    "schema_version": result.metadata.schema_version,
    "parser_version": result.metadata.parser_version,
    "access_tier": result.metadata.access_tier,
    "endpoint": result.metadata.endpoint,
}
```

Write `table` and `record` together with Arrow IPC, Parquet, a database, or the
storage system you already use. A key such as `(symbol, fetched_at)` keeps
repeated captures distinct.

Provenance fields stay consistent across operation families, so captures can be
joined later. The package never invents provider history.

See [proxies and cache](proxies-and-cache.md) for repeat reads inside one
process and the [design notes on snapshot history](https://github.com/legout/finvizp/blob/main/docs/brainstorming/snapshot-history.md).
