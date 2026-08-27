# Product and scope

## Name and audience

`finvizp` means **Finviz for Python**. The distribution and import package share
that name. The compact form mirrors `yfin` while deliberately avoiding the
legacy `finvizfinance` class contract.

The product is a public open-source PyPI package supporting Python 3.11-3.14.
The software is MIT licensed; Finviz data rights and entitlements remain the
caller's responsibility.

## Product statement

`finvizp` is a Finviz-only, async-first, Arrow-native client for the complete
verified public Finviz structured/image surface frozen on 2026-08-27. It
replaces every `finvizfinance` 1.4.0 capability, fixes current parser/transport
defects, and adds public features absent from that package.

It emphasizes:

- a small deep interface rather than legacy signature compatibility;
- immutable typed results instead of mixed dictionaries/DataFrames/files;
- explicit completeness, access, source, and temporal provenance;
- source-faithful typed normalization with raw evidence where lossy;
- robust drift detection and versioned Arrow schemas;
- responsible bounded access instead of bulk/site-wide crawling;
- enough observation metadata for caller-owned point-in-time history.

## Public 1.0 outcomes

A caller can:

1. fetch complete multi-symbol stock-page bundles, including all six snapshot
   tables and related ratings/news/insider/peer/ETF-holder relations;
2. fetch all six statement forms with currency, periods, raw values, and long
   deterministic Arrow output;
3. issue typed screen/group/signal queries with complete safe pagination,
   ordering, rank, query provenance, and deterministic custom-view schemas;
4. retrieve maps data, groups, ratings, global/ticker/publisher news metadata,
   ticker/global/fund/manager insider data, earnings screens, and economic
   calendar/detail data;
5. retrieve forex, crypto, current futures tile data, and public chart/spectrum
   artifact descriptors;
6. fetch the current canonical Finviz security-page universe in one manifest
   request and perform bounded symbol/company lookup through typed JSON
   suggestions;
7. distinguish complete, partial, recognized-empty, cached, stale, blocked,
   rate-limited, unentitled, and structurally broken responses;
8. configure explicit symbols, proxies, cache, pacing, retries, fixed browser
   profile, authenticated state, progress, and diagnostics;
9. accumulate provider-specific observations without claiming retroactive
   history or merging another provider.

## Frozen scope

The complete matrix lives in
[`public-surface-inventory.md`](../research/public-surface-inventory.md). Entirely new families discovered
after 2026-08-27 do not automatically block 1.0. Additive fields within existing
families follow schema-version policy.

Login/Elite-only options, portfolio, exports/APIs, complete ETF holdings,
alerts, correlated stocks, fundamental charts, and publishing remain deferred
until legitimately authenticated and verified. Public 1.0 has no stubs that
always fail and no guessed schemas.

Supporting the public surface means bounded caller-requested access, not a
sitemap-driven archive. The sole manifest exception is `symbols()`, which reads
the published stock sitemap once and never follows its entries. The package
paginates one explicit screen/feed but does not crawl ticker, publisher, fund,
manager, or calendar-detail pages. News support stops at Finviz metadata; maps
support is data, not rendering.

## `finvizfinance` parity

Parity means every legacy user outcome has a checked-in capability-manifest
entry mapped to a new interface, schema/artifact kind, documentation, and test.
CSV/Excel outcomes are documented through Arrow-native workflows rather than
core pandas/Excel exporters. A migration guide replaces a compatibility shim.

The machine-readable manifest—not narrative confidence—is the 1.0 gate.

## History boundary

- **Provider history:** fiscal periods and dated events supplied by Finviz.
- **Accumulated history:** repeated captures of current statistics, screens,
  ranks, groups, maps, and relationships starting with the first collection.
- **Derived history:** locally computed metrics outside `finvizp`.

The package provides timestamps, periods, hashes, schema/parser versions, and
provenance. It provides no Parquet/database writer, storage adapter, scheduler,
or cross-provider canonical model.

## Non-goals

- Yahoo or other-provider joins/fallbacks;
- canonical investment truth or regulatory source replacement;
- pre-collection snapshot reconstruction;
- database, Parquet, sink, scheduler, pipeline, or site-crawler functionality;
- third-party article-body scraping;
- browser/SPA automation, JavaScript execution, login, CAPTCHA, challenge, or
  entitlement bypass;
- proxy rotation after blocks/rate limits;
- pandas/legacy compatibility interface;
- arbitrary raw Finviz request interface;
- telemetry or unsolicited logging/printing.

## Source-selection policy

The client is structured-first, not blindly JSON-first. A first-party public
JSON/API, XHR/fetch response, or embedded JSON payload is preferred only when it
is same-tier, complete for the method, directly callable without browser state,
no more request-intensive, and no less snapshot-coherent than semantic HTML.
The capability manifest records the chosen representation and evidence.

This means statements and symbol search use public JSON, futures prefer embedded
JSON, and verified map APIs may be used. It also means public screener HTML is
not replaced by an Elite export and the coherent stock-page bundle is not
replaced by an incomplete quote XHR.

## Alternatives rejected

### Compatibility-first fork

Rejected because it preserves shallow classes, constructor I/O, global mutable
transport, expensive per-ticker signal probing, pandas coupling, and mixed
return types.

### Universal generic fetch facade

Rejected as the sole public interface because snapshots, period histories,
events, ranked queries, compound pages, and images have materially different
contracts.

### Capability-complete deep modules — chosen

Endpoint-family functions hide batching, pagination, caching, parsing,
normalization, and diagnostics behind immutable query/result models and a
reusable transport client.

## Release success criteria

- every frozen-public and legacy manifest entry is implemented/documented/tested;
- schemas are deterministic, versioned, and valid for recognized empty results;
- quote bundles parse the complete current page once;
- strict/partial, cache/stale, access, retry, and drift semantics are proven;
- fixtures are minimal/scrubbed and live tests are bounded/opt-in;
- Python 3.11-3.14 CI, pytest, Ruff, ty, builds, manifests, schemas, fixtures,
  and documentation checks pass;
- no Elite capability is claimed without legitimate authenticated evidence.
