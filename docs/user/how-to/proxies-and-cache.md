# Configure proxies and caching

`FinvizClient` uses conservative defaults. Treat them as politeness limits,
not throughput targets.

## Defaults

| Setting | Default |
|---|---:|
| `cache_ttl` | `None` |
| `rate_limit` | `None` |
| `retry_attempts` | `2` |
| `retry_backoff` | `1.0s` exponential |
| in-flight concurrency | `6` |
| quote batch size | `32` symbols |
| statement batch size | `50` symbols |

Retries apply only to transient transport, 5xx, and 429 responses. The client
does not retry query, parse, entitlement, challenge, or strict one-request
manifest failures.

## Rate-limit circuit breaker

After 3 consecutive 429 responses, the client stops sending requests: each
further call raises `CircuitOpenError` (a `FinvizRateLimitError` subclass)
immediately, without touching the network, until the cooldown elapses. The
cooldown is the provider's `Retry-After` value when present, otherwise 60
seconds. The first request after the deadline is a probe: any non-429 outcome
closes the circuit, another 429 re-opens it. Blocked (403) and entitlement
failures never count toward the circuit.

```python
import finvizp

try:
    result = await finvizp.quote_async("AAPL", client=client)
except finvizp.CircuitOpenError as exc:
    wait = exc.context.get("retry_after")  # seconds to back off
```

## Set a proxy

Precedence is explicit arguments, `FINVIZP_PROXY`, standard fastreq resolution,
then direct access. `proxy=False` or `proxies=[]` disables discovery.

```python
from finvizp import FinvizClient

client = FinvizClient(proxies=["http://proxy.example:8080"])
```

The client never prints proxy URLs. It records only a safe proxy seed hash in
metadata. A 403, 429, challenge, or entitlement failure does not trigger proxy
failover.

## Enable caching

Caching is per client and stores parsed immutable results, not raw bodies.
Cache keys include route, query, access/auth scope, proxy seed, representation,
parser version, and schema version.

```python
from finvizp import FinvizClient, symbols_async

client = FinvizClient(cache_ttl=600.0)

first = await symbols_async(client=client)
second = await symbols_async(client=client)
forced = await symbols_async(client=client, refresh=True)
bypassed = await symbols_async(client=client, cache=False)

client.cache.clear()
print(client.cache.stats())
```

Concurrent identical misses use single-flight coalescing: one request serves all
waiting callers.

Cache hits preserve `fetched_at` and `response_hash`. They update
`served_at`, `cache_hit`, and `cache_age` instead.

!!! note
    `stale_if_error=True` may serve an expired entry after an eligible
    transport failure. It never hides query, parse, entitlement, or challenge
    errors.

## Keep your own snapshots

`finvizp` has no persistence layer. Store the table with the provenance fields
you need, such as `fetched_at`, `response_hash`, `schema_version`, and
`access_tier`. See [caller-owned history](caller-owned-history.md).
