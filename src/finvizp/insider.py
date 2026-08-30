"""Public insider feed families: global event feed and fund/manager disclosures.

Three explicit, bounded public feeds (frozen inventory: ``insider.global`` and
``insider.fund_manager``):

- :func:`global_async` reads one window of the ``/insidertrading.ashx`` Form 4
  event table (typed variant feeds: latest / top week / top 10% owner, buys,
  sales, or all transactions). The provider serves one window per request and
  repeats identical rows on its ``b=N`` continuation links (live evidence
  2026-08-30), so this operation never paginates: one request, no repeated
  state. Rows normalize into the registered ``quote_insider`` contract —
  the same columns the ticker insider projection produces.
- :func:`fund_async` / :func:`manager_async` require one explicit slug each
  and return the page's embedded first-party portfolio JSON (N-PORT fund or
  13F manager quarterly disclosure: filer identity, latest-quarter summary,
  top buy/sell observations, allocation history, report dates). These are
  quarterly relationship disclosures — never Form-4 events — and they never
  enumerate or search the underlying sitemap families.
"""

from __future__ import annotations

import re
from typing import Any

from finvizp._parsers import insider as insider_parser
from finvizp._sync import run_sync
from finvizp.arrow import build_table
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import FinvizQueryError
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

__all__ = [
    "INSIDER_FEEDS",
    "fund_async",
    "fund_insider",
    "global_async",
    "global_insider",
    "manager_async",
    "manager_insider",
]

_FEED_PATH = "/insidertrading.ashx"
_FUND_PATH = "/insidertrading/funds"
_MANAGER_PATH = "/insidertrading/managers"
_PARSER_VERSION = "1"
_SCHEMA_VERSION = 1  # quote_insider dataset version

# Reviewed variant registry: feed name -> exact provider query parameters
# (live evidence 2026-08-30; each variant's page title verified).
INSIDER_FEEDS: dict[str, dict[str, str]] = {
    "latest": {},
    "latest_buys": {"tc": "1"},
    "latest_sales": {"tc": "2"},
    "top_week": {"or": "-10", "tv": "100000", "tc": "7", "o": "-transactionValue"},
    "top_week_buys": {"or": "-10", "tv": "100000", "tc": "1", "o": "-transactionValue"},
    "top_week_sales": {"or": "-10", "tv": "100000", "tc": "2", "o": "-transactionValue"},
    "top_owner_trade": {"or": "10", "tv": "1000000", "tc": "7", "o": "-transactionValue"},
    "top_owner_buys": {"or": "10", "tv": "1000000", "tc": "1", "o": "-transactionValue"},
    "top_owner_sales": {"or": "10", "tv": "1000000", "tc": "2", "o": "-transactionValue"},
}

# Slug grammar: the provider's own sitemap slugs are lowercase alphanumerics
# joined by dashes (``na-0000002230``, ``kingdon-capital-management-llc-1000097``).
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_MAX_SLUG_LENGTH = 128


def _validate_feed(feed: str) -> dict[str, str]:
    if feed not in INSIDER_FEEDS:
        valid = ", ".join(sorted(INSIDER_FEEDS))
        msg = f"insider feed must be one of {valid}, got {feed!r}"
        raise FinvizQueryError(msg)
    return INSIDER_FEEDS[feed]


def _validate_slug(kind: str, slug: str) -> str:
    if (
        not isinstance(slug, str)
        or not slug
        or len(slug) > _MAX_SLUG_LENGTH
        or not _SLUG.match(slug)
    ):
        msg = (
            f"{kind} slug must be a lowercase alphanumeric dash identifier "
            f"(provider sitemap form), got {slug!r}"
        )
        raise FinvizQueryError(msg)
    return slug


def _parse_global(response: ClientResponse, *, strict_schema: bool = False) -> FetchResult[Any]:
    """Reviewed endpoint parser: classified envelope -> immutable FetchResult."""
    warnings: list[Any] = []
    rows = insider_parser.parse_insider_table(response.data)
    table = build_table(
        "quote_insider",
        rows,
        fetched_at=response.fetched_at,
        strict_schema=strict_schema,
        on_warning=warnings.append,
    )
    return FetchResult(
        table,
        ResultMetadata(
            endpoint=_FEED_PATH,
            status=ResultStatus.COMPLETE,
            access_tier=response.access_tier,
            fetched_at=response.fetched_at,
            query=dict(response.query),
            warnings=tuple(warnings),
            attempts=response.attempts,
            response_hash=response.response_hash,
            route_fingerprint=response.route_fingerprint,
        ),
    )


def _parse_disclosure(kind: str, parse_rows: Any) -> Any:
    """Build the parser closure for one fund/manager disclosure route."""

    def parse(response: ClientResponse) -> FetchResult[Any]:
        # FetchResult freezes the mapping (MappingProxyType / tuples), so the
        # disclosed payload is immutable exactly as served.
        return FetchResult(
            parse_rows(response.data),
            ResultMetadata(
                endpoint=f"{_FUND_PATH if kind == 'fund' else _MANAGER_PATH}/<slug>",
                status=ResultStatus.COMPLETE,
                access_tier=response.access_tier,
                fetched_at=response.fetched_at,
                query=dict(response.query),
                attempts=response.attempts,
                response_hash=response.response_hash,
                route_fingerprint=response.route_fingerprint,
            ),
        )

    return parse


async def _disclosure_async(
    kind: str,
    slug: str,
    *,
    client: FinvizClient,
    refresh: bool,
    cache: bool,
) -> FetchResult[Any]:
    _validate_slug(kind, slug)
    base = _FUND_PATH if kind == "fund" else _MANAGER_PATH
    op = client._endpoint_op(
        f"{base}/{slug}",
        query={},
        cache=cache,
        refresh=refresh,
        representation=f"insider-{kind}",
        parser_version=_PARSER_VERSION,
        schema_version=_SCHEMA_VERSION,
        parse=_parse_disclosure(
            kind,
            insider_parser.parse_fund_page if kind == "fund" else insider_parser.parse_manager_page,
        ),
    )
    return await op()


async def global_async(
    *,
    feed: str = "latest",
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
    strict_schema: bool = False,
) -> FetchResult[Any]:
    """Read one window of the global insider Form 4 event table.

    Args:
        feed: one of the nine reviewed variants in :data:`INSIDER_FEEDS`
            (latest / top week / top 10% owner x all / buys / sales).
        client: reusable client.
        refresh: bypass the cache and replace any cached entry.
        cache: ``False`` bypasses the client cache for this call.
        strict_schema: promote recoverable conversion drift to typed errors.

    One request, one window: the provider repeats identical rows on its
    ``b=N`` continuation links (live evidence 2026-08-30), so there is no
    pagination walk and no repeated-state duplication. Rows normalize into
    the registered ``quote_insider`` columns.
    """
    params = _validate_feed(feed)
    op = client._endpoint_op(
        _FEED_PATH,
        query=params,
        cache=cache,
        refresh=refresh,
        representation="insider-global+strict" if strict_schema else "insider-global",
        parser_version=_PARSER_VERSION,
        schema_version=_SCHEMA_VERSION,
        parse=lambda response: _parse_global(response, strict_schema=strict_schema),
    )
    return await op()


async def fund_async(
    slug: str,
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Read one explicitly named N-PORT fund disclosure page.

    ``slug`` is the provider's own identifier form (``na-0000002230``);
    anything else fails before any network access. The result data is the
    page's embedded JSON verbatim (``details`` plus ``report_dates``): filer
    identity, latest-quarter summary, top buy/sell observations, allocation
    history. Unknown slugs surface the provider's typed not-found.
    """
    return await _disclosure_async("fund", slug, client=client, refresh=refresh, cache=cache)


async def manager_async(
    slug: str,
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Read one explicitly named 13F manager disclosure page.

    Same contract as :func:`fund_async` with the manager route and sector
    allocations; ``slug`` is the provider's sitemap form.
    """
    return await _disclosure_async("manager", slug, client=client, refresh=refresh, cache=cache)


def global_insider(
    *,
    feed: str = "latest",
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
    strict_schema: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`global_async`; rejects an active event loop."""
    return run_sync(
        global_async(
            feed=feed,
            client=client,
            refresh=refresh,
            cache=cache,
            strict_schema=strict_schema,
        )
    )


def fund_insider(
    slug: str, *, client: FinvizClient, refresh: bool = False, cache: bool = True
) -> FetchResult[Any]:
    """Sync wrapper for :func:`fund_async`; rejects an active event loop."""
    return run_sync(fund_async(slug, client=client, refresh=refresh, cache=cache))


def manager_insider(
    slug: str, *, client: FinvizClient, refresh: bool = False, cache: bool = True
) -> FetchResult[Any]:
    """Sync wrapper for :func:`manager_async`; rejects an active event loop."""
    return run_sync(manager_async(slug, client=client, refresh=refresh, cache=cache))
