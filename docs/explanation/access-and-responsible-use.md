# Access and responsible use (explanation)

finvizp exists to read the **public** Finviz surface politely. This page
explains the access model and the use limits the library enforces — and the
ones it deliberately does not enforce because they are the caller's
responsibility. For endpoint-level detail see
[Access and entitlements](../research/access-and-entitlements.md); for the
transport knobs see
[Proxies and cache](../how-to/proxies-and-cache.md) and
[Caller-owned history](../how-to/caller-owned-history.md).

## Tiers

| Tier | Meaning | finvizp behavior |
| --- | --- | --- |
| PUBLIC | Anonymous browser-visible pages and first-party JSON | Fully covered by every operation marked `implemented` |
| AUTHENTICATED | Requires a logged-in session | Never automated; no login flow exists in the library |
| ELITE | Requires a paid subscription | Typed `FinvizEntitlementError` when the provider walls a route; export endpoints are simply not called |

Cookies prove authentication, not Elite entitlement. The library never
stores, transmits, or fabricates credentials, and result metadata records
only the observed `access_tier`.

## What the client enforces

- **One request per logical operation.** Parsers never trigger follow-up
  fetches; related links, badges, and article URLs are data, not work.
- **Bounded retries** (2, exponential backoff) for transient transport/5xx/429
  only — never for query, parse, entitlement, or challenge errors.
- **No failover after an access wall.** A 403, challenge, or entitlement
  error ends the attempt; switching proxies or identities to evade it is out
  of scope, permanently.
- **Redaction everywhere.** Proxy URLs, authorization headers, cookies, and
  response bodies are redacted recursively in every error context and
  metadata record.
- **Robots awareness.** Routes the provider's `robots.txt` disallows for
  automation (exports, chart/image variants, insider searches) are either not
  implemented or strictly opt-in single reads, never crawled.

## What stays the caller's responsibility

The library cannot make collecting legal, licensed, or polite — it can only
make politeness the default. Callers own:

- the **purpose** of their access and any terms-of-service obligations on
  their use of the data (redistribution, commercial use, attribution);
- the **rate and volume** of their own loops; the client's bounded defaults
  are ceilings for polite access, not throughput targets;
- **retention and privacy** for whatever they persist; snapshots you build
  are your data and your liability (see
  [Caller-owned history](../how-to/caller-owned-history.md));
- **verification of entitlement** for anything beyond the anonymous public
  surface, per deployment.

## Hard limits (non-negotiable in this library)

- No login automation, credential handling, or session farming.
- No crawling: no sitemap enumeration, ticker sweeps, publisher/fund/manager
  index walks, or filter-grid exhaustion. One explicit request per explicit
  caller choice, always.
- No bypass of challenges, entitlement walls, or robots directives.
- No telemetry: nothing about your usage, queries, or environment leaves the
  process. There is no phone-home path in the code.
