# finvizp

[![CI](https://github.com/legout/finvizp/actions/workflows/ci.yml/badge.svg)](https://github.com/legout/finvizp/actions/workflows/ci.yml)
[![Python 3.11-3.14](https://img.shields.io/badge/python-3.11--3.14-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Finviz for Python** — a planned public, async-first, Arrow-native Finviz
client built on [`fastreq`](https://pypi.org/project/fastreq/), direct lxml, and
PyArrow.

> [!IMPORTANT]
> `finvizp` is an unofficial project. It is not affiliated with, endorsed by,
> or sponsored by Finviz. Finviz is a trademark of its respective owner.

The short name deliberately mirrors `yfin`: `finvizp` means “Finviz for
Python,” while leaving room for a clean interface rather than preserving the
legacy `finvizfinance` class surface.

## Current status

Milestone **0.1** implements the verified core surface: the symbol universe
and search, all six public statement forms, complete quote-page bundles with
cached relation projections, an immutable result/error contract, versioned
Arrow schemas, and a bounded result cache with single-flight coalescing.
Later capabilities are seeded as `planned` in the capability manifest
(`finvizp.capabilities()`).

Public 1.0 is capability-complete for the verified public Finviz surface frozen
on 2026-08-27, including everything available through `finvizfinance` plus:

- complete compound stock-page bundles rather than one partial quote table;
- maps data, publisher news, fund/manager insider pages, economic details, and
  current futures tile data;
- a canonical symbol-universe method backed by the published stock sitemap and
  a separate bounded JSON symbol-search method;
- all statement, screener/signal, group, news, insider, earnings/calendar,
  forex, crypto, chart, and spectrum outcomes;
- async-first transport using `fastreq[curl]`;
- direct lxml parsers and versioned deterministic Arrow schemas;
- structured-first source selection when a public first-party JSON/XHR or
  embedded payload is same-tier, complete, direct, and snapshot-coherent;
- immutable `FetchResult[T]` envelopes, compound bundles, typed partial/error
  diagnostics, and caller-owned point-in-time history metadata;
- explicit proxies, route-isolated auth, conservative pacing/retries, bounded
  result caching, and single-flight request coalescing;
- no login/browser/challenge automation, bulk site crawler, pandas contract,
  persistence layer, telemetry, or entitlement bypass.

Login- and Elite-only features are deferred until they can be legitimately
verified. The public package supports Python 3.11-3.14.

## Quick start

```bash
uv add finvizp  # or: pip install finvizp
```

Every operation is async-first with a sync twin, and returns an immutable
`FetchResult` whose `.data` is an Arrow table (or a compound bundle):

```python
import asyncio

import finvizp


async def main() -> None:
    async with finvizp.FinvizClient() as client:
        # Symbol universe: exactly one request to the published stock manifest.
        universe = await finvizp.symbols_async(client=client)

        # Bounded ranked symbol search.
        matches = await finvizp.search_symbols_async("APPLE", client=client)

        # All six statement forms: IA/IQ/BA/BQ/CA/CQ.
        income = await finvizp.statements_async("AAPL", statement="IA", client=client)

        # Complete quote bundle; one page fetch, every relation.
        bundle = await finvizp.quote_async("AAPL", client=client)
        for table in bundle.data:
            print(table.snapshot.num_rows)


asyncio.run(main())
```

The sync twins (`finvizp.symbols()`, `finvizp.search_symbols()`,
`finvizp.statements()`, `finvizp.statements_batch()`, `finvizp.quote()`,
`finvizp.snapshot()`, …) reject an active event loop and otherwise behave
identically. Cache-preserving quote projections — `snapshot()`, `ratings()`,
`news()`, `insider()`, `peers()`, `etf_holders()` — reuse the cached page and
perform no second request.

The 0.2/0.3 surface works the same way: `screen_async`/`signal_async`/
`earnings_async` for the screener, `group_async`/`spectrum_async` for group
aggregates and spectrum descriptors, `map_async` for the structured S&P 500
map bundle, `global_news_async`/`publisher_news_async` for news metadata,
`global_insider_async`/`fund_insider_async`/`manager_insider_async` for the
insider feeds, and `calendar_async`/`calendar_detail_async` for the economic
calendar. Publisher, fund, manager, and release operations take one explicit
caller identifier and never enumerate their sitemaps.

Result envelopes carry full provenance; `strict` batching raises
`FinvizPartialError` with the successful prefix preserved, and
`allow_partial=True` returns a `PARTIAL` result with typed `unit_errors`.
See [`docs/reference/results.md`](docs/reference/results.md) for the
envelope, [`docs/reference/schemas-0.1.md`](docs/reference/schemas-0.1.md)
for the Arrow tables,
[`docs/reference/screener.md`](docs/reference/screener.md) for the screener
surface, [`docs/reference/groups-maps-events.md`](docs/reference/groups-maps-events.md)
for the 0.3 families, and
[`docs/how-to/proxies-and-cache.md`](docs/how-to/proxies-and-cache.md) for
proxies, caching, and caller-owned persistence.

## Documentation

Start at [`docs/index.md`](docs/index.md), which links the user docs: the
result-envelope reference
([`docs/reference/results.md`](docs/reference/results.md)), the Arrow
schema tables ([`docs/reference/schemas-0.1.md`](docs/reference/schemas-0.1.md)),
the screener surface ([`docs/reference/screener.md`](docs/reference/screener.md)),
the 0.3 groups/maps/events families
([`docs/reference/groups-maps-events.md`](docs/reference/groups-maps-events.md)),
and the proxy/cache how-to
([`docs/how-to/proxies-and-cache.md`](docs/how-to/proxies-and-cache.md)).
The research explains the complete
Finviz surface, audits `finvizfinance`, compares overlapping Yahoo data, and
defines what “history” means for snapshot-only statistics. The approved spec,
confirmed decision register, and implementation plans are complete. Start with
the [implementation roadmap](docs/plans/2026-08-27-implementation-roadmap.md),
which is mirrored onto a dedicated dormant Hermes Kanban board.

Publication and release mechanics are documented in
[`docs/publishing.md`](docs/publishing.md). No package has been published from
this foundation scaffold.

## Development foundation

```bash
uv sync --all-groups
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run ty check src
uv build
```

## Access policy

Finviz public pages, authenticated Elite features, and export/API endpoints
have different entitlements. `finvizp` will not automate login, solve
challenges, bypass access controls, or use proxy rotation to evade limits.
Callers are responsible for valid access and data-use rights.
