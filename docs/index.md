# finvizp research and design

**Research date:** 2026-08-27

**Project status:** milestone 0.1 implemented (symbols, statements, quote
bundles); later milestones planned per the roadmap

`finvizp` means **Finviz for Python**. It is intended to be an async,
Arrow-native public Finviz client covering the verified 2026-08-27 public
surface and all `finvizfinance` capabilities through a smaller, deeper interface.

## Read this first

1. [Product and scope](brainstorming/product-and-scope.md) — why the package
   exists, capability-completeness, non-goals, and the meaning of the name.
2. [Finviz data surface](research/finviz-data-surface.md) — every endpoint
   family and temporal shape identified in `finvizfinance` and current Finviz.
3. [Frozen public-surface inventory](research/public-surface-inventory.md) — the
   exact evidence-backed feature/access cutoff that gates public 1.0.
4. [finvizfinance audit](research/finvizfinance-audit.md) — what is reusable as
   knowledge, what broke against the live site, and why `finvizp` is a clean
   implementation rather than a fork.
5. [Finviz/Yahoo overlap](research/finviz-yahoo-overlap.md) — fields available
   from both providers and why provider-specific observations can still be
   valuable.
6. [Snapshot history](brainstorming/snapshot-history.md) — true provider
   history versus history accumulated by repeated captures.
7. [Architecture](brainstorming/architecture.md) — approved module seams,
   transport, Arrow contracts, data flow, error handling, and testing strategy.
8. [Access and entitlements](research/access-and-entitlements.md) — public,
   authenticated Elite, robots directives, pacing, and non-bypass policy.
9. [Confirmed design decisions](brainstorming/design-decisions.md) — the closed
   decision register from nine grilling rounds.
10. [Research source ledger](research/source-ledger.md) — audited revisions,
   URLs, bounded live findings, and reproducibility caveats.
11. [Publishing](publishing.md) — explicit version tags, GitHub Actions,
   trusted PyPI publishing, and release verification.
12. [Implementation roadmap](plans/2026-08-27-implementation-roadmap.md) —
    module ownership, milestone dependencies, Kanban workflow, verification,
    and links to the executable 0.1–1.0 plans.

The approved foundation design is recorded at
[docs/superpowers/specs/2026-08-27-finvizp-foundation-design.md](superpowers/specs/2026-08-27-finvizp-foundation-design.md).
The machine-readable dormant board graph is
[`docs/plans/kanban-cards.json`](plans/kanban-cards.json).

## Using finvizp (0.1)

- [Results, provenance, and errors](reference/results.md) — the immutable
  `FetchResult` envelope, status/provenance facts, batching semantics, and
  the typed error hierarchy.
- [Arrow schemas in 0.1](reference/schemas-0.1.md) — every registered
  dataset, units, `_raw` companions, and the versioning contract.
- [Screener](reference/screener.md) — the 0.2 screener surface: typed
  queries, fixed/custom view schemas, pagination and safety semantics,
  signals, earnings screens, and bounded live smokes.
- [Groups, maps, and events](reference/groups-maps-events.md) — the 0.3
  surface: typed group queries and spectrum descriptors, structured map
  bundles, global/publisher news with typed temporal fields, insider feed
  families, and the economic calendar with release details.
- [Markets and artifacts](reference/markets-and-artifacts.md) — the 0.4
  surface: immutable chart/spectrum descriptors with explicit bounded
  downloads, module-level forex/crypto structured tiles and performance
  tables, and current futures tile data with honest temporal semantics.
- [Proxies and cache](how-to/proxies-and-cache.md) — proxy precedence,
  opt-in TTL caching with single-flight, provisional conservative
  defaults, and caller-owned persistence.

The implemented-versus-planned capability ledger is machine-readable:
`finvizp.capabilities()` (see `src/finvizp/capabilities.json` and
`finvizp.provisional_defaults()` for the conservative transport defaults).

## Evidence base

The research used:

- `lit26/finvizfinance` 1.4.0 at commit
  `c8d461d1991da1675edc63ea0238391e6f0ba776`;
- source and fixture tests from the cloned repository;
- bounded public quote, statement, screener, group, maps, news, insider,
  calendar/detail, forex, crypto, futures, ETF/options-shell, and chart probes;
- official Finviz screener help, Elite feature table, and `robots.txt`;
- official navigation and sitemap indexes for the frozen public inventory;
- bounded verification of the stock sitemap manifest and public JSON symbol
  suggestions endpoint;
- the local `yfin` 1.4.0 and `fastreq` 3.2.0 implementations;
- live `yfin` probes of `financialData` and `defaultKeyStatistics`.

Research observations are not promises of endpoint stability or permission to
collect data. Entitlement and data-use requirements must be verified by each
caller.
