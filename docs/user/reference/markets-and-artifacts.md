# Markets and artifacts

Milestone 0.4 adds explicit chart downloads plus structured forex, crypto, and
futures data. The same client, cache, single-flight, retry, and error rules
apply throughout.

Artifacts are not tables. None of these operations claims provider history.

## Describe and download an artifact

A descriptor is pure. It builds a reviewed URL and performs no network request.
Downloading is a separate, explicit operation.

```python
from datetime import datetime, timezone

from finvizp import FinvizClient, chart_descriptor, download_artifact_async

descriptor = chart_descriptor(
    "AAPL",
    timeframe="1d",
    fetched_at=datetime.now(timezone.utc),
)

async with FinvizClient() as client:
    downloaded = await download_artifact_async(
        descriptor,
        client=client,
        path="aapl-day.png",
    )

print(downloaded.content_hash, downloaded.content_length, downloaded.path)
```

`download_artifact()` is the sync twin.

The downloaded descriptor records a SHA-256 `content_hash` and
`content_length`, and stores bytes either in `content` or at the caller's
`path`. Bodies are never cached or written implicitly.

### Download safety

- URLs use the reviewed symbol, timeframe, and spectrum grammars.
- Sources must stay on Finviz origins or the provider's chart renderer.
- Redirects are checked hop by hop. Cross-origin hops and loops fail.
- The response must have an image media type and valid magic bytes.
- Declared content length and the construction-time byte limit are enforced.
- Challenge HTML masquerading as an image raises `FinvizParseError`.
- Login or Elite redirects raise `FinvizEntitlementError`.

`spectrum_async()` returns the same descriptor type.

## Forex and crypto

The families are module-level because their names would collide at package root:
`finvizp.forex` and `finvizp.crypto`.

| Operation | Source | Result |
|---|---|---|
| `performance_async()` | `/forex_performance.ashx` or `/crypto_performance.ashx` | Wide Arrow table, one row per pair. |
| `tiles_async()` | `/forex.ashx` or `/crypto.ashx` | Frozen `TileBundle` with prices, changes, ranges, and labels. |
| `chart_async(symbol, timeframe=...)` | Provider chart gallery | `Artifact` descriptor; download separately. |

Percent changes are decimal fractions. Raw provider displays remain in
`*_raw` companions. Unknown tile fields go to `extra_fields`.

The sparkline array stays verbatim. The provider supplies no point timestamps,
so `sparkline_timestamps` and `sparkline_interval_seconds` remain `None`.

## Futures

`finvizp.futures.futures_async()` reads embedded tile JSON from `/futures` into
the versioned `futures_tiles` schema.

The table contains contract identity, current price fields, raw displays,
`delay_minutes`, and verbatim `sparkline` and `sparkline_date_changes` values.

The provider's tile `change` is a percentage. `change_percent` and
`change_usd` are separate fields. The old `finvizfinance` empty performance
table is not modeled.

## Access boundaries

All 0.4 capabilities are public-tier operations. They use bounded sequential
smokes, no enumeration, no rate escalation, and no Elite export endpoints.
