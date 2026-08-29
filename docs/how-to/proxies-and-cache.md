# Proxies, caching, and caller-owned persistence (how-to)

## Transport defaults

`FinvizClient` ships deliberately conservative provisional defaults
(mirrored in `finvizp.provisional_defaults()` and asserted by the
capability tests so they cannot drift from the docs):

- **No TTL caching** (`cache_ttl=None`) and **no rate limit**
  (`rate_limit=None`) until measured live evidence justifies real values;
- bounded retries (`retry_attempts=2`, `retry_backoff=1.0s` exponential)
  for transient transport/5xx/429 only — never for query, parse,
  entitlement, challenge, or the strict one-request symbols manifest;
- bounded concurrency (6 in-flight) and bounded batch sizes
  (`max_symbols=32` for quote batches, 50 for statement batches).

Treat these as ceilings for polite access, not throughput targets. Raise a
value only when your own measurements say you must.

## Proxies

Precedence: explicit `proxy=`/`proxies=` argument > `FINVIZP_PROXY`
environment variable > standard fastreq resolution > direct. `proxy=False`
or `proxies=[]` disables all discovery and forces direct.

```python
from finvizp import FinvizClient

client = FinvizClient(proxies=["http://proxy.example:8080"])
```

Proxy URLs are never echoed: request metadata records a safe proxy seed
hash, never the URL, and error contexts redact them recursively. No proxy
failover happens after 403/429/challenge/entitlement — switching exits to
evade an access wall is out of scope for this library.

## Caching

Caching is opt-in per client and caches only parsed immutable
`FetchResult` values — never raw bodies. Keys isolate by route, query,
access/auth scope, proxy seed, browser profile, representation, parser
version, and schema version.

```python
client = FinvizClient(cache_ttl=600.0)  # 10-minute TTL

result = await symbols_async(client=client)  # cached after first call
result = await symbols_async(client=client, refresh=True)  # forced miss
result = await symbols_async(client=client, cache=False)  # bypass entirely

client.cache.clear()  # explicit invalidation
client.cache.stats()  # hits/misses/entries
```

Identical concurrent misses are coalesced (single-flight): one underlying
request, all callers receive the same result. Cache hits preserve the
original `fetched_at` and `response_hash` and update `served_at`,
`cache_hit`, and `cache_age` so provenance stays truthful.
`stale_if_error=True` (opt-in) may serve an expired entry after an eligible
transport failure; it never masks typed query/parse/entitlement/challenge
errors.

## Caller-owned persistence

finvizp does not ship a persistence layer. To build point-in-time history,
capture the `FetchResult` yourself: store `.table` (e.g.
`table.to_pylist()` or Arrow IPC files) together with
`result.metadata` fields — `fetched_at`, `response_hash`,
`schema_version`, `access_tier` — so each row keeps its snapshot
provenance. Repeated captures accumulate history; the provider surface
itself is snapshot-only.
