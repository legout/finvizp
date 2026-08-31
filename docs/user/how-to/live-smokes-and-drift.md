# Live smokes and drift (how-to)

The default test suite is hermetic: no network, fixtures only. The live
smokes under `tests/live/` are the opt-in complement — one bounded
sequential request per public family, proving that current access and
response shape still match the verified structure the fixtures encode.

## Running the smokes

```bash
uv run pytest -q tests/live -m live_public          # public families
uv run pytest -q tests/live -m live_elite            # authenticated Elite (legitimate session state required)
```

Plain `pytest` (and therefore CI) always skips them: `tests/live/conftest.py`
deselects the directory unless the `live_public` marker expression is given.

Each smoke makes exactly one request (or one fixed small batch where the
operation is batched by contract) against a canonical endpoint with one
explicit, stable argument — `AAPL`, one screener page, one publisher slug.
No sitemap, ticker, publisher, fund, manager, or release enumeration; no
retries beyond the client's own bounded transport policy; no rate escalation.
Failures classify through the shared helper `tests/live/_smoke.py`.

## Failure categories

A red live run is triaged by bucket, never collapsed into one undifferentiated
failure:

| Category | Class | Meaning | Action |
| --- | --- | --- | --- |
| network/access | `FinvizTransportError`, `FinvizRateLimitError` (base `FinvizError`) | Transport, 5xx, rate limiting, local connectivity | Skip — expected variability; retry later |
| block/challenge/entitlement | `FinvizBlockedError`, `FinvizEntitlementError` | 403, challenge page, Elite wall | Skip — never bypassed; investigate only if the surface was legitimately public |
| parser drift | `FinvizParseError` | Provider markup/embedded-JSON shape changed | Red (unless the smoke marks `skip_drift`); route for review, refresh fixtures |
| data conversion | `FinvizDataError` | Typed unit normalization broke on a valid payload | Red; fix the converter, add a fixture row |
| assertion | any other exception | The smoke contract itself broke | Red; fix the smoke |

## When drift is confirmed

1. Capture the current structure with one bounded probe (never a crawl).
2. Update the structural fixture and its `_build.py` evidence notes; scrub all
   provider values to synthetic data (the fixture-hygiene tests added by the
   0.5-B hardening card enforce this).
3. Bump the parser/schema version the module registers if the shape change is
   semantic, and add a variant only if the provider serves both shapes.
4. Re-run the affected fixture tests, then the live smoke once.

## The weekly workflow

`.github/workflows/live-public.yml` runs the full `live_public` matrix every
Monday on a schedule (plus manual dispatch). It is deliberately inert for
normal development: `contents: read` permissions, no `pull_request` or `push`
trigger, a `live-public` concurrency group, a 15-minute timeout, and no
secrets — the smokes hit only anonymous public endpoints. The JUnit report is
uploaded as a workflow artifact (30-day retention) for drift timelines. No
telemetry leaves the run: request metadata stays in the report, and proxy or
cookie configuration is intentionally absent from the workflow environment.
