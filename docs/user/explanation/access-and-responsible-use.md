# Access and responsible use

`finvizp` reads the public Finviz surface with bounded, explicit requests. This
page describes what the client does and what remains your responsibility.

For endpoint details, see the
[access and entitlements research](https://github.com/legout/finvizp/blob/main/docs/research/access-and-entitlements.md).
For request settings, see [proxies and cache](../how-to/proxies-and-cache.md).

## Access tiers

| Tier | Meaning | Client behavior |
|---|---|---|
| `PUBLIC` | Anonymous browser-visible HTML or first-party JSON. | Implemented public operations may use it. |
| `AUTHENTICATED` | Requires a logged-in session. | No login flow or automation. |
| `ELITE` | Requires a paid subscription. | A wall raises `FinvizEntitlementError`; export routes are not called. |
| `UNKNOWN` | The response does not establish entitlement. | The client does not guess. |

Cookies prove authentication, not Elite entitlement. The library does not store,
transmit, or fabricate credentials.

## What the client does

- Makes one request per logical operation. Related links, badges, and article
  URLs are data, not follow-up work.
- Retries transient transport, 5xx, and 429 failures within bounded limits.
- Stops on 403, challenge, and entitlement responses. It does not switch
  proxies or identities to evade a wall.
- Redacts proxy URLs, authorization headers, cookies, and response bodies from
  metadata and error context.
- Avoids sitemap enumeration and filter-grid exhaustion.

## What you must decide

The client cannot decide whether your use is permitted. You are responsible for:

- the purpose of access and any terms, licences, attribution, or redistribution
  requirements;
- the rate and volume of your own loops;
- retention and privacy for snapshots you persist;
- verifying entitlement for anything beyond the anonymous public surface.

The defaults are intended to be polite. They are not a legal opinion or a
throughput guarantee.

## Hard limits

`finvizp` does not provide:

- login automation or session farming;
- sitemap, ticker, publisher, fund, manager, or filter-grid crawls;
- challenge, entitlement, or robots bypasses;
- usage telemetry or a phone-home path.
