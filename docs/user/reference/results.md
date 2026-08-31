# Results and errors

Networked operations return an immutable `FetchResult[T]`. The payload lives in
`.data`:

- endpoint operations return an Arrow table;
- `quote()` returns a `QuoteBundle`;
- artifact downloads return an `Artifact` with content metadata.

Use `.table` or `.artifact` when you know which payload you received. The wrong
view raises `FinvizDataError` instead of returning a misleading value.

## Status values

| Status | Meaning |
|---|---|
| `COMPLETE` | Every requested unit succeeded. |
| `PARTIAL` | Some units failed. Returned only with `allow_partial=True`. |
| `EMPTY` | The provider answered successfully with recognized empty data. |

An empty result is not an error. Strict mode raises `FinvizPartialError` for a
mixed batch, but preserves the successful prefix in `partial_result`.

## Provenance

`result.metadata` is frozen and records how to interpret the payload.

| Field | What it tells you |
|---|---|
| `endpoint`, `query` | What the client requested. |
| `response_hash`, `route_fingerprint` | Which response and route produced the data. |
| `fetched_at` | When the provider response was fetched. |
| `served_at`, `cache_hit`, `cache_age` | How this call was served from the cache. |
| `access_tier` | Observed `PUBLIC`, `AUTHENTICATED`, `ELITE`, or `UNKNOWN`. |
| `symbols` | Requested spelling, canonical symbol, and input position. |
| `warnings`, `unit_errors` | Typed, scrubbed diagnostics. |
| `parser_version`, `schema_version` | Code and schema versions that produced the table. |
| `projected_from` | `"quote"` when a relation came from a cached quote bundle. |

Metadata never contains credentials, proxy URLs, or response bodies. Context
formatting redacts sensitive values recursively.

## Batches

Batch calls validate, normalize, deduplicate, and order symbols before network
access. They also enforce the operation's maximum batch size.

| Mode | Behavior |
|---|---|
| Strict, default | Raises `FinvizPartialError` at the first failed unit. Successful rows remain in `partial_result`. |
| `allow_partial=True` | Returns `PARTIAL`; inspect `metadata.unit_errors` for failed units. |
| All units fail | Raises `FinvizBatchError` with one `UnitError` per unit. |
| Symbol/resource does not resolve | Records a typed `FinvizNotFoundError`, never a silent drop. |

## Error hierarchy

Every public error derives from `FinvizError`.

| Exception | Use it for |
|---|---|
| `FinvizTransportError` | Network failures and timeouts. |
| `FinvizRateLimitError` | HTTP 429 with parsed `Retry-After`. |
| `FinvizBlockedError` | HTTP 403 or a challenge page. |
| `FinvizEntitlementError` | Login or Elite content walls. |
| `FinvizNotFoundError` | A symbol or resource does not resolve. |
| `FinvizQueryError` | Invalid caller input. Also a `ValueError`. |
| `FinvizPartialError` | Strict batch mode with at least one failed unit. |
| `FinvizBatchError` | Every batch unit failed. |
| `FinvizParseError` | The provider's response shape drifted. |
| `FinvizDataError` | A conversion, schema, or lookup contract failed. |

Access failures and parser drift stay separate. A live smoke can therefore
report “the route is blocked” without mislabeling it as a parser regression.
