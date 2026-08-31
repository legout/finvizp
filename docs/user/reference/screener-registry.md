# Screener registry and drift tooling (0.2)

`src/finvizp/screener_registry.json` is the checked-in, human-reviewed source
of truth for screener metadata: filters (with their categorical options),
signals, orders, views, and columns. Every screener query is validated against
it before any network request is made, so a stale registry fails safely — it
rejects queries the provider now supports — but never invents provider codes.

The registry is data, reviewed like code. Nothing in the library writes to it
at runtime.

## Observing drift

`finvizp._dev.screener_drift` (developer tooling, imported by explicit path —
never part of the public API) builds a reviewable drift report:

```python
from finvizp._dev.screener_drift import build_live_report
from finvizp import FinvizClient

report = build_live_report(live=True, client=FinvizClient())
# or write it out for review:
build_live_report(live=True, out_path=pathlib.Path("drift.json"))
```

Live access is always opt-in: the call requires an explicit `live=True`
keyword. It performs exactly two metadata requests — the custom view page
(`v=151`, carrying the order and signal dropdowns) and the all-filters layout
(`v=111&ft=4`, carrying every filter select) — fetched once, never crawled or
scheduled. `collect_observations(client=...)` runs the same bounded fetch if
you want the raw observation mapping instead of a report.

The report is deterministic JSON (`meta` + `report` sections) listing
`added`, `removed`, and `changed` entries per namespace, paired by human name
and sorted. Strings from the provider that carry markup or shell-ish
characters are replaced with `[redacted]`; the report never contains cookies,
proxy configuration, or raw response bodies. Only namespaces actually observed
are compared, so an unobserved namespace is never misreported as removed.

For offline work, `build_live_report(live=False, observations=...)` compares
supplied observations instead of fetching — this is how the report shape is
exercised in tests.

## Approval and update workflow

1. Run the report (bounded, opt-in) and read the diff.
2. For each entry, verify the change is real metadata — not a provider
   experiment, A/B artifact, or Elite-gated surface. Cross-check the live page
   by hand.
3. Edit `screener_registry.json` deliberately: new entries get human names and
   grammar-conformant codes; changed entries keep their `name` key. Bump
   `version` and update `observation_date`.
4. Review the registry diff like any code change; the typed registry loader
   (`finvizp._queries.screener._parse_registry`) will reject duplicate names,
   grammar-violating codes, and empty option lists at import time.
5. Commit. The tool never mutates the registry — `registry_mutated` in every
   report is always `false` by construction.

Fixed-view column drift is out of scope for this tool: it is covered by the
page parser's header-driven column contract.
