# finvizp

[![CI](https://github.com/legout/finvizp/actions/workflows/ci.yml/badge.svg)](https://github.com/legout/finvizp/actions/workflows/ci.yml)
[![Python 3.11-3.14](https://img.shields.io/badge/python-3.11--3.14-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Finviz for Python** — an unofficial, async-first, Arrow-native Finviz client
built on [`fastreq`](https://pypi.org/project/fastreq/), direct lxml, and
PyArrow.

> [!IMPORTANT]
> `finvizp` is not affiliated with, endorsed by, or sponsored by Finviz.
> Finviz is a trademark of its respective owner.

## Install

```bash
uv add finvizp  # or: pip install finvizp
```

## Quick start

Async-first:

```python
import asyncio
import finvizp


async def main() -> None:
    async with finvizp.FinvizClient() as client:
        universe = await finvizp.symbols_async(client=client)
        bundle = await finvizp.quote_async("AAPL", client=client)
        print(bundle.data.snapshot.num_rows)


asyncio.run(main())
```

Every operation has a **sync twin** (same name, no `_async` suffix) for
scripts and notebooks:

```python
import finvizp

matches = finvizp.search_symbols("APPLE")
income = finvizp.statements("AAPL", statement="IA")  # IA/IQ/BA/BQ/CA/CQ
snap = finvizp.snapshot("AAPL")  # cached, no 2nd request
quote = finvizp.quote("AAPL")  # complete bundle
universe = finvizp.symbols()
finvizp.capabilities()  # implemented/planned ledger
```

Sync twins reject an active event loop; results, caching, and errors are
identical.

## What's implemented

| Milestone | Surface | Status |
|---|---|---|
| 0.1 | Symbols, statements, quote bundles, cache | ✅ |
| 0.2 | Screener, signals/earnings, registry drift tools | ✅ |
| 0.3 | Groups, spectrum, maps, news, insider, calendar | ✅ |
| 0.4 | Markets, artifacts (charts/spectrum), forex/crypto, futures | ✅ |
| 0.5 | Frozen-surface + finvizfinance parity audit | ✅ |
| 1.0 | Stable release | pending |

The full implemented/planned ledger lives in the
[capability matrix](docs/user/reference/capability-matrix.md) and
`finvizp.capabilities()`.

## Design in one screen

- **Async-first with sync twins** — one transport, no hidden event loops.
- **Arrow-native** — every dataset is a versioned, deterministic Arrow table.
- **Immutable `FetchResult[T]`** — full provenance, typed partial/error
  diagnostics, no in-place mutation.
- **Complete compound bundles** — e.g. a quote page yields every relation in
  one fetch; cache-preserving projections (`snapshot`, `ratings`, `news`,
  `insider`, `peers`, `etf_holders`) never re-request.
- **Structured-first sources** — public JSON/XHR or embedded payloads when
  same-tier, complete, direct, and snapshot-coherent; lxml parsing otherwise.
- **Explicit proxies, route-isolated auth, conservative pacing/retries,
  bounded result caching, single-flight coalescing.** A per-client 429
  circuit breaker trips after 3 consecutive rate limits and fails fast
  (`CircuitOpenError`, no request sent) until the cooldown passes.
- **No** login/browser automation, bulk crawling, pandas contract,
  persistence layer, telemetry, or entitlement bypass.

## Documentation

User docs (Diataxis — tutorials, how-to, reference, explanation), rendered
with [Zensical](https://zensical.org) from
[`docs/user/`](docs/user/):

| You want to… | Go to |
|---|---|
| Try it in 5 minutes | [Get started](docs/user/tutorials/get-started.md) |
| Use the sync API | [Use the sync API](docs/user/how-to/sync-api.md) |
| Set proxies / caching | [Proxies & cache](docs/user/how-to/proxies-and-cache.md) |
| Migrate from `finvizfinance` | [Migration guide](docs/user/how-to/migrate-from-finvizfinance.md) |
| Check a dataset's columns | [Arrow schemas](docs/user/reference/schemas-0.1.md) |
| Understand result envelopes | [Results](docs/user/reference/results.md) |
| See every capability + status | [Capability matrix](docs/user/reference/capability-matrix.md) |

Deep links: [screener](docs/user/reference/screener.md) ·
[screener registry](docs/user/reference/screener-registry.md) ·
[groups/maps/events](docs/user/reference/groups-maps-events.md) ·
[markets/artifacts](docs/user/reference/markets-and-artifacts.md) ·
[schema versioning](docs/user/reference/schema-versioning.md) ·
[keep your own history](docs/user/how-to/caller-owned-history.md) ·
[access & responsible use](docs/user/explanation/access-and-responsible-use.md)

Build the site locally:

```bash
uv run zensical build --clean
```

Background research (surface inventory, finvizfinance audit, Yahoo overlap,
snapshot-history semantics) lives under
[`docs/research/`](docs/research) and
[`docs/brainstorming/`](docs/brainstorming); the
[foundation design](docs/superpowers/specs/2026-08-27-finvizp-foundation-design.md)
tracks the approved architecture. Publication mechanics:
[`docs/publishing.md`](docs/publishing.md).

## Development

```bash
uv sync --all-groups
uv run pytest
uv run ruff format --check . && uv run ruff check .
uv run ty check src
uv build
```

## Access policy

Finviz public pages, Elite features, and export/API endpoints carry
different entitlements. `finvizp` does not automate login, solve challenges,
bypass access controls, or rotate proxies to evade limits. Callers are
responsible for valid access and data-use rights. See
[access & responsible use](docs/user/explanation/access-and-responsible-use.md).
