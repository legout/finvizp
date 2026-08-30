"""Public groups operations: header-driven aggregate tables and spectrum descriptors.

The collector requests one explicit caller-built group page (a validated
:class:`GroupQuery` from ``finvizp._queries.groups``), parses it with the pure
parser, and returns a deterministic wide Arrow table: one row per group with
the group ``name``, the provider's aggregate columns typed from their display
units (compact/percent/number/count), ``_raw`` companions for every converted
column, and ``extra_fields``/``fetched_at`` provenance. Groups are single-page
observations; there is no pagination to walk.

The spectrum view returns an immutable :class:`Artifact` descriptor (URL,
media type, group) without downloading bytes — the legacy ``finvizfinance``
defect (calling ``.order_dict`` on a plain dict, raising ``AttributeError`` for
valid input) is structurally impossible on this typed path. Raw-byte download
helpers arrive with the 0.4 artifact card.

Recognized empty (a groups_table with zero data rows) yields an ``EMPTY``
result; structural drift raises :class:`FinvizParseError`. Cancellation
propagates immediately.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from finvizp._parsers import _displays
from finvizp._parsers.groups import GroupPage, parse_groups_page, parse_spectrum_page
from finvizp._queries.groups import (
    GroupColumn,
    GroupDimension,
    GroupOrder,
    GroupQuery,
    GroupView,
    groups_registry,
)
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import FinvizParseError, FinvizQueryError
from finvizp.models import Artifact
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

__all__ = [
    "GROUPS_PATH",
    "SPECTRUM_VIEW_CODE",
    "Artifact",
    "GroupColumn",
    "GroupDimension",
    "GroupOrder",
    "GroupQuery",
    "GroupView",
    "group",
    "group_async",
    "spectrum",
    "spectrum_async",
]

GROUPS_PATH = "/groups.ashx"
_PARSER_VERSION = "1"
_SCHEMA_VERSION = 1
SPECTRUM_VIEW_CODE = groups_registry().views["spectrum"]

# Header label -> (snake_case field, Arrow unit). Labels not listed here stay
# text displays; unknown labels land in extra_fields with a drift warning.
_COLUMN_UNITS: dict[str, str] = {
    "Stocks": "int64",
    "Market Cap": "compact",
    "Dividend": "percent",
    "P/E": "float64",
    "Fwd P/E": "float64",
    "PEG": "float64",
    "P/S": "float64",
    "P/B": "float64",
    "P/C": "float64",
    "P/FCF": "float64",
    "EPS past 5Y": "percent",
    "EPS next 5Y": "percent",
    "Sales past 5Y": "percent",
    "Float Short": "percent",
    "Perf Week": "percent",
    "Perf Month": "percent",
    "Perf Quart": "percent",
    "Perf Half": "percent",
    "Perf Year": "percent",
    "Perf YTD": "percent",
    "Recom": "float64",
    "Avg Volume": "compact",
    "Rel Volume": "float64",
    "Change %": "percent",
    "Volume": "compact",
    "LTDebt/Eq": "float64",
    "Debt/Eq": "float64",
    "ROA": "percent",
    "ROE": "percent",
    "ROIC": "percent",
    "Employees": "int64",
}


def _field_name(label: str) -> str:
    """Stable snake_case Arrow column name for one provider header label."""
    cleaned = (
        label.replace("/", " per ")
        .replace("%", " percent")
        .replace("+", " plus ")
        .replace("-", " ")
        .replace(".", "")
        .replace("(", " ")
        .replace(")", " ")
    )
    parts = [part for part in cleaned.split() if part]
    name = "_".join(parts).lower()
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_") or "column"


def _arrow_type(label: str) -> pa.DataType:
    unit = _COLUMN_UNITS.get(label)
    if unit == "int64":
        return pa.int64()
    if unit in {"float64", "percent", "compact"}:
        return pa.float64()
    return pa.string()


def _convert(label: str, display: str) -> Any:
    """Convert one raw display per the column's declared unit; None when blank."""
    if display in {"", "-"}:
        return None
    unit = _COLUMN_UNITS.get(label)
    try:
        if unit == "int64":
            return _displays.parse_int(display)
        if unit == "compact":
            return _displays.parse_compact(display)
        if unit == "float64":
            return float(display.replace(",", ""))
        if unit == "percent":
            return _displays.parse_percent(display)
    except ValueError as exc:
        raise FinvizParseError(
            f"cannot convert display to {unit} for column {label!r}",
            context={"endpoint": "groups"},
        ) from exc
    return display


def _schema_for(columns: tuple[str, ...]) -> pa.Schema:
    """Deterministic Arrow schema: identity, typed aggregates, provenance."""
    fields = [
        pa.field("rank", pa.int64(), nullable=False),
        pa.field("name", pa.string(), nullable=False),
    ]
    for label in columns:
        if label in {"No.", "Name"}:
            continue
        fields.append(pa.field(_field_name(label), _arrow_type(label)))
        if label in _COLUMN_UNITS:
            fields.append(pa.field(f"{_field_name(label)}_raw", pa.string()))
    fields.append(pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False))
    fields.append(pa.field("extra_fields", pa.map_(pa.string(), pa.string()), nullable=True))
    return pa.schema(fields)


def _table_from_page(page: GroupPage, fetched_at: Any) -> pa.Table:
    schema = _schema_for(page.columns)
    names: list[str] = schema.names
    ranks: list[int] = []
    group_names: list[str] = []
    values: dict[str, list[Any]] = {}
    raws: dict[str, list[str | None]] = {}
    for label in page.columns:
        if label in {"No.", "Name"}:
            continue
        name = _field_name(label)
        values[name] = []
        if label in _COLUMN_UNITS:
            raws[f"{name}_raw"] = []
    for row in page.rows:
        ranks.append(row.index)
        group_names.append(row.name)
        displays = iter(row.raw)
        for label in page.columns:
            if label in {"No.", "Name"}:
                continue
            name = _field_name(label)
            display = next(displays, None)
            if display is None:
                msg = f"groups row {row.index} ({row.name}) has no display for column {label!r}"
                raise FinvizParseError(msg, context={"endpoint": "groups"})
            if label in _COLUMN_UNITS:
                raws[f"{name}_raw"].append(display)
            values[name].append(_convert(label, display))
    arrays: list[pa.Array] = [
        pa.array(ranks, type=schema.field("rank").type),
        pa.array(group_names, type=schema.field("name").type),
    ]
    for name in names[2:-2]:
        if name.endswith("_raw"):
            arrays.append(pa.array(raws[name], type=schema.field(name).type))
        else:
            arrays.append(pa.array(values[name], type=schema.field(name).type))
    arrays.append(pa.array([fetched_at] * len(ranks), type=schema.field("fetched_at").type))
    arrays.append(pa.array([[] for _ in ranks], type=schema.field("extra_fields").type))
    return pa.Table.from_arrays(arrays, schema=schema)


def _empty_table() -> pa.Table:
    schema = _schema_for(("No.", "Name"))
    return pa.Table.from_arrays([pa.array([], type=field.type) for field in schema], schema=schema)


def _parse_groups_page(query: GroupQuery):
    """Reviewed endpoint parser: classified envelope -> one-page FetchResult.

    The query is bound so a custom view can prove the provider served every
    requested column; a missing one is drift, not silently dropped data.
    """

    def parse(response: ClientResponse) -> FetchResult[Any]:
        page = parse_groups_page(response.data)
        if query.columns is not None:
            served = set(page.columns)
            for column in query.columns:
                if column.name not in served:
                    msg = f"served groups page is missing requested column {column.name!r}"
                    raise FinvizParseError(msg, context={"endpoint": "groups"})
        if page.is_empty:
            table: pa.Table = _empty_table()
            status = ResultStatus.EMPTY
        else:
            table = _table_from_page(page, response.fetched_at)
            status = ResultStatus.COMPLETE
        metadata = ResultMetadata(
            endpoint=response.endpoint,
            status=status,
            access_tier=response.access_tier,
            fetched_at=response.fetched_at,
            query=dict(response.query),
            response_hash=response.response_hash,
            attempts=response.attempts,
            requested_units=0 if status is ResultStatus.EMPTY else 1,
            succeeded_units=0 if status is ResultStatus.EMPTY else 1,
            failed_units=0,
        )
        return FetchResult(table, metadata)

    return parse


def _parse_spectrum_response(query: GroupQuery):
    """Bind the query's dimension so the descriptor names its group."""

    def parse(response: ClientResponse) -> FetchResult[Any]:
        descriptor = parse_spectrum_page(
            response.data,
            dimension=query.dimension.code,
            fetched_at=response.fetched_at,
        )
        metadata = ResultMetadata(
            endpoint=response.endpoint,
            status=ResultStatus.COMPLETE,
            access_tier=response.access_tier,
            fetched_at=response.fetched_at,
            query=dict(response.query),
            response_hash=response.response_hash,
            attempts=response.attempts,
            requested_units=1,
            succeeded_units=1,
            failed_units=0,
        )
        return FetchResult(descriptor, metadata)

    return parse


def _validate_group_query(query: GroupQuery) -> GroupQuery:
    if not isinstance(query, GroupQuery):
        msg = "group operations take a validated GroupQuery"
        raise FinvizQueryError(msg)
    return query


async def group_async(
    query: GroupQuery,
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Fetch one explicit group page and return its Arrow aggregate table.

    The query is validated before any network I/O; the public HTML table is
    the representation (the Elite ``/grp_export`` CSV is never requested).
    A recognized empty table yields an ``EMPTY`` result; structural drift
    raises :class:`FinvizParseError`. ``cache=False`` bypasses the client
    cache for this call; ``refresh=True`` fetches a fresh copy.
    """
    _validate_group_query(query)
    async with _client_or_transient(client) as op_client:
        op = op_client._endpoint_op(
            GROUPS_PATH,
            query=query.provider_params(),
            cache=cache,
            refresh=refresh,
            representation="groups_table",
            parser_version=_PARSER_VERSION,
            schema_version=_SCHEMA_VERSION,
            parse=_parse_groups_page(query),
        )
        return await op()


def group(
    query: GroupQuery,
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`group_async`; rejects an active event loop."""
    return run_sync(group_async(query, client=client, refresh=refresh, cache=cache))


async def spectrum_async(
    query: GroupQuery,
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Describe one group spectrum image without downloading its bytes.

    Returns an immutable :class:`Artifact` descriptor (source URL, media type,
    group, chart type). Unlike the legacy ``finvizfinance`` spectrum path —
    which crashed with ``AttributeError`` on valid input by calling
    ``.order_dict`` on a plain dict — the descriptor resolves purely from the
    typed query's provider params. Raw-byte download helpers land with 0.4.
    """
    _validate_group_query(query)
    async with _client_or_transient(client) as op_client:
        op = op_client._endpoint_op(
            GROUPS_PATH,
            query=query.provider_params(),
            cache=cache,
            refresh=refresh,
            representation="groups_spectrum",
            parser_version=_PARSER_VERSION,
            schema_version=_SCHEMA_VERSION,
            parse=_parse_spectrum_response(query),
        )
        return await op()


def spectrum(
    query: GroupQuery,
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`spectrum_async`; rejects an active event loop."""
    return run_sync(spectrum_async(query, client=client, refresh=refresh, cache=cache))


def _client_or_transient(client: FinvizClient):
    from finvizp.symbols import _client_or_transient as _ctx

    return _ctx(client)
