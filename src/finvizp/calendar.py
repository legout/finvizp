"""Economic calendar and release details: embedded-JSON event tables.

Endpoint module in the foundation architecture: ``/calendar.ashx`` and
``/calendar/economic/detail/<RELEASE>`` both serve a SPA shell whose embedded
``route-init-data`` JSON carries the complete structured payload (verified
2026-08-30; no legacy ``table.calendar`` HTML remains, so there is no fallback
parser). The pure parser (:mod:`finvizp._parsers.calendar`) hands over
source-near rows and :func:`finvizp.arrow.build_table` normalizes them into
the registered ``economic_calendar`` / ``economic_details`` datasets.

Release details take exactly one explicit caller slug (validated pre-network)
and never enumerate the detail sitemap. One request per call; caching,
single-flight, retries, and provenance are the shared client's own.
"""

from __future__ import annotations

import re
from typing import Any

from finvizp import arrow as fa
from finvizp._parsers import calendar as cal_parser
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import FetchWarning, FinvizQueryError
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

__all__ = [
    "calendar",
    "calendar_async",
    "calendar_detail",
    "calendar_detail_async",
]

CALENDAR_PATH = "/calendar.ashx"
DETAIL_PREFIX = "/calendar/economic/detail/"

# Release slug contract (observed provider slugs like ``USACSA``, ``NFP TCH``
# are carried in the payload ticker field; the URL slug itself is a compact
# uppercase alphanumeric token). Validated pre-network.
_SLUG = re.compile(r"^[A-Z0-9]{1,64}$")

_PARSER_VERSION = "1"


def _validate_release(release: Any) -> str:
    if not isinstance(release, str) or not _SLUG.fullmatch(release):
        msg = (
            "release slug must be 1-64 uppercase letters/digits (as in the "
            f"provider detail URL), got {release!r}"
        )
        raise FinvizQueryError(msg)
    return release


def _restore_release_date_raw(table: Any, displays: list[str | None]) -> Any:
    """Put the provider's verbatim ``date`` displays into ``release_date_raw``.

    The parser splits ``date`` into day + time (the builder's date unit only
    reads an ISO day), so the raw companion must be restored explicitly. The
    builder's ``raw_overrides`` are strings for every row — they cannot
    express per-row nulls — so the parser hands over ``None``-able displays
    and this helper writes them into the built table directly.
    """
    import pyarrow as pa

    column_index = table.schema.get_field_index("release_date_raw")
    return table.set_column(
        column_index,
        table.schema.field(column_index),
        pa.array(displays, type=table.schema.field(column_index).type),
    )


def _parse_calendar(response: ClientResponse, *, strict_schema: bool = False) -> FetchResult[Any]:
    """Reviewed calendar parser: classified envelope -> immutable FetchResult."""
    warnings: list[FetchWarning] = []
    records = cal_parser.parse_calendar_page(response.data, fetched_at=response.fetched_at)
    if not records.rows:
        return _empty_result(response, "economic_calendar")
    table = _restore_release_date_raw(
        fa.build_table(
            "economic_calendar",
            records.rows,
            fetched_at=response.fetched_at,
            strict_schema=strict_schema,
            on_warning=warnings.append,
        ),
        records.displays["release_date"],
    )
    return _complete_result(response, table, warnings)


def _parse_detail(response: ClientResponse, *, strict_schema: bool = False) -> FetchResult[Any]:
    """Reviewed release-detail parser: classified envelope -> immutable FetchResult."""
    warnings: list[FetchWarning] = []
    records = cal_parser.parse_release_detail_page(response.data, fetched_at=response.fetched_at)
    if not records.rows:
        return _empty_result(response, "economic_details")
    table = _restore_release_date_raw(
        fa.build_table(
            "economic_details",
            records.rows,
            fetched_at=response.fetched_at,
            strict_schema=strict_schema,
            on_warning=warnings.append,
        ),
        records.displays["release_date"],
    )
    return _complete_result(response, table, warnings)


def _empty_result(response: ClientResponse, dataset: str) -> FetchResult[Any]:
    """Positively recognized no-entries state: registered empty table, zero units."""
    return FetchResult(
        data=fa.build_table(dataset, [], fetched_at=response.fetched_at),
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


def _calendar_op(
    client: FinvizClient, *, refresh: bool = False, strict_schema: bool = False, cache: bool = True
):
    """Bind one current-calendar endpoint operation.

    ``strict_schema`` changes parse behavior, so it joins the cache identity
    through the representation facet (mirrors the statements contract).
    """
    return client._endpoint_op(
        CALENDAR_PATH,
        representation="embedded_json+strict" if strict_schema else "embedded_json",
        parser_version=_PARSER_VERSION,
        schema_version=1,
        refresh=refresh,
        cache=cache,
        parse=lambda response: _parse_calendar(response, strict_schema=strict_schema),
    )


def _detail_op(
    client: FinvizClient,
    release: str,
    *,
    refresh: bool = False,
    strict_schema: bool = False,
    cache: bool = True,
):
    """Bind one release-detail endpoint operation for an explicit slug."""
    return client._endpoint_op(
        f"{DETAIL_PREFIX}{release}",
        representation="embedded_json+strict" if strict_schema else "embedded_json",
        parser_version=_PARSER_VERSION,
        schema_version=1,
        refresh=refresh,
        cache=cache,
        parse=lambda response: _parse_detail(response, strict_schema=strict_schema),
    )


async def calendar_async(
    *,
    client: FinvizClient,
    refresh: bool = False,
    strict_schema: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Fetch the current economic calendar; Arrow ``economic_calendar`` table.

    One request to ``/calendar.ashx``; the embedded payload is the complete
    representation, so there is no HTML fallback. Recognized zero-entry
    payloads return ``EMPTY`` with the registered schema; structurally broken
    payloads raise :class:`FinvizParseError`. ``refresh`` bypasses and
    replaces any cached entry; ``cache=False`` bypasses it without storing.
    """
    return await _calendar_op(client, refresh=refresh, strict_schema=strict_schema, cache=cache)()


async def calendar_detail_async(
    release: str,
    *,
    client: FinvizClient,
    refresh: bool = False,
    strict_schema: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Fetch one explicitly named release's history; ``economic_details`` table.

    ``release`` is the provider's URL slug (``/calendar/economic/detail/<slug>``),
    validated pre-network. Exactly one request for exactly one caller-chosen
    release — the detail sitemap is never enumerated. ``refresh`` bypasses and
    replaces any cached entry; ``cache=False`` bypasses it without storing.
    """
    release = _validate_release(release)
    return await _detail_op(
        client, release, refresh=refresh, strict_schema=strict_schema, cache=cache
    )()


def calendar(
    *,
    client: FinvizClient,
    refresh: bool = False,
    strict_schema: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`calendar_async`; rejects an active event loop."""
    return run_sync(
        calendar_async(client=client, refresh=refresh, strict_schema=strict_schema, cache=cache)
    )


def calendar_detail(
    release: str,
    *,
    client: FinvizClient,
    refresh: bool = False,
    strict_schema: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`calendar_detail_async`; rejects an active event loop."""
    return run_sync(
        calendar_detail_async(
            release, client=client, refresh=refresh, strict_schema=strict_schema, cache=cache
        )
    )
