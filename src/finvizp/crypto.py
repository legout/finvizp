"""Public crypto operations: performance tables, price tiles, chart descriptors.

Mirrors :mod:`finvizp.forex` on the crypto surface's verified shape: the
``/crypto_performance.ashx`` table adds Ticker and Name identity columns,
the ``/crypto.ashx`` page embeds the ``Finviz:FinvizInitCrypto`` tile
payload, and chart descriptors resolve from the crypto charts gallery. The
same honest temporal semantics apply: sparkline arrays are verbatim value
sequences with no timestamps and no inferred history.
"""

from __future__ import annotations

from typing import Any

from finvizp._parsers import markets
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import FinvizQueryError
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

__all__ = [
    "CRYPTO_CHARTS_PATH",
    "CRYPTO_PERFORMANCE_PATH",
    "CRYPTO_TILE_EVENT",
    "CRYPTO_TILES_PATH",
    "Artifact",
    "TileBundle",
    "TileRow",
    "chart",
    "chart_async",
    "performance",
    "performance_async",
    "tiles",
    "tiles_async",
]

CRYPTO_PERFORMANCE_PATH = "/crypto_performance.ashx"
CRYPTO_TILES_PATH = "/crypto.ashx"
CRYPTO_CHARTS_PATH = "/crypto_charts.ashx"
CRYPTO_TILE_EVENT = "FinvizInitCrypto"
_FAMILY = "crypto"
_PARSER_VERSION = "1"
_SCHEMA_VERSION = 1

# Same verified timeframe grammar as the forex charts page.
_TIMEFRAMES: dict[str, str] = {"5m": "m5", "1h": "h1", "d": "d", "w": "w", "m": "mo"}
_CHART_SYMBOL_QUERY = {"t": "ALL", "tf": "d"}


def _metadata(response: ClientResponse, status: ResultStatus) -> ResultMetadata:
    return ResultMetadata(
        endpoint=response.endpoint,
        status=status,
        access_tier=response.access_tier,
        fetched_at=response.fetched_at,
        served_at=response.served_at,
        query=dict(response.query),
        attempts=response.attempts,
        response_hash=response.response_hash,
        route_fingerprint=response.route_fingerprint,
        parser_version=_PARSER_VERSION,
        schema_version=_SCHEMA_VERSION,
        requested_units=0 if status is ResultStatus.EMPTY else 1,
        succeeded_units=0 if status is ResultStatus.EMPTY else 1,
        failed_units=0,
    )


def _parse_performance(response: ClientResponse) -> FetchResult[Any]:
    page = markets.parse_market_performance(response.data, family=_FAMILY)
    status = ResultStatus.EMPTY if page.is_empty else ResultStatus.COMPLETE
    table = markets.performance_table(page, response.fetched_at, family=_FAMILY)
    return FetchResult(table, _metadata(response, status))


def _parse_tiles(response: ClientResponse) -> FetchResult[Any]:
    rows = markets.parse_market_tiles(response.data, tile_event=CRYPTO_TILE_EVENT)
    bundle = markets.TileBundle(
        rows=rows,
        fetched_at=response.fetched_at,
        access_tier=response.access_tier,
    )
    status = ResultStatus.EMPTY if not rows else ResultStatus.COMPLETE
    return FetchResult(bundle, _metadata(response, status))


async def performance_async(
    *,
    client: FinvizClient,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Fetch the public crypto performance table as a wide Arrow table.

    One row per ticker with ``rank``/``ticker``/``name``/``price`` plus the
    Perf 5Min..Year columns as decimal fractions, each keeping its ``_raw``
    display companion. A recognized empty table yields an ``EMPTY`` result;
    structural drift raises :class:`finvizp.errors.FinvizParseError`.
    ``cache=False`` bypasses the cache; ``refresh=True`` fetches fresh.
    """
    return await client._endpoint_op(
        CRYPTO_PERFORMANCE_PATH,
        query={},
        cache=cache,
        refresh=refresh,
        representation="markets_performance",
        parser_version=_PARSER_VERSION,
        schema_version=_SCHEMA_VERSION,
        parse=_parse_performance,
    )()


def performance(
    *,
    client: FinvizClient,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`performance_async`; rejects an active event loop."""
    return run_sync(performance_async(client=client, cache=cache, refresh=refresh))


async def tiles_async(
    *,
    client: FinvizClient,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Fetch the public crypto price tiles into one :class:`TileBundle`.

    The bundle preserves the provider's embedded tile payload with honest
    temporal semantics: sparkline arrays are verbatim value sequences with no
    timestamps and no inferred history. ``cache=False`` bypasses the cache;
    ``refresh=True`` fetches a fresh copy.
    """
    return await client._endpoint_op(
        CRYPTO_TILES_PATH,
        query={},
        cache=cache,
        refresh=refresh,
        representation="markets_tiles",
        parser_version=_PARSER_VERSION,
        schema_version=_SCHEMA_VERSION,
        parse=_parse_tiles,
    )()


def tiles(
    *,
    client: FinvizClient,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`tiles_async`; rejects an active event loop."""
    return run_sync(tiles_async(client=client, cache=cache, refresh=refresh))


async def chart_async(
    *,
    symbol: str,
    timeframe: str = "d",
    client: FinvizClient,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Describe one crypto chart image from the public charts gallery.

    Returns an immutable :class:`finvizp.models.Artifact` descriptor (source
    URL, media type, symbol, timeframe) without downloading bytes. The URL
    is the page's own cross-origin ``charts2-node.finviz.com`` srcset entry,
    verbatim. ``timeframe`` is one of ``5m``/``1h``/``d``/``w``/``m`` and is
    validated before any I/O.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        msg = f"symbol must be a non-empty string, got {symbol!r}"
        raise FinvizQueryError(msg)
    if timeframe not in _TIMEFRAMES:
        msg = f"timeframe must be one of {sorted(_TIMEFRAMES)}, got {timeframe!r}"
        raise FinvizQueryError(msg)
    query = dict(_CHART_SYMBOL_QUERY)
    query["tf"] = _TIMEFRAMES[timeframe]
    return await client._endpoint_op(
        CRYPTO_CHARTS_PATH,
        query=query,
        cache=cache,
        refresh=refresh,
        representation="markets_charts",
        parser_version=_PARSER_VERSION,
        schema_version=_SCHEMA_VERSION,
        parse=_parse_charts(symbol.strip(), timeframe),
    )()


def chart(
    *,
    symbol: str,
    timeframe: str = "d",
    client: FinvizClient,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`chart_async`; rejects an active event loop."""
    return run_sync(
        chart_async(
            symbol=symbol, timeframe=timeframe, client=client, cache=cache, refresh=refresh
        )
    )


def _parse_charts(symbol: str, timeframe: str):
    """Bind the requested symbol so its descriptor resolves from the page."""

    def parse(response: ClientResponse) -> FetchResult[Any]:
        descriptor = markets.chart_descriptor(
            response.data,
            symbol=symbol,
            timeframe=timeframe,
            fetched_at=response.fetched_at,
            family=_FAMILY,
        )
        return FetchResult(descriptor, _metadata(response, ResultStatus.COMPLETE))

    return parse
