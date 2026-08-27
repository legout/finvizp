# finvizp research and design

**Research date:** 2026-08-27

**Project status:** confirmed foundation design; no endpoint implementation plan exists yet

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

The approved foundation design is recorded at
[docs/superpowers/specs/2026-08-27-finvizp-foundation-design.md](superpowers/specs/2026-08-27-finvizp-foundation-design.md).

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
