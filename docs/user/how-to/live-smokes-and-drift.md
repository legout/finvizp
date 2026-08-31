# Run live smokes and handle drift

The default suite is hermetic. Tests under `tests/live/` are the opt-in check
against Finviz's current public responses.

## Run them

```bash
uv run pytest -q tests/live -m live_public
uv run pytest -q tests/live -m live_elite  # legitimate session state required
```

Plain `pytest`, and CI's normal test job, skip these tests. The live test
configuration requires the `live_public` marker expression.

Each smoke uses one explicit, stable argument. It does not enumerate
sitemaps, tickers, publishers, funds, managers, releases, or filter grids.
The client's own bounded retry policy is the only retry policy involved.

## Read a failure

| Category | Exception | What it means | Response |
|---|---|---|---|
| Network/access | `FinvizTransportError`, `FinvizRateLimitError` | Connectivity, 5xx, or rate limiting. | Skip and retry later. |
| Block/entitlement | `FinvizBlockedError`, `FinvizEntitlementError` | Challenge, 403, login, or Elite wall. | Never bypass it. Check whether the route was meant to be public. |
| Parser drift | `FinvizParseError` | Provider shape changed. | Refresh evidence and route the change for review. |
| Data conversion | `FinvizDataError` | A valid payload no longer fits the typed contract. | Fix conversion and add a fixture row. |
| Assertion | Any other exception | The smoke itself failed. | Fix the smoke or the operation. |

The helper in `tests/live/_smoke.py` keeps these categories separate.

## Confirmed parser drift

1. Capture the current structure with one bounded probe. Do not crawl.
2. Update the structural fixture and its `_build.py` evidence notes.
3. Replace provider values with synthetic data. Fixture-hygiene tests enforce
   this.
4. Bump the parser or schema version when the meaning changed. Add a parser
   variant only when both provider shapes are still served.
5. Run the affected fixture tests and then the live smoke once.

## Weekly run

`.github/workflows/live-public.yml` runs the public matrix every Monday and
supports manual dispatch. It has no push or pull-request trigger, no secrets,
and a 15-minute timeout. The JUnit report is uploaded for 30 days so parser
drift can be compared over time.
