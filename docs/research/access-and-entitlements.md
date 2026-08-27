# Access, entitlements, and responsible transport

## Scope

This document separates technical capability from permission. The software
license of `finvizfinance` does not grant rights to Finviz content, and a URL
being reachable does not establish a right to collect, retain, or redistribute
its output.

## Current official tier distinctions

The Finviz Elite page observed on 2026-08-27 states:

| Capability | Free | Elite |
|---|---|---|
| Quotes/charts/screener | delayed | real-time |
| Maps/groups | delayed | real-time |
| Extended hours | unavailable | premarket/after-hours |
| Screener rows | 20 in table view | up to 100 in table view |
| Export/API | unavailable | screener, portfolio, groups, options, news |
| Statements | 3 years | 8 years |
| Full ETF holdings/structural metrics | unavailable | available |
| Custom filters/stats view | unavailable | available |

Finviz also states that fundamentals are recalculated hourly and that market
coverage is NASDAQ, NYSE, and AMEX.

Source: <https://finviz.com/elite>

These terms can change. Runtime code should detect actual capability and return
a typed entitlement error rather than infer access from account configuration.
Cookies prove only authentication, not Elite entitlement. Result metadata uses
`PUBLIC`, `AUTHENTICATED`, `ELITE`, or `UNKNOWN` based on response evidence and
preserves displayed delay/as-of information instead of inventing it.

## Robots directives observed

`https://finviz.com/robots.txt` currently disallows several paths, including:

- export, group export, and portfolio export;
- chart/image variants;
- generic screener query URLs;
- insider searches;
- the v1 screener CSV export endpoint.

It explicitly allows a list of selected signal screener URLs. Robots directives
are not a complete contract or legal opinion, but they are an important
machine-readable access signal. Bulk collection design must account for them
and should prefer documented authenticated APIs where available.

The confirmed policy permits a direct caller-requested public query under
conservative pacing; it does not permit automatic crawling of disallowed routes,
sitemaps, all tickers, all publishers, or all filter/signal combinations.

## Client modes

### Public client

- No credentials.
- Delayed/public surfaces only.
- Conservative serial or low-rate requests by default.
- No assumptions about Elite-only page size, export, API, or depth.
- Typed `EntitlementError` if a response indicates login/subscription is
  required.
- No site-wide enumeration or third-party article fetching.

### Authenticated client

- Caller explicitly supplies cookies/session state or a configured transport.
- `finvizp` does not own credential persistence.
- Authentication state is pinned to one network route and cache scope.
- Explicit capability/tier metadata is attached to results.
- The client does not automate browser login, consent, CAPTCHA, payment, or
  challenge solving.
- The package never accepts account username/password or reads an automatic
  credential environment variable.

## Proxy policy

`fastreq` provides explicit proxy pools, health tracking, cooldown, and
route-isolated cookie jars. `finvizp` may expose these capabilities for caller
controlled routing and reliability.

Proxy precedence is:

```text
explicit client/per-call proxy
  > FINVIZP_PROXY
  > standard fastreq proxy environment
  > direct connection
```

`proxy=False` or `proxies=[]` forces direct access. Metadata contains only a
safe route fingerprint, never the proxy URL or credentials.

It must not:

- discover free proxies;
- rotate routes to evade an access limit;
- retry challenges indefinitely;
- claim that TLS impersonation grants permission;
- move authenticated cookies across routes without an explicit safe design;
- hide repeated 403/429 responses as empty tables.

Public routes may fail over only for transport failures. A 403, 429, challenge,
or entitlement response cools down/stops rather than switching identity.

A proxy changes the route, not the caller's entitlement. One fixed configurable
browser/TLS profile is pinned per client. Random identity rotation is forbidden.
The profile's non-sensitive identifier is part of request/cache metadata.

## Default pacing

The implementation plan must choose defaults from live bounded probes. Until
then, the design requires:

- low concurrency;
- a per-client token-bucket rate limit and concurrency bound;
- bounded retries only for transient transport failures and configured 429/5xx
  statuses;
- `Retry-After` support;
- no retry for parser drift, invalid query, or entitlement failures;
- a circuit breaker/cooldown after repeated challenge pages;
- caller overrides that remain subject to a documented responsible-use policy.

Yahoo's existing production rate settings must not be copied to Finviz.
Exact TTL, concurrency, request-rate, retry, and symbol-safety defaults are
selected from bounded implementation probes rather than guessed in the design.

## Cache policy

Ordinary requests use endpoint-specific transparent TTL caching:

- parsed immutable `FetchResult` values, not ordinary raw bodies;
- approximate byte-bounded in-memory LRU with optional entry cap;
- optional caller-supplied cache adapter, but no core disk implementation;
- isolation by query, auth/access, safe route, browser profile, parser/schema
  version, and representation;
- original fetch time/hash retained on a hit, with served-at/age/hit metadata;
- explicit disable, refresh, and stale-if-error controls;
- stale fallback disabled by default;
- per-client single-flight for identical concurrent misses.

Authenticated entries never cross auth/client scopes. Transparent cache storage
does not grant additional retention or redistribution rights.

## Raw response handling

Raw fixtures and explicit capture hooks can improve reproducibility, but they
may contain licensed content or authentication state. Requirements:

- never persist request cookies, authorization headers, proxy credentials, or
  full response headers containing secrets;
- SHA-256 hash ordinary response bytes before parsing, then discard them;
- make raw-body fixture capture opt-in;
- document retention and redistribution responsibilities;
- hash content for deduplication without exposing it;
- scrub fixtures and keep them minimal enough for parser tests;
- separate public fixtures from authenticated/Elite fixtures;
- commit only minimal scrubbed structural regions, never full account pages,
  portfolio data, tracking identifiers, ads, or unnecessary article content.

## Failure behavior

At minimum the client should distinguish:

- `FinvizTransportError`
- `FinvizRateLimitError` with retry-after metadata
- `FinvizBlockedError` for challenge/wall responses
- `FinvizEntitlementError`
- `FinvizNotFoundError`
- `FinvizQueryError`
- `FinvizPartialError` / `FinvizBatchError`
- `FinvizParseError` with endpoint and structural fingerprint
- `FinvizDataError` for contradictory row widths, invalid dates, or units

No access failure should be represented as a valid empty table unless the
endpoint contract explicitly says “no results.”

Warnings/errors are immutable structured records with stable codes and safe
endpoint/symbol/page context. They never include cookie values, authorization,
proxy URLs/credentials, or sensitive raw bodies. The package emits no
unsolicited logs/prints and no telemetry; callers may supply typed event and
progress callbacks.

## Pre-implementation gate

Before a production collector or authenticated module is planned:

1. identify the exact endpoint and tier;
2. review current official access documentation and terms;
3. decide whether collection is personal, internal, or redistributed;
4. establish permitted rate and retention;
5. record test credentials/session handling without storing secrets;
6. define a small live smoke that proves access without bulk collection.
