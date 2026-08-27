# finvizp Implementation Roadmap

> **For Hermes:** Execute this roadmap through the dedicated `finvizp` Kanban board. Each code card uses an isolated worktree, strict TDD, same-card review, and merge-before-completion.

**Goal:** Deliver the complete frozen public Finviz surface as a stable async-first, Arrow-native Python package through milestones 0.1–1.0.

**Architecture:** Public endpoint functions sit over three deep modules: transport (`FinvizClient`), immutable result/cache policy, and schema-driven Arrow normalization. Pure parsers remain transport-free. Each milestone ends in an integration/audit card; central manifests and exports are changed there instead of by parallel endpoint cards.

**Tech Stack:** Python 3.11–3.14, `uv`, `fastreq[curl]`, lxml, PyArrow, pytest, Ruff, ty, GitHub Actions, Hermes Kanban.

---

## Normative inputs

Every worker must read the relevant sections of:

1. `docs/superpowers/specs/2026-08-27-finvizp-foundation-design.md`
2. `docs/brainstorming/design-decisions.md`
3. `docs/research/public-surface-inventory.md`
4. `docs/research/source-ledger.md`
5. the milestone plan linked from its card

The approved foundation specification wins if supporting documents disagree. No card may expand the frozen 1.0 scope without a human decision recorded on the board.

## Module map

```text
src/finvizp/
├── __init__.py               # curated public exports; integration cards own it
├── client.py                 # request lifecycle, route/auth isolation, classification
├── cache.py                  # parsed-result cache adapter, LRU, single-flight
├── errors.py                 # public exception hierarchy and safe context
├── results.py                # immutable FetchResult and metadata/diagnostics
├── models.py                 # compound bundles and artifact descriptors
├── schemas.py                # checked-in schema registry loader/validator
├── arrow.py                  # schema-driven normalization and table construction
├── symbols.py                # symbol universe/search operations
├── quote.py                  # quote bundle operation and projections
├── statements.py             # six statement forms
├── screener.py               # typed query execution and pagination
├── earnings.py               # earnings-specific screener projections
├── groups.py                 # group dimensions/views
├── maps.py                   # structured map hierarchy/constituents
├── news.py                   # global and publisher metadata
├── insider.py                # global/fund/manager feeds
├── calendar.py               # economic event and release-detail data
├── artifacts.py              # chart/spectrum descriptors and explicit downloads
├── forex.py                  # forex structured data + artifact projections
├── crypto.py                 # crypto structured data + artifact projections
├── futures.py                # current embedded tile data
├── capabilities.json         # frozen public/legacy capability ledger
├── schema_registry.json      # versioned dataset definitions
├── _sync.py                  # event-loop-safe sync bridge
├── _symbols.py               # canonical symbol normalization and resolution
├── _queries/                 # immutable typed query models/registries
├── _parsers/                 # pure HTML/XML/JSON parsers
└── _dev/                     # explicit fixture/registry drift tools, never public API
```

This is a target ownership map, not permission to pre-create empty modules. A card creates a file only when its first tested behavior needs it. Do not add repository/adapter/protocol layers for hypothetical alternatives. The only initial adapter seams are the two real variants: built-in cache versus caller cache, and real transport versus hermetic fake transport.

## Dependency graph

```text
human start gate
  └─ 0.1 contracts
      ├─ schemas/normalization ─┬─ symbols ───────────┐
      │                         ├─ statements ────────┤
      │                         └─ quote parser ─ quote API
      └─ client ─ cache ───────────┘                 │
                                                     └─ 0.1 integration → 0.1 audit

0.1 audit → 0.2 registries → screener → signals/earnings ─┐
                            └──────────── drift tooling ───┴→ integration → 0.2 audit

0.2 audit ─┬→ groups ───┐
           ├→ maps ─────┤
           ├→ news ─────┤
           ├→ insider ──┤
           └→ calendar ─┴→ integration → 0.3 audit

0.3 audit ─┬→ artifacts ────┐
           ├→ forex/crypto ─┤
           └→ futures ──────┴→ integration → 0.4 audit

0.4 audit → parity/migration ─┐
          ├→ schema hardening ├→ 0.5 audit → 1.0 release-readiness audit
          └→ live/docs matrix ┘
```

## Milestone plans

| Milestone | Plan | Outcome |
|---|---|---|
| 0.1 | [`2026-08-27-0.1-core-symbols-quotes-statements.md`](2026-08-27-0.1-core-symbols-quotes-statements.md) | trustworthy client/result/schema foundation plus symbol universe, symbol search, statements, and complete quote bundles |
| 0.2 | [`2026-08-27-0.2-screeners.md`](2026-08-27-0.2-screeners.md) | typed registries, pagination, signals, earnings screens |
| 0.3 | [`2026-08-27-0.3-groups-maps-events.md`](2026-08-27-0.3-groups-maps-events.md) | groups, maps, news, insider, economic events/details |
| 0.4 | [`2026-08-27-0.4-markets-artifacts.md`](2026-08-27-0.4-markets-artifacts.md) | charts/spectrum, forex, crypto, current futures |
| 0.5–1.0 | [`2026-08-27-0.5-to-1.0-hardening.md`](2026-08-27-0.5-to-1.0-hardening.md) | parity, schema hardening, migration docs, live matrix, stable-release audit |

## Kanban execution protocol

### Dormant start

The card graph is seeded behind one typed `needs_input` card:
`gate: approve finvizp implementation start`. Create the gate unassigned, apply
the typed block, and only then assign `default`; this prevents the current
promotion sweep from dispatching a generic root block. No worker may run until
the user explicitly approves implementation and that gate is completed. Creating
plans and the board does not authorize code implementation or package publication.

### Workspace and branch policy

- Every implementation card uses a board-managed git worktree and a unique `kanban/<card-key>` branch.
- Before writing code, fetch `origin`, verify the worktree is based on current `origin/main`, and inspect parent summaries.
- Do not edit another card's worktree, reuse its branch, or commit unrelated dirty files.
- Central collision hotspots—`__init__.py`, `capabilities.json`, `schema_registry.json`, README/reference indexes, and `uv.lock`—are owned by explicit integration cards unless a task specifically lists them.

### TDD and verification

For each behavior:

1. Add one focused test.
2. Run that exact test and record the expected RED failure.
3. Add the minimal implementation.
4. Run the exact test to GREEN.
5. Refactor only while green.
6. Run the card's focused test file and then the full repository gates.

Default repository gates:

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run ty check src scripts
uv run python scripts/check_docs.py
rm -rf dist && uv build
uvx --from twine twine check dist/*
git diff --check
```

Live checks are opt-in and bounded. They never replace scrubbed fixtures.

### Same-card review and merge-before-completion

1. The coder commits, pushes the unique branch, opens a PR, and calls `kanban_request_review` with changed files, RED/GREEN evidence, verification commands, PR URL, provider evidence, and residual risks.
2. The reviewer independently checks the specification, provider semantics, secret redaction, fixtures, and full gates. The reviewer does not silently fix findings.
3. Findings return through `kanban_request_changes`; the same coder/worktree handles them.
4. On approval, the reviewer merges the PR, verifies the merged `main`/CI state, and only then calls `kanban_complete`.
5. Consequently, a child becoming ready means all parent code is merged—not merely committed on an isolated branch.

Never tag, publish, create a GitHub release, or alter PyPI trusted-publisher
settings from an implementation card. Release mechanics require a separate
explicit user approval after the 1.0 audit.

## Provider evidence rules

- Before implementing a family, make one bounded probe through the actual `FinvizClient` transport or capture a minimal scrubbed fixture through the explicit fixture hook.
- Record URL pattern, representation, access tier evidence, relevant headers, observed structural fingerprint, and observation date without raw cookies, proxy URLs, or tracking data.
- A public JSON/XHR source replaces HTML only when it is same-tier, complete, direct, no more request-intensive, and no less snapshot-coherent.
- Never follow sitemap entries, crawl publishers/funds/managers/releases, automate login, execute JavaScript, solve challenges, or rotate identity after 403/429/entitlement responses.
- Live values and row counts are examples, never assertions hard-coded into unit tests.

## Definition of done by milestone

A milestone audit card may complete only when:

- every milestone capability is implemented in `capabilities.json` with operation, representation, access tier, schema, fixtures, tests, and docs;
- all public sync/async pairs exist and sync calls reject active event loops clearly;
- strict/partial/empty behavior is tested for relevant collectors;
- deterministic Arrow schemas and empty tables are tested;
- default CI is hermetic and green on Python 3.11–3.14;
- bounded live evidence is current enough for the milestone and categorized as success, access/network failure, or parser drift;
- documentation describes provenance, limits, and access boundaries honestly;
- merged `main` is clean and CI is green.

## Deliberate non-goals

No pandas compatibility layer, raw-request hatch, persistence/scheduler,
database/Parquet helper, browser automation, third-party article retrieval,
public bulk crawler, authentication automation, telemetry, or unverified Elite
schemas. Never bypass access controls. These cannot be introduced as “helpful”
implementation details.