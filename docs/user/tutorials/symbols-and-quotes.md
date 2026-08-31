# First data: symbols & quotes

Two operations cover most getting-started needs: the symbol universe/search
pair, and the quote bundle.

## Find symbols

```python
import finvizp

matches = finvizp.search_symbols("APPLE")  # bounded, ranked
universe = finvizp.symbols()  # published stock manifest
```

## Fetch a complete quote bundle

```python
import finvizp

bundle = finvizp.quote("AAPL")
print(bundle.data.snapshot.schema)
```

## Cache-preserving projections

`snap()`, `ratings()`, `news()`, `insider()`, `peers()`, and
`etf_holders()` reuse the cached quote page — no second request:

```python
import finvizp

snap = finvizp.snapshot("AAPL")  # served from cache
```

!!! note
    Every async operation (`quote_async`, `symbols_async`, …) has a sync twin
    with the same name minus the suffix. Sync twins reject an active event
    loop.

Next: [Result envelopes](../reference/results.md) for provenance and partial
handling.
