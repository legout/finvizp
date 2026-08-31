# Use the sync API

Every async operation has a sync twin: same name, `_async` suffix removed.

```python
import finvizp

# These two calls are equivalent to their _async counterparts:
income = finvizp.statements("AAPL", statement="IA")
matches = finvizp.search_symbols("APPLE")
```

## Rules

1. Sync twins **reject an active event loop** — don't call them from inside
   `asyncio.run` or a coroutine.
2. Results, caching, and errors are identical to the async path.
3. For many symbols, prefer batch operations (`finvizp.statements_batch`)
   over a loop of single calls.

```python
import finvizp

rows = finvizp.statements_batch(["AAPL", "MSFT"], statement="IA")
for result in rows:
    print(result.data.num_rows)
```

See [Result envelopes](../reference/results.md) for strict vs.
`allow_partial=True` batch semantics.
