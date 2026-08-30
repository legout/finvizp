# Markets and artifacts (0.4)

The 0.4 surface adds the chart/spectrum artifact contract and the three
market families over the same client seam as 0.1–0.3: explicit bounded chart
downloads, structured forex and crypto data, and current futures tiles. Every
operation is async-first with a sync wrapper, returns the immutable
`FetchResult` envelope, and honors the shared cache, single-flight, retries,
and typed errors. Artifacts are never tables and never claimed history.

## Artifacts: descriptors describe, downloads download

`finvizp.chart_descriptor(symbol, timeframe=..., fetched_at=...)` constructs
an immutable `finvizp.Artifact` descriptor — source URL, kind, media type,
canonical symbol, timeframe — **purely from the reviewed grammar, with zero
network I/O**. Constructing or describing an artifact never fetches bytes.

`finvizp.download_artifact_async(descriptor, client=...)` (sync wrapper:
`finvizp.download_artifact`) is the only path to raw bytes. It returns a
derived immutable descriptor stamped with `content_hash` (SHA-256) and
`content_length`, carrying either `content` (in-memory) or `path` (bytes
atomically written to the caller's file, then dropped from memory). Bodies
are never cached and never written implicitly.

```python
import asyncio
from datetime import datetime, timezone

from finvizp import FinvizClient, chart_descriptor, download_artifact_async


async def main() -> None:
    descriptor = chart_descriptor("AAPL", timeframe="1d", fetched_at=datetime.now(timezone.utc))
    async with FinvizClient() as client:
        downloaded = await download_artifact_async(descriptor, client=client, path="aapl-day.png")
    print(downloaded.content_hash, downloaded.content_length, downloaded.path)


asyncio.run(main())
```

Safety contracts, enforced before and during any request:

- chart URLs are built only from the canonical-symbol grammar and provider
  timeframe codes; spectrum URLs only from reviewed dimension letters. A
  descriptor's `source_url` must already sit on a Finviz origin (or the
  provider's own `charts2-node.finviz.com` chart renderer) or the download is
  rejected before any network access.
- redirects are followed hop-by-hop only within Finviz origins; an
  Elite/login landing raises `FinvizEntitlementError`, a cross-origin hop or
  redirect loop raises `FinvizTransportError`.
- the response must be an image by media type **and** by magic bytes, within
  the descriptor's media family; truncation is detected against the declared
  `Content-Length`; HTML challenge bodies masquerading as images raise
  `FinvizParseError`.
- the byte bound is `min(DOWNLOAD_LIMIT, client cache budget)` — bounded by
  construction.

The legacy `finvizfinance` chart helper's arbitrary parameter interpolation
is structurally impossible on this path. `spectrum_async` (0.3) returns the
same descriptor type; its bytes download through the same helpers.

## Forex and crypto: structured tiles, performance tables, chart descriptors

The families are module-level: `finvizp.forex` and `finvizp.crypto` mirror
each other (their operation names would collide at top level). Each exposes:

- `performance_async(client=...)` — one `/forex_performance.ashx`
  (`/crypto_performance.ashx`) request into a deterministic wide Arrow table:
  one row per pair (crypto: ticker + name), provider performance columns
  typed from their displays, verbatim `_raw` companions, `extra_fields`,
  `fetched_at`. A populated table with zero data rows is the recognized
  `EMPTY` state; structural drift raises `FinvizParseError`.
- `tiles_async(client=...)` — the `/forex.ashx` (`/crypto.ashx`) page's
  embedded first-party tile JSON as a `TileBundle` of frozen `TileRow`s:
  ticker, label, last price, percent change (decimal fraction), absolute USD
  change, prev close, high, low. The sparkline array is preserved **verbatim
  as raw payload text**: the provider sends no per-point timestamps, so no
  history is ever inferred (`sparkline_timestamps`/
  `sparkline_interval_seconds` are always `None`). Unknown tile fields land
  in `extra_fields`.
- `chart_async(symbol, timeframe=..., client=...)` — resolves the gallery's
  `charts2-node.finviz.com` image URL verbatim into an `Artifact` descriptor
  (never constructed, never fetched); download via the artifact helpers.

Sync wrappers (`performance`, `tiles`, `chart`), `cache=`/`refresh=` controls
and typed errors behave like every other family.

## Futures: current embedded tiles, not the legacy empty table

`finvizp.futures.futures_async(client=...)` reads the `/futures` page's
embedded tile JSON into the registered `futures_tiles` Arrow schema
(version 1): contract/category identity (`symbol`, `name`, `category`),
current price facts (`last`, `change_percent`, `change_usd`, `prev_close`,
`high`, `low` — each with its verbatim `_raw` companion), honest temporal
fields (`sparkline`, `sparkline_date_changes` preserved verbatim; `delay_minutes`),
`extra_fields`, and `fetched_at`.

The provider's tile `change` is the percent change (verified evidence); the
dataset has no separate absolute-`change` column for it — percent and
absolute are distinct columns, matching the cross-dataset convention. The
legacy `finvizfinance` futures performance table (empty since the provider
moved to tiles) is not modeled.

## Access and entitlement boundaries

All 0.4 capabilities are PUBLIC tier. The smoke contract is unchanged: one
sequential request per family, no enumeration, no retries, no rate
escalation, and Elite export endpoints are never touched. Artifact downloads
are explicit and bounded everywhere; a login/Elite redirect surfaces as
`FinvizEntitlementError`, never as silent content.
