"""Public forex operations: performance tables, price tiles, chart descriptors.

The performance table is the verified ``/forex_performance.ashx`` HTML
contract (one ``groups_table``: No./Pair/Price + Perf 5Min..Year); the
``PIPS`` variant (``v=1&tv=2``) keeps the identical shape with pip-count
displays that pass through as plain floats, each column keeping its ``_raw``
companion. The ``/forex.ashx`` page embeds one first-party tile JSON payload
(``Finviz:FinvizInitForex``); tiles are returned honestly — the sparkline
array is preserved verbatim with no timestamps (the provider sends none and
none are invented), ``change`` percent values become decimal fractions, and
unknown tile fields land in ``extra_fields``.

Chart operations return an immutable :class:`~finvizp.models.Artifact`
descriptor whose URL is the gallery page's own cross-origin
``charts2-node.finviz.com`` srcset entry, verbatim — never constructed and
never fetched (the client contract pins transport to the canonical origin;
explicit byte downloads are the 0.4 artifact card's surface).

Recognized empty (a populated table shell with zero data rows, or no tile
payload entries) yields an ``EMPTY`` result; structural drift raises
:class:`FinvizParseError`. Cancellation propagates immediately.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from finvizp._parsers import markets
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import FinvizParseError, FinvizQueryError
from finvizp.models import Artifact
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

__all__ = [
    "FOREX_CHARTS_PATH",
    "FOREX_PERFORMANCE_PATH",
    "FOREX_TILE_EVENT",
    "FOREX_TILES_PATH",
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

FOREX_PERFORMANCE_PATH = "/forex_performance.ashx"
FOREX_TILES_PATH = "/forex.ashx"
FOREX_CHARTS_PATH = "/forex_charts.ashx"
FOREX_TILE_EVENT = "FinvizInitForex"
_FAMILY = "forex"
_PARSER_VERSION = "1"
_SCHEMA_VERSION = 1

# Verified chart-page timeframe codes -> provider URL parameter values.
_TIMEFRAMES: dict[str, str] = {"5m": "m5", "1h": "h1", "d": "d", "w": "w", "m": "mo"}
_CHART_SYMBOL_QUERY = {"t": "ALL", "tf": "d"}
_ARTIFACT = Artifact  # re-exported descriptor type


def _metadata(response: ClientResponse, status: ResultStatus, *, units: int = 1) -> ResultMetadata:
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
        requested_units=0 if status is ResultStatus.EMPTY else units,
        succeeded_units=0 if status is ResultStatus.EMPTY else units,
        failed_units=0,
    )


def _parse_performance(response: ClientResponse) -> FetchResult[Any]:
    """Reviewed performance parser: EMPTY verdict or the wide Arrow table."""
    page = markets.parse_market_performance(response.data, family=_FAMILY)
    if page.is_empty:
        table: pa.Table = markets.performance_table(page, response.fetched_at, family=_FAMILY)
        return FetchResult(table, _metadata(response, ResultStatus.EMPTY))
    table = markets.performance_table(page, response.fetched_at, family=_FAMILY)
    return FetchResult(table, _metadata(response, ResultStatus.COMPLETE))


def _parse_tiles(response: ClientResponse) -> FetchResult[Any]:
    """Reviewed tile parser: EMPTY verdict when no tiles are embedded."""
    rows = markets.parse_market_tiles(response.data, tile_event=FOREX_TILE_EVENT)
    if not rows:
        bundle = markets.TileBundle(
            rows=(),
            fetched_at=response.fetched_at,
            access_tier=response.access_tier,
        )
        return FetchResult(bundle, _metadata(response, ResultStatus.EMPTY))
    bundle = markets.TileBundle(
        rows=rows,
        fetched_at=response.fetched_at,
        access_tier=response.access_tier,
    )
    return FetchResult(bundle, _metadata(response, ResultStatus.COMPLETE))


def _validate_timeframe(timeframe: str) -> str:
    if timeframe not in _TIMEFRAMES:
        msg = (
            f"timeframe must be one of {sorted(_TIMEFRAMES)}, got {timeframe!r}"
        )
        raise FinvizQueryError(msg)
    return timeframe


def _validate_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        msg = f"symbol must be a non-empty string, got {symbol!r}"
        raise FinvizQueryError(msg)
    return symbol.strip()


def _chart_result(
    descriptor: Artifact, *, query: dict[str, Any]
) -> FetchResult[Any]:
    return FetchResult(
        descriptor,
        ResultMetadata(
            endpoint=FOREX_CHARTS_PATH,
            status=ResultStatus.COMPLETE,
            access_tier=descriptor.access_tier,
            fetched_at=descriptor.fetched_at,
            query=dict(query),
            attempts=1,
            parser_version=_PARSER_VERSION,
            schema_version=_SCHEMA_VERSION,
            requested_units=1,
            succeeded_units=1,
            failed_units=0,
        ),
    )


async def performance_async(
    *,
    client: FinvizClient,
    change: str = "percent",
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Fetch the public forex performance table as a wide Arrow table.

    ``change`` selects the display variant: ``"percent"`` (default) or
    ``"PIPS"`` (pip counts; same schema, raw displays carry the pips).
    A recognized empty table yields an ``EMPTY`` result; structural drift
    raises :class:`FinvizParseError`. ``cache=False`` bypasses the client
    cache; ``refresh=True`` fetches a fresh copy.
    """
    if change not in {"percent", "PIPS"}:
        msg = f"change must be 'percent' or 'PIPS', got {change!r}"
        raise FinvizQueryError(msg)
    query = {} if change == "percent" else {"v": "1", "tv": "2", "o": "-perfdaypct"}
    return await client._endpoint_op(
        FOREX_PERFORMANCE_PATH,
        query=query,
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
    change: str = "percent",
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`performance_async`; rejects an active event loop."""
    return run_sync(
        performance_async(client=client, change=change, cache=cache, refresh=refresh)
    )


async def tiles_async(
    *,
    client: FinvizClient,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Fetch the public forex price tiles into one :class:`TileBundle`.

    The bundle preserves the provider's embedded tile payload with honest
    temporal semantics: sparkline arrays are verbatim value sequences with no
    timestamps and no inferred history. ``cache=False`` bypasses the cache;
    ``refresh=True`` fetches a fresh copy.
    """
    return await client._endpoint_op(
        FOREX_TILES_PATH,
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
    """Describe one forex chart image from the public charts gallery.

    Returns an immutable :class:`Artifact` descriptor (source URL, media
    type, symbol, timeframe) without downloading bytes. The URL is the
    page's own cross-origin image host entry, verbatim. ``timeframe`` is one
    of ``5m``/``1h``/``d``/``w``/``m`` and is validated before any I/O.
    """
    _validate_symbol(symbol)
    _validate_timeframe(timeframe)
    query = dict(_CHART_SYMBOL_QUERY)
    query["tf"] = _TIMEFRAMES[timeframe]
    return await client._endpoint_op(
        FOREX_CHARTS_PATH,
        query=query,
        cache=cache,
        refresh=refresh,
        representation="markets_charts",
        parser_version=_PARSER_VERSION,
        schema_version=_SCHEMA_VERSION,
        parse=_parse_charts(symbol, timeframe),
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
        return FetchResult(
            descriptor,
            ResultMetadata(
                endpoint=response.endpoint,
                status=ResultStatus.COMPLETE,
                access_tier=response.access_tier,
                fetched_at=response.fetched_at,
                query=dict(response.query),
                attempts=response.attempts,
                response_hash=response.response_hash,
                route_fingerprint=response.route_fingerprint,
                parser_version=_PARSER_VERSION,
                schema_version=_SCHEMA_VERSION,
                requested_units=1,
                succeeded_units=1,
                failed_units=0,
            ),
        )

    return parse
