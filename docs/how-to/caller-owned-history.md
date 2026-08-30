# Caller-owned history (how-to)

finvizp reads snapshot surfaces. With one documented exception the provider
does not expose history through the public endpoints this client covers, and
finvizp ships no persistence layer, storage helpers, or database code by
design: what a caller keeps is the caller's own collection, under the
caller's retention rules.

## Provider history vs. collector-built snapshots

- **Provider history** — data the provider itself serves as a historical
  series. On the current public surface this is essentially limited to the
  economic-calendar release detail page (`calendar_detail_async`), which
  returns the release's own history table. Sparklines on forex/crypto tiles
  and futures are verbatim value arrays with no timestamps — the payload
  carries no interval, so finvizp never presents them as history
  (`sparkline_timestamps` and `sparkline_interval_seconds` are always
  `None`), and quotes are delayed snapshots, not series.
- **Collector-built snapshots** — history that exists only because a caller
  captured repeatedly. Every `FetchResult` is an immutable point-in-time
  record; accumulating them is the intended way to build history.

## Building point-in-time history

Capture the pieces of `FetchResult` you need and store them with your own
tools — Arrow IPC, Parquet, a database, anything:

```python
from finvizp import FinvizClient, quote_async

async with FinvizClient() as client:
    result = await quote_async("AAPL", client=client)

# The snapshot itself (Arrow-native, your writer of choice):
table = result.table  # or result.data for bundle operations

# The provenance that makes repeated captures comparable:
record = {
    "fetched_at": result.metadata.fetched_at,      # provider-observed time
    "response_hash": result.metadata.response_hash,
    "schema_version": result.metadata.schema_version,
    "parser_version": result.metadata.parser_version,
    "access_tier": result.metadata.access_tier,
    "endpoint": result.metadata.endpoint,
}
```

Write both under one key (symbol + `fetched_at` is the natural one) with the
storage system you already run. Provenance fields are stable across
operations, so captures from different families stay joinable. See
[Proxies and cache](proxies-and-cache.md) for the cache behavior that governs
repeat reads inside one process, and
[Snapshot history](../brainstorming/snapshot-history.md) for the design
reasoning.
