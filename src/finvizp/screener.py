"""Public screener operations: header-driven screen tables over public HTML.

The collector requests one explicit caller-built screen (a validated
:class:`ScreenerQuery`), parses each page with the pure parser, and follows
the provider's own pagination evidence — a page is final when it renders
fewer rows than the registry page size or its ``#start / total Total`` marker
puts ``start + page_size`` past the observed total. The universe size is
never assumed; every stop is page evidence. Ranks repeating across pages
(repeated pagination state) and mid-walk column drift are structural drift.

``max_pages``/``max_rows`` are client-side safety stops, not provider
parameters: hitting one yields a ``COMPLETE`` result carrying the stop
warning (nothing failed). A mid-walk request failure under strict mode
raises the original typed error; ``allow_partial=True`` returns the rows
fetched so far as ``PARTIAL`` (with zero rows fetched the original error
still raises — there is nothing to carry). Cancellation propagates
immediately.

The public representation is the anonymous HTML table. Elite export
endpoints are never requested.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pyarrow as pa

from finvizp._parsers import _displays
from finvizp._parsers.screener import ScreenerPage, parse_screener_page
from finvizp._queries.screener import ScreenerQuery, screener_registry
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import (
    FetchWarning,
    FinvizError,
    FinvizParseError,
    FinvizPartialError,
    FinvizQueryError,
)
from finvizp.results import AccessTier, FetchResult, ResultMetadata, ResultStatus

__all__ = ["DEFAULT_MAX_PAGES", "DEFAULT_MAX_ROWS", "SCREEN_PATH", "screen", "screen_async"]

SCREEN_PATH = "/screener.ashx"
_PARSER_VERSION = "1"
_SCHEMA_VERSION = 1

# Reviewed safety ceiling: complete pagination of one explicit screen under
# conservative limits; never a crawl of every screen/filter combination.
DEFAULT_MAX_PAGES = 250
DEFAULT_MAX_ROWS = 20_000

ProgressCallback = Callable[[int, int], Any]

# Per-page pagination evidence rides in the cached page table's schema
# metadata (the only channel that survives client caching, which stores the
# parsed FetchResult, never raw HTML). Stripped from the combined table.
_PAGE_START_KEY = b"finvizp:page_start"
_PAGE_TOTAL_KEY = b"finvizp:page_total"

# Header label -> (snake_case field, Arrow unit). Labels not listed here stay
# text displays; the custom view's requested names map through the same table.
_COLUMN_UNITS: dict[str, str] = {
    "No.": "int64",
    "Market Cap": "compact",
    "P/E": "float64",
    "Forward P/E": "float64",
    "PEG": "float64",
    "P/S": "float64",
    "P/B": "float64",
    "P/Cash": "float64",
    "P/FCF": "float64",
    "EPS": "float64",
    "Price": "float64",
    "Change %": "percent",
    "Volume": "int64",
    "Sales": "compact",
    "Income": "compact",
    "Dividend %": "percent",
    "Employees": "int64",
    "Beta": "float64",
    "ATR": "float64",
    "Volatility W": "percent",
    "Volatility M": "percent",
    "RSI": "float64",
    "Gap %": "percent",
    "Perf Week": "percent",
    "Perf Month": "percent",
    "Perf Quart": "percent",
    "Perf Half Y": "percent",
    "Perf Year": "percent",
    "Perf YTD": "percent",
    "Float Short": "percent",
    "Inst Own": "percent",
    "Insider Own": "percent",
    "Sales growth quarter": "percent",
    "EPS growth quarter": "percent",
    "EPS this Y": "percent",
    "EPS next Y": "percent",
    "EPS past 5Y": "percent",
    "EPS next 5Y": "percent",
    "Sales past 5Y": "percent",
    "Quick Ratio": "float64",
    "Current Ratio": "float64",
    "Debt/Eq": "float64",
    "LT Debt/Eq": "float64",
    "ROA": "percent",
    "ROE": "percent",
    "ROI": "percent",
    "Gross Margin": "percent",
    "Oper. Margin": "percent",
    "Profit Margin": "percent",
    "Earnigs": "float64",
    "Target Price": "float64",
    "Shares Outstand": "compact",
    "Shares Float": "compact",
    "Short Ratio": "float64",
    "Recom": "float64",
    "SMA20": "percent",
    "SMA50": "percent",
    "SMA200": "percent",
    "Rel Volume": "float64",
    "Avg Volume": "compact",
    "Sales Surprise %": "percent",
    "EPS Surprise %": "percent",
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
    # Collapse repeats produced by consecutive separators.
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
            context={"endpoint": "screener"},
        ) from exc
    return display


def _schema_for(columns: tuple[str, ...]) -> pa.Schema:
    """Deterministic Arrow schema from one page's header-driven columns."""
    fields = [
        pa.field("rank", pa.int64(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
    ]
    for label in columns:
        if label in {"No.", "Ticker"}:
            continue
        fields.append(pa.field(_field_name(label), _arrow_type(label)))
    fields.append(pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False))
    fields.append(pa.field("extra_fields", pa.map_(pa.string(), pa.string()), nullable=True))
    return pa.schema(fields)


def _table_from_page(page: ScreenerPage, fetched_at: Any) -> pa.Table:
    schema = _schema_for(page.columns)
    ranks: list[int] = []
    symbols: list[str] = []
    values: dict[str, list[Any]] = {name: [] for name in schema.names[2:-2]}
    for row in page.rows:
        ranks.append(row.rank)
        symbols.append(row.symbol)
        displays = iter(row.raw)
        for label in page.columns:
            if label in {"No.", "Ticker"}:
                continue
            name = _field_name(label)
            values[name].append(_convert(label, next(displays)))
    arrays = [
        pa.array(ranks, type=schema.field("rank").type),
        pa.array(symbols, type=schema.field("symbol").type),
    ]
    arrays.extend(
        pa.array(values[name], type=schema.field(name).type) for name in schema.names[2:-2]
    )
    arrays.append(pa.array([fetched_at] * len(ranks), type=schema.field("fetched_at").type))
    arrays.append(pa.array([[] for _ in ranks], type=schema.field("extra_fields").type))
    return pa.Table.from_arrays(arrays, schema=schema)


def _empty_table() -> pa.Table:
    schema = _schema_for(())
    return pa.Table.from_arrays([pa.array([], type=field.type) for field in schema], schema=schema)


def _parse_screen_page(response: ClientResponse) -> FetchResult[Any]:
    """Reviewed endpoint parser: classified envelope -> one-page FetchResult."""
    page = parse_screener_page(response.data)
    if page.is_empty:
        table: pa.Table = _empty_table()
        status = ResultStatus.EMPTY
    else:
        table = _table_from_page(page, response.fetched_at).replace_schema_metadata(
            {
                _PAGE_START_KEY: str(page.page_start),
                _PAGE_TOTAL_KEY: str(page.total_rows),
            }
        )
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


def _page_facts(table: Any) -> tuple[int | None, int | None]:
    """Per-page pagination evidence stored by :func:`_parse_screen_page`."""
    meta = table.schema.metadata or {}
    try:
        return int(meta[_PAGE_START_KEY]), int(meta[_PAGE_TOTAL_KEY])
    except (KeyError, TypeError, ValueError):
        return None, None


def _positive_limit(name: str, value: int | None) -> int | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        msg = f"{name}: expected a positive integer, got {value!r}"
        raise FinvizQueryError(msg)
    return value


async def screen_async(
    query: ScreenerQuery,
    *,
    client: FinvizClient,
    allow_partial: bool = False,
    max_pages: int | None = None,
    max_rows: int | None = None,
    refresh: bool = False,
    cache: bool = True,
    on_progress: ProgressCallback | None = None,
) -> FetchResult[Any]:
    """Fetch one explicit screen completely and return its combined Arrow table.

    Pagination follows provider evidence only: a page is final when it renders
    fewer rows than the registry page size or its ``start + page_size`` exceeds
    the page marker's observed total. Repeated pagination state (duplicate
    ranks across pages), column drift between pages, and structural drift raise
    :class:`FinvizParseError` under strict mode; a mid-walk request failure
    raises the original typed error, or returns the rows fetched so far as
    ``PARTIAL`` with ``allow_partial=True``. ``max_pages``/``max_rows`` bound
    the walk as safety stops: hitting one yields ``COMPLETE`` with the stop
    warning (nothing failed). Cancellation propagates immediately.
    """
    page_limit = _positive_limit("max_pages", max_pages) or DEFAULT_MAX_PAGES
    row_limit = _positive_limit("max_rows", max_rows) or DEFAULT_MAX_ROWS
    page_size = screener_registry().page_size

    async with _client_or_transient(client) as op_client:
        page_tables: list[Any] = []
        seen_ranks: set[int] = set()
        warnings: list[FetchWarning] = []
        fetched_rows = 0
        done_pages = 0
        attempts = 0
        page_in_screen = query.page
        status = ResultStatus.COMPLETE
        first_meta: ResultMetadata | None = None
        # Set when a safety stop truncates the walk: strict mode raises
        # FinvizPartialError carrying the partial table, allow_partial
        # returns it. Truncation is the failed unit.
        stop_limit: str | None = None

        while True:
            # Cooperative yield: bounds CPU between pages and keeps
            # cancellation prompt even with instantaneous transports.
            await asyncio.sleep(0)
            page_query = (
                query if page_in_screen == query.page else replace(query, page=page_in_screen)
            )
            op = op_client._endpoint_op(
                SCREEN_PATH,
                query=page_query.provider_params(),
                cache=cache,
                refresh=refresh and page_in_screen == query.page,
                representation="screener_table",
                parser_version=_PARSER_VERSION,
                schema_version=_SCHEMA_VERSION,
                parse=_parse_screen_page,
            )
            try:
                result = await op()
            except FinvizError as exc:
                # Cancellation (BaseException) never lands here. A strict call
                # raises the original typed failure; with rows already in hand,
                # allow_partial returns them as PARTIAL. First-page failures
                # always raise: there is nothing to carry.
                if not allow_partial or not page_tables:
                    raise
                warnings.append(
                    FetchWarning(
                        code="page_failed",
                        message=f"screener page {page_in_screen} failed: {type(exc).__name__}",
                        endpoint="screener",
                    )
                )
                status = ResultStatus.PARTIAL
                break
            if first_meta is None:
                first_meta = result.metadata
            attempts += result.metadata.attempts
            done_pages += 1
            if on_progress is not None:
                on_progress(done_pages, fetched_rows + result.table.num_rows)

            if result.metadata.status is ResultStatus.EMPTY:
                status = ResultStatus.EMPTY
                break

            table = result.table
            ranks = table.column("rank").to_pylist()
            duplicated = seen_ranks.intersection(ranks)
            if duplicated:
                msg = f"screener rank {min(duplicated)} was already seen on an earlier page"
                raise FinvizParseError(msg, context={"endpoint": "screener"})
            seen_ranks.update(ranks)
            if page_tables and tuple(table.column_names) != tuple(page_tables[0].column_names):
                msg = f"screener page {page_in_screen} columns drifted from the first page"
                raise FinvizParseError(msg, context={"endpoint": "screener"})
            page_tables.append(table)
            fetched_rows += table.num_rows

            page_start, total_rows = _page_facts(table)
            effective_start = (
                page_start if page_start is not None else ((page_in_screen - 1) * page_size + 1)
            )
            is_final = len(ranks) < page_size or (
                total_rows is not None and effective_start + page_size > total_rows
            )
            if not is_final and fetched_rows >= row_limit:
                warnings.append(
                    FetchWarning(
                        code="max_rows_reached",
                        message=f"pagination stopped at the {row_limit}-row safety limit",
                        endpoint="screener",
                    )
                )
                status = ResultStatus.PARTIAL
                stop_limit = f"{row_limit}-row safety limit"
                break
            if is_final:
                break
            if done_pages >= page_limit:
                warnings.append(
                    FetchWarning(
                        code="max_pages_reached",
                        message=f"pagination stopped at the {page_limit}-page safety limit",
                        endpoint="screener",
                    )
                )
                status = ResultStatus.PARTIAL
                stop_limit = f"{page_limit}-page safety limit"
                break
            page_in_screen += 1

        if stop_limit is not None and not allow_partial:
            # The walk can only reach here after one parsed page, so the
            # first page's metadata exists.
            assert first_meta is not None
            partial = FetchResult(
                pa.concat_tables([t.replace_schema_metadata(None) for t in page_tables]),
                ResultMetadata(
                    endpoint=SCREEN_PATH,
                    status=ResultStatus.PARTIAL,
                    access_tier=first_meta.access_tier,
                    fetched_at=first_meta.fetched_at,
                    query={"q": query.to_json()},
                    warnings=tuple(warnings),
                    requested_units=done_pages + 1,
                    succeeded_units=done_pages,
                    failed_units=1,
                    parser_version=_PARSER_VERSION,
                    schema_version=_SCHEMA_VERSION,
                ),
            )
            raise FinvizPartialError(
                f"screener pagination stopped at the {stop_limit} after {done_pages} pages",
                partial_result=partial,
            )

        # The walk always completes one loop iteration, so the first page's
        # metadata exists whenever we reach the envelope.
        assert first_meta is not None
        if page_tables:
            combined = pa.concat_tables([t.replace_schema_metadata(None) for t in page_tables])
        else:
            combined = _empty_table()
        if status is ResultStatus.EMPTY:
            units = (0, 0, 0)
        elif status is ResultStatus.PARTIAL:
            units = (done_pages + 1, done_pages, 1)
        else:
            units = (done_pages, done_pages, 0)
        metadata = ResultMetadata(
            endpoint=SCREEN_PATH,
            status=status,
            access_tier=first_meta.access_tier if first_meta else AccessTier.PUBLIC,
            fetched_at=first_meta.fetched_at,
            query={"q": query.to_json()},
            warnings=tuple(warnings),
            response_hash=first_meta.response_hash if first_meta else None,
            cache_hit=first_meta.cache_hit if first_meta else False,
            requested_units=units[0],
            succeeded_units=units[1],
            failed_units=units[2],
            attempts=attempts,
            parser_version=_PARSER_VERSION,
            schema_version=_SCHEMA_VERSION,
        )
        return FetchResult(combined, metadata)


def _client_or_transient(client: FinvizClient):
    from finvizp.symbols import _client_or_transient as _ctx

    return _ctx(client)


def screen(
    query: ScreenerQuery,
    *,
    client: FinvizClient,
    allow_partial: bool = False,
    max_pages: int | None = None,
    max_rows: int | None = None,
    refresh: bool = False,
    cache: bool = True,
    on_progress: ProgressCallback | None = None,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`screen_async`; rejects an active event loop."""
    return run_sync(
        screen_async(
            query,
            client=client,
            allow_partial=allow_partial,
            max_pages=max_pages,
            max_rows=max_rows,
            refresh=refresh,
            cache=cache,
            on_progress=on_progress,
        )
    )
