"""Quote operations: cached one-fetch bundles and cache-preserving projections.

Endpoint module in the foundation architecture: it owns route construction,
canonical symbols, batching, strict/partial semantics, and bundle projection,
while ``FinvizClient`` owns transport, cache, and single-flight. There are no
pass-through request methods on the client and no independent relation fetches:
every projection derives from the same cached stock-page bundle, so one page
fetch serves snapshot, ratings, news, insider, peers, and ETF holders.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

from finvizp._parsers.quote import parse_quote_page
from finvizp._symbols import SymbolInputError, SymbolResolutionRecord, _resolve
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import (
    FinvizNotFoundError,
    FinvizParseError,
    FinvizPartialError,
    FinvizQueryError,
    UnitError,
)
from finvizp.models import QuoteBundle
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

__all__ = [
    "etf_holders",
    "etf_holders_async",
    "insider",
    "insider_async",
    "news",
    "news_async",
    "peers",
    "peers_async",
    "quote",
    "quote_async",
    "ratings",
    "ratings_async",
    "snapshot",
    "snapshot_async",
]

CANONICAL_PATH = "/stock"
FALLBACK_PATH = "/quote.ashx"

# Reviewed conservative ceiling: quote batches are caller-requested bounded
# access, not crawling. Exact empirical default comes from the 0.1 probe card.
DEFAULT_MAX_SYMBOLS = 32

# Typed verdicts that say "this symbol does not resolve to a stock page" and
# justify the documented fallback route probe; every other failure type
# (rate limit, blocked, transport, entitlement) fails the unit as-is.
_NOT_FOUND_LIKE = (FinvizNotFoundError, FinvizParseError)


def _parser_version() -> str:
    from finvizp import schemas

    return str(schemas.dataset_version("quote_snapshot"))


def _parse_stock_page(response: ClientResponse) -> FetchResult[QuoteBundle]:
    """Reviewed quote-page parser: classified envelope -> immutable bundle result.

    News time-only displays anchor to the provider response's own date: the
    provenance timestamp's US-Eastern day, computed here so the parser keeps
    its plain ``dt.date`` contract.
    """
    bundle = parse_quote_page(
        response.data,
        fetched_at=response.fetched_at,
        response_date=response.fetched_at.astimezone(ZoneInfo("America/New_York")).date(),
    )
    return FetchResult(
        bundle,
        ResultMetadata(
            endpoint=response.endpoint,
            status=bundle.status,
            access_tier=response.access_tier,
            fetched_at=response.fetched_at,
            query=dict(response.query),
            attempts=response.attempts,
            response_hash=response.response_hash,
            route_fingerprint=response.route_fingerprint,
        ),
    )


async def _fetch_uncached_bundle(
    client: FinvizClient, symbol: str, parser_version: str, schema_version: int
) -> FetchResult[QuoteBundle]:
    """Canonical route with the documented fallback; no route in the cache key.

    The caller (:func:`_fetch_bundle`) applies route-keyed caching itself, so
    the resolved route stays an output fact rather than a cache-key input.
    """
    facets = _bundle_facets(parser_version, schema_version)
    try:
        return await client._endpoint_op(CANONICAL_PATH, query={"t": symbol}, **facets)()
    except _NOT_FOUND_LIKE:
        return await client._endpoint_op(FALLBACK_PATH, query={"t": symbol}, **facets)()


_BUNDLE_ROUTES: WeakKeyDictionary[FinvizClient, dict[str, str]] = WeakKeyDictionary()


def _bundle_facets(parser_version: str, schema_version: int) -> dict[str, Any]:
    return {
        "representation": "bundle",
        "parser_version": parser_version,
        "schema_version": schema_version,
        "parse": _parse_stock_page,
    }


async def _fetch_bundle(
    client: FinvizClient, symbol: str, parser_version: str, schema_version: int
) -> FetchResult[QuoteBundle]:
    """One symbol -> one cached bundle, route resolved once per client.

    ``FinvizClient`` keys its cache by endpoint path, so this logical
    operation caches under a stable route-free key and remembers the route
    that served each symbol: a warm call replays the cached bundle with zero
    HTTP requests, even when it originally came from the fallback route.
    Single-flight still coalesces concurrent misses on the shared key.
    """
    routes = _BUNDLE_ROUTES.setdefault(client, {})
    route = routes.get(symbol)
    if route is None:
        result = await _fetch_uncached_bundle(client, symbol, parser_version, schema_version)
        routes[symbol] = result.metadata.endpoint
        return result
    facets = _bundle_facets(parser_version, schema_version)
    return await client._endpoint_op(route, query={"t": symbol}, **facets)()


async def quote_async(
    symbols: str | Iterable[str],
    *,
    client: FinvizClient,
    allow_partial: bool = False,
    max_symbols: int | None = None,
) -> FetchResult[list[QuoteBundle]]:
    """Fetch one coherent stock page per unique symbol into quote bundles.

    Canonical ``/stock?t=`` route with the documented ``/quote.ashx?t=``
    fallback only for recognized not-found/route drift. Symbols normalize
    (``brk.b`` -> ``BRK-B``), dedupe in first-occurrence order, and fetch
    bounded-concurrent on the client's transport. Each page is fetched and
    parsed once into a cached :class:`QuoteBundle`.

    Strict mode (default) raises :class:`FinvizPartialError` carrying the
    partial result when any unit fails; ``allow_partial=True`` returns a
    ``PARTIAL`` result when at least one unit succeeded; an all-failed batch
    raises the first unit failure. Cancellation propagates immediately.
    """
    canonical, records = _validated_symbols(symbols, max_symbols)
    parser_version = _parser_version()
    schema_version = 1

    tasks = [
        asyncio.ensure_future(_fetch_bundle(client, symbol, parser_version, schema_version))
        for symbol in canonical
    ]
    try:
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    bundles: list[QuoteBundle] = []
    succeeded: list[FetchResult[QuoteBundle]] = []
    failures: list[BaseException | None] = [None] * len(canonical)
    for position, outcome in enumerate(outcomes):
        if isinstance(outcome, asyncio.CancelledError):
            # A cancelled child is this batch's own cancellation surfacing
            # (gather's return_exceptions only shields exceptions): cancel the
            # remaining units and propagate — it must never become a unit
            # failure (foundation contract: cancellation propagates).
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise outcome
        if isinstance(outcome, BaseException):
            failures[position] = outcome
        else:
            succeeded.append(outcome)
            bundles.append(outcome.data)

    raised = [failure for failure in failures if failure is not None]
    if raised and not succeeded:
        raise raised[0]
    if raised:
        partial = _bundle_result(
            bundles,
            records,
            succeeded,
            parser_version,
            schema_version,
            ResultStatus.PARTIAL,
            canonical=canonical,
            failures=failures,
        )
        if not allow_partial:
            raise FinvizPartialError(
                f"{len(raised)} of {len(canonical)} quote units failed",
                partial_result=partial,
            )
        return partial
    return _bundle_result(
        bundles,
        records,
        succeeded,
        parser_version,
        schema_version,
        ResultStatus.COMPLETE,
        canonical=canonical,
        failures=failures,
    )


def quote(
    symbols: str | Iterable[str],
    *,
    client: FinvizClient,
    allow_partial: bool = False,
    max_symbols: int | None = None,
) -> FetchResult[list[QuoteBundle]]:
    """Sync wrapper for :func:`quote_async`; rejects an active event loop."""
    return run_sync(
        quote_async(symbols, client=client, allow_partial=allow_partial, max_symbols=max_symbols)
    )


def _validated_symbols(
    symbols: str | Iterable[str], max_symbols: int | None
) -> tuple[list[str], tuple[SymbolResolutionRecord, ...]]:
    """Normalize/dedupe input and enforce the batch safety limit pre-network."""
    try:
        canonical, records = _resolve(symbols)
    except SymbolInputError as exc:
        raise FinvizQueryError(str(exc)) from exc
    if max_symbols is not None and (
        isinstance(max_symbols, bool) or not isinstance(max_symbols, int) or max_symbols < 1
    ):
        msg = f"max_symbols: expected a positive integer, got {max_symbols!r}"
        raise FinvizQueryError(msg)
    limit = DEFAULT_MAX_SYMBOLS if max_symbols is None else max_symbols
    if len(canonical) > limit:
        msg = f"symbols: {len(canonical)} unique symbols exceed the safety limit of {limit}"
        raise FinvizQueryError(msg)
    return canonical, tuple(records)


def _bundle_result(
    bundles: list[QuoteBundle],
    records: tuple[SymbolResolutionRecord, ...],
    succeeded: Sequence[FetchResult[QuoteBundle]],
    parser_version: str,
    schema_version: int,
    status: ResultStatus,
    *,
    canonical: Sequence[str],
    failures: Sequence[BaseException | None],
) -> FetchResult[list[QuoteBundle]]:
    """Assemble the batch envelope; provenance comes from the succeeded units.

    The endpoint names the route the data actually came from: one canonical
    fetch for every unit, or the fallback route when every unit needed it.
    """
    first = succeeded[0].metadata
    endpoints = {unit.metadata.endpoint for unit in succeeded}
    endpoint = endpoints.pop() if len(endpoints) == 1 else first.endpoint
    unit_errors = tuple(
        UnitError(code="quote_symbol_failed", message=type(exc).__name__, symbol=symbol)
        for symbol, exc in zip(canonical, failures, strict=True)
        if exc is not None
    )
    failed = len(unit_errors)
    return FetchResult(
        bundles,
        ResultMetadata(
            endpoint=endpoint,
            status=status,
            access_tier=first.access_tier,
            fetched_at=first.fetched_at,
            served_at=first.served_at,
            symbols=records,
            requested_units=len(bundles) + failed,
            succeeded_units=len(bundles),
            failed_units=failed,
            unit_errors=unit_errors,
            attempts=first.attempts,
            cache_hit=all(unit.metadata.cache_hit for unit in succeeded),
            response_hash=first.response_hash,
            route_fingerprint=first.route_fingerprint,
            parser_version=parser_version,
            schema_version=schema_version,
        ),
    )


def _projection_async_factory(region: str) -> Any:
    """Build one cached-bundle relation projection (async + sync pair).

    The projection calls the quote operation (cache/single-flight shared), so
    a cold call fetches the stock page once and a warm call performs no HTTP
    request at all. Original provenance (response hash, fetch time, endpoint)
    is preserved verbatim; ``projected_from="quote"`` records the derivation.
    """

    async def projection_async(
        symbols: str | Iterable[str],
        *,
        client: FinvizClient,
        allow_partial: bool = False,
        max_symbols: int | None = None,
    ) -> FetchResult[Any]:
        quote_result = await quote_async(
            symbols,
            client=client,
            allow_partial=allow_partial,
            max_symbols=max_symbols,
        )
        tables = [getattr(bundle, region) for bundle in quote_result.data]
        return FetchResult(
            tables[0] if len(tables) == 1 else tables,
            replace(quote_result.metadata, projected_from="quote"),
        )

    def projection(
        symbols: str | Iterable[str],
        *,
        client: FinvizClient,
        allow_partial: bool = False,
        max_symbols: int | None = None,
    ) -> FetchResult[Any]:
        return run_sync(
            projection_async(
                symbols,
                client=client,
                allow_partial=allow_partial,
                max_symbols=max_symbols,
            )
        )

    projection_async.__doc__ = (
        f"Project the cached bundle's {region} relation; never refetches the page."
    )
    projection.__doc__ = f"Sync wrapper for {region}_async; rejects an active event loop."
    return projection_async, projection


snapshot_async, snapshot = _projection_async_factory("snapshot")
ratings_async, ratings = _projection_async_factory("ratings")
news_async, news = _projection_async_factory("news")
insider_async, insider = _projection_async_factory("insider")
peers_async, peers = _projection_async_factory("peers")
etf_holders_async, etf_holders = _projection_async_factory("etf_holders")
