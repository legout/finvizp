# Results, provenance, and error handling (0.1)

Every finvizp network operation returns an immutable `FetchResult[T]`
envelope. `.data` is the canonical payload — an Arrow table for endpoint
operations, a `QuoteBundle` for `quote()` — and `.table`/`.artifact` are
typed convenience views that raise `FinvizDataError` on the wrong payload
kind.

## Result status

`ResultStatus` has exactly three values:

- `COMPLETE` — every requested unit succeeded.
- `PARTIAL` — some units failed; only produced with `allow_partial=True`
  (strict mode raises `FinvizPartialError` carrying the immutable partial
  result instead).
- `EMPTY` — the provider answered successfully with recognized empty data
  (for example a symbol with no statement rows). Empty is a result, not an
  error.

## Provenance

`FetchResult.metadata` (`ResultMetadata`) is frozen and carries everything
needed to interpret the data later:

- `endpoint`, `query`, `response_hash`, `route_fingerprint` — what was asked
  and how the response is identified;
- `fetched_at` (provider fetch time) and `served_at`/`cache_hit`/`cache_age`
  (cache facts; cache hits keep the original `fetched_at` and hash);
- `access_tier` (`AccessTier`: `PUBLIC`, `AUTHENTICATED`, `ELITE`,
  `UNKNOWN`) — inferred from response evidence, never guessed;
- `symbols` — per-symbol resolution records preserving the requested
  spelling, canonical form, and position;
- `warnings` (`FetchWarning` with a stable `code`) and `unit_errors`
  (`UnitError`) — typed, scrubbed diagnostics;
- `parser_version`/`schema_version` — which parser and registered Arrow
  schema produced the table;
- `projected_from` — `"quote"` on projection results derived from a cached
  bundle.

Nothing in metadata ever contains credentials, proxy URLs, or response
bodies; context formatting redacts them recursively.

## Batching: strict, partial, and empty

Batch operations (`statements_batch`, `quote`, projections) validate and
normalize symbols before any network access, deduplicate, keep
first-canonical order, and enforce a bounded batch size:

- strict (default): the first failed unit raises `FinvizPartialError` with
  the successful prefix preserved in `partial_result`;
- `allow_partial=True`: the call returns a `PARTIAL` result whose
  `metadata.unit_errors` says which symbols failed and why;
- all units failed: `FinvizBatchError` carries one `UnitError` per symbol;
- a symbol that does not resolve to data is a typed per-unit error
  (`FinvizNotFoundError` class), never a crash and never a silent drop.

## Errors

All failures derive from `FinvizError`. The hierarchy distinguishes exactly:

| Exception | Meaning |
|---|---|
| `FinvizTransportError` | network/transport failure, timeouts included |
| `FinvizRateLimitError` | 429 with parsed `Retry-After` |
| `FinvizBlockedError` | 403/challenge wall |
| `FinvizEntitlementError` | login/Elite redirect on entitled content |
| `FinvizNotFoundError` | symbol or resource does not resolve |
| `FinvizQueryError` | invalid caller input (also a `ValueError`) |
| `FinvizPartialError` | strict-mode batch with failures; carries partial result |
| `FinvizBatchError` | all batch units failed |
| `FinvizParseError` | provider shape drift — the response is not what was verified |
| `FinvizDataError` | contract violation in data construction or lookup |

Parse drift and access problems are deliberately different exceptions: a
live smoke can classify an access/network failure separately from schema
drift.
