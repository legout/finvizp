# Screener registry and drift tooling

`src/finvizp/screener_registry.json` is the reviewed source of truth for
screener filters, signals, orders, views, and columns.

Every query is checked against it before the client sends a request. A stale
registry can reject a provider option that was added later, but it never
invents a provider code.

The library reads this file at runtime. It never edits it.

## Produce a drift report

The drift tool is developer-only and is not part of the public API.

```python
from pathlib import Path

from finvizp import FinvizClient
from scripts.screener_drift import build_live_report

with FinvizClient() as client:
    report = build_live_report(live=True, client=client)

build_live_report(live=True, out_path=Path("drift.json"))
```

!!! warning "Live access is opt-in"
    `live=True` is required. The report makes exactly two metadata requests:
    the custom view page (`v=151`) and the all-filters layout (`v=111&ft=4`).
    It does not crawl, schedule, or enumerate the site.

Use `collect_observations(client=...)` when you need the raw observation
mapping rather than the report.

## Report contents

The JSON report has `meta` and `report` sections. Each namespace lists sorted
entries under `added`, `removed`, or `changed`.

The tool:

- pairs entries by human name;
- compares only namespaces observed in the current run;
- replaces provider strings containing markup or shell-like characters with
  `[redacted]`;
- omits cookies, proxy configuration, and raw response bodies.

An unobserved namespace is not reported as removed.

## Approve a change

1. Run the bounded report and read the diff.
2. Check each difference against the live page. Rule out an experiment, an
   A/B variant, or an Elite-only option.
3. Edit `screener_registry.json` deliberately.
4. Keep existing `name` keys when changing entries. New entries need a human
   name and a grammar-valid provider code.
5. Bump `version` and `observation_date`.
6. Review the registry diff like code, then commit it.

The typed loader rejects duplicate names, invalid codes, and empty option
lists. The report never mutates the registry; `registry_mutated` is always
`false`.

!!! note
    Fixed-view column drift is handled by the page parser's header contract,
    not by this tool.
