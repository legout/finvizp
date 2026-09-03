# stock-screener (xang1234) learnings for finvizp

Full analysis: ~/projects/marketdata-screens/research/stock-screener-analysis.md (2026-09-02).

## Adopt (evidence paths under backend/app/)

1. **curl_cffi Chrome-impersonation session** — built once per process, injected into every yfinance/provider call so Yahoo doesn't fingerprint-block. `services/yf_session.py:71-85`, wired in `bulk_data_fetcher.py:57-72`. Validates our fastreq choice — same trick applies to any provider client.
2. **Per-(provider, market) circuit breaker** — closed/open/half-open in Redis; trips after 3 consecutive 429s; per-market cooldown; half-open single probe via SET NX; `CircuitOpenError` SUBCLASSES the rate-limit error so call sites degrade through existing handling. `services/provider_circuit_breaker.py` (401 LOC). Right size for a client lib: state machine + one exception hierarchy. **ADOPTED for finvizp (client-local, no Redis): deadline-as-probe-gate in `client.py`, trips after 3 consecutive 429s, `CircuitOpenError(FinvizRateLimitError)`, Retry-After cooldown / 60s default, any non-429 closes.**
3. **Rate-budget splitting by universe weight** — divide each provider's global budget across markets proportional to universe size, weekly weight refresh, per-market env overrides, 14-day stale-weights safety recompute. `services/rate_budget_policy.py:146-241`. Pattern for budget-aware client defaults.
4. **Provider plan registry** — per market×dataset ordered `(provider, batch_size, fallback)` tuples with provenance recorded downstream; callers never embed provider branching. `domain/providers/data_plan.py`.

## Avoid (their documented anti-patterns)

- **Error classification by message substrings** — `is_transient_database_error` and 429 detection string-match provider error text (`tasks/transient_database.py:14-36`, `services/price_fetch_failures.py`). finvizp classifies on response metadata/status codes by design — keep that.
- Single concurrency-1 worker as the rate-limit strategy — the Lua limiter + breaker is the right tool; the big lock caps throughput by design.
