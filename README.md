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

Research and design foundation only. The package is importable, but endpoint
implementations will be created from a separately reviewed implementation
plan.

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

## Documentation

Start at [`docs/index.md`](docs/index.md). The research explains the complete
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
