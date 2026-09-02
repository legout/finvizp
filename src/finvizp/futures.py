"""Current public futures tile data: one page, embedded tile JSON.

Endpoint module in the foundation architecture: the public futures surface is
``/futures`` (the legacy ``/futures.ashx`` route 301-redirects there), whose
single inline script carries the complete current tile payload (verified
2026-08-30; the page has no ``<table>`` element at all, so the legacy
empty-table assumption is not preserved — there is no fallback parser). The
pure parser (:mod:`finvizp._parsers.futures`) hands over source-near tiles
and :func:`finvizp.arrow.build_table` normalizes them into the registered
``futures_tiles`` dataset: one row per contract tile with current price,
change/performance, session high/low, the page's own delay statement, and
the verbatim sparkline payload (no provider timestamps exist — sparklines
are never presented as history).

No chart/image artifact references exist on the verified page, so there is
nothing to project through the artifacts module; the tile payload itself is
the complete representation. One request per call; caching, single-flight,
retries, and provenance are the shared client's own.
"""

from __future__ import annotations

import json
from typing import Any

from finvizp import arrow as fa
from finvizp._parsers import futures as futures_parser
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import FetchWarning
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

__all__ = [
    "FUTURES_PATH",
    "futures",
    "futures_async",
]

FUTURES_PATH = "/futures"

_PARSER_VERSION = "1"

# Tile fields whose provider values feed registered columns. The raw value
# kept in each ``*_raw`` companion is the provider's own JSON payload text.
# The tile ``change`` is the percent change (verified evidence), so it feeds
# only ``change_percent``; the provider supplies no absolute-change value,
# so the dataset has no ``change`` column (cross-dataset convention: absolute
# change vs percent are distinct columns — see quote_snapshot).
_TILE_TO_ROW = {
    "label": "name",
    "last": "last",
    "change_usd": "change_usd",
    "prev_close": "prev_close",
    "high": "high",
    "low": "low",
}


def _parse_futures(response: ClientResponse, *, strict_schema: bool = False) -> FetchResult[Any]:
    """Reviewed futures parser: classified envelope -> immutable FetchResult."""
    warnings: list[FetchWarning] = []
    records = futures_parser.parse_futures_page(
        response.data, fetched_at=response.fetched_at, on_warning=warnings.append
    )
    if not records.tiles:
        return _empty_result(response)
    rows = []
    for ticker, tile in records.tiles.items():
        row: dict[str, Any] = {
            "symbol": ticker,
            "name": tile.get("label"),
            "category": records.groups.get(ticker),
            "change_percent": tile.get("change"),
            "sparkline": tile.get("sparkline"),
            # Provider payload decoration, kept as verbatim text like sparkline:
            # projected so a populated map can never vanish silently.
            "sparkline_date_changes": json.dumps(
                tile.get("sparkline_date_changes", {}), separators=(",", ":")
            ),
            "delay_minutes": records.delay_minutes,
        }
        for tile_name, row_name in _TILE_TO_ROW.items():
            row[row_name] = tile.get(tile_name)
        for field_name, value in records.extra_fields.get(ticker, {}).items():
            row[field_name] = value
        rows.append(row)
    table = fa.build_table(
        "futures_tiles",
        rows,
        fetched_at=response.fetched_at,
        strict_schema=strict_schema,
        on_warning=warnings.append,
    )
    return _complete_result(response, table, warnings)


def _empty_result(response: ClientResponse) -> FetchResult[Any]:
    """Positively recognized no-tiles state: registered empty table, zero units."""
    return FetchResult(
        data=fa.build_table("futures_tiles", [], fetched_at=response.fetched_at),
        metadata=ResultMetadata(
            endpoint=response.endpoint,
            status=ResultStatus.EMPTY,
            access_tier=response.access_tier,
            fetched_at=response.fetched_at,
            query=dict(response.query),
            requested_units=0,
            succeeded_units=0,
            failed_units=0,
            attempts=response.attempts,
            response_hash=response.response_hash,
            route_fingerprint=response.route_fingerprint,
        ),
    )


def _complete_result(
    response: ClientResponse, table: Any, warnings: list[FetchWarning]
) -> FetchResult[Any]:
    return FetchResult(
        data=table,
        metadata=ResultMetadata(
            endpoint=response.endpoint,
            status=ResultStatus.COMPLETE,
            access_tier=response.access_tier,
            fetched_at=response.fetched_at,
            query=dict(response.query),
            requested_units=1,
            succeeded_units=1,
            failed_units=0,
            attempts=response.attempts,
            response_hash=response.response_hash,
            route_fingerprint=response.route_fingerprint,
            warnings=tuple(warnings),
            parser_version=_PARSER_VERSION,
        ),
    )


def _futures_op(client: FinvizClient, *, refresh: bool = False, cache: bool = True):
    """Bind one current-futures endpoint operation."""
    return client._endpoint_op(
        FUTURES_PATH,
        representation="embedded_script_json",
        parser_version=_PARSER_VERSION,
        schema_version=1,
        refresh=refresh,
        cache=cache,
        parse=_parse_futures,
    )


async def futures_async(
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Fetch the current public futures tiles; Arrow ``futures_tiles`` table.

    One request to ``/futures``; the embedded tile payload is the complete
    representation, so there is no HTML fallback. Recognized zero-tile
    payloads return ``EMPTY`` with the registered schema; structurally broken
    payloads raise :class:`FinvizParseError`. The ``sparkline`` column is the
    provider's verbatim payload text — the payload carries no timestamps, so
    it is never presented as provider history. ``refresh`` bypasses and
    replaces any cached entry; ``cache=False`` bypasses it without storing.
    ``strict_schema`` stays a parser-level seam (not exposed): futures rows
    are fully registry-typed with no client-side display parsing.
    """
    return await _futures_op(client, refresh=refresh, cache=cache)


def futures(
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`futures_async`; rejects an active event loop."""
    return run_sync(futures_async(client=client, refresh=refresh, cache=cache))
