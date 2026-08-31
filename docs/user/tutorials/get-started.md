# Get started

This five-minute tutorial fetches real Finviz data with `finvizp`.

## Install

```bash
uv add finvizp  # or: pip install finvizp
```

## Your first fetch

```python
import asyncio

import finvizp


async def main() -> None:
    async with finvizp.FinvizClient() as client:
        bundle = await finvizp.quote_async("AAPL", client=client)
        print(bundle.data.snapshot.num_rows)


asyncio.run(main())
```

That's the whole pattern: one client, one `await`, an immutable
`FetchResult` whose `.data` is an Arrow-backed bundle.

## Where to go next

- [First data: symbols & quotes](symbols-and-quotes.md) — the two calls every
  workflow starts with.
- [Use the sync API](../how-to/sync-api.md) — when you're not in async land.
- [Result envelopes](../reference/results.md) — provenance, strictness, and
  partial results.
