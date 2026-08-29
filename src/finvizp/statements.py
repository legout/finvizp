"""Public statement operations: IA/IQ/BA/BQ/CA/CQ as long Arrow tables.

Module-level async operations plus sync wrappers (architecture contract).
Validation happens pre-network: only the six reviewed statement codes are
accepted (the live endpoint silently echoes ``IA`` for unknown codes, so code
checking cannot be delegated to the provider). One request per canonical
symbol; responses parse through the pure JSON parser and normalize through the
registry-driven Arrow builder into the long ``statements`` dataset.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
from collections.abc import Iterable
from typing import Any

from finvizp import arrow as fa
from finvizp._parsers import statements as stmt_parser
from finvizp._symbols import resolve_symbols
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import (
    FetchWarning,
    FinvizBatchError,
    FinvizPartialError,
    FinvizQueryError,
    UnitError,
)
from finvizp.results import (
    AccessTier,
    FetchResult,
    ResultMetadata,
    ResultStatus,
    SymbolResolutionRecord,
)

__all__ = [
    "STATEMENT_CODES",
    "statements",
    "statements_async",
    "statements_batch",
    "statements_batch_async",
]

STATEMENT_CODES: dict[str, tuple[str, str]] = {
    "IA": ("income", "annual"),
    "IQ": ("income", "quarterly"),
    "BA": ("balance", "annual"),
    "BQ": ("balance", "quarterly"),
    "CA": ("cashflow", "annual"),
    "CQ": ("cashflow", "quarterly"),
}

# Cap on symbols per batch call (conservative bounded access, not crawling).
MAX_BATCH_SYMBOLS = 50

_ROUTE = "/api/statement"
_PARSER_VERSION = "1"
_SCHEMA_VERSION = 1  # statements dataset version


def _transient_client() -> FinvizClient:
    """One-shot default client for calls without an explicit ``client``."""
    return FinvizClient()


def _validate(statement: str) -> tuple[str, str]:
    if not isinstance(statement, str) or statement not in STATEMENT_CODES:
        valid = ", ".join(sorted(STATEMENT_CODES))
        msg = f"statement must be one of {valid}, got {statement!r}"
        raise FinvizQueryError(msg)
    return STATEMENT_CODES[statement]


def _parse_response(
    response: ClientResponse,
    *,
    symbol: str,
    statement: str,
    kind: str,
    periodicity: str,
    strict_schema: bool = False,
) -> FetchResult[Any]:
    """Reviewed endpoint parser: classified envelope -> immutable FetchResult."""
    warnings: list[FetchWarning] = []
    records = stmt_parser.parse_statement_json(
        response.data, symbol=symbol, statement=statement, fetched_at=response.fetched_at
    )
    if records.empty_recognized:
        # Positively recognized no-results state: registered empty table.
        # Metadata contract: EMPTY results carry zero units (nothing failed —
        # the provider positively answered "no data").
        table = fa.build_table("statements", [], fetched_at=response.fetched_at)
        return FetchResult(
            data=table,
            metadata=ResultMetadata(
                endpoint=_ROUTE,
                status=ResultStatus.EMPTY,
                access_tier=response.access_tier,
                fetched_at=response.fetched_at,
                query={"t": symbol, "s": statement},
                symbols=(SymbolResolutionRecord(position=0, requested=symbol, canonical=symbol),),
                requested_units=0,
                succeeded_units=0,
                failed_units=0,
                attempts=response.attempts,
                response_hash=response.response_hash,
                route_fingerprint=response.route_fingerprint,
                warnings=tuple(warnings),
            ),
        )
    rows = [
        {
            "symbol": row["symbol"],
            "statement_kind": kind,
            "periodicity": periodicity,
            "period_label": row["period_label"],
            "period_end_date": row["period_end_date"],
            "period_length_days": row["period_length_days"],
            "metric": row["metric"],
            "value": row["value"],
            "currency": row["currency"],
        }
        for row in records.rows
    ]
    table = fa.build_table(
        "statements",
        rows,
        fetched_at=response.fetched_at,
        strict_schema=strict_schema,
        on_warning=warnings.append,
    )
    return FetchResult(
        data=table,
        metadata=ResultMetadata(
            endpoint=_ROUTE,
            status=ResultStatus.COMPLETE,
            access_tier=response.access_tier,
            fetched_at=response.fetched_at,
            query={"t": symbol, "s": statement},
            symbols=(SymbolResolutionRecord(position=0, requested=symbol, canonical=symbol),),
            requested_units=1,
            succeeded_units=1,
            failed_units=0,
            attempts=response.attempts,
            response_hash=response.response_hash,
            route_fingerprint=response.route_fingerprint,
            warnings=tuple(warnings),
        ),
    )


def _symbol_op(
    client: FinvizClient,
    symbol: str,
    statement: str,
    kind: str,
    periodicity: str,
    *,
    refresh: bool = False,
    proxy: str | bool | None = None,
    strict_schema: bool = False,
):
    """Bind one cached per-symbol endpoint operation."""

    def parse(response: ClientResponse) -> FetchResult[Any]:
        return _parse_response(
            response,
            symbol=symbol,
            statement=statement,
            kind=kind,
            periodicity=periodicity,
            strict_schema=strict_schema,
        )

    return client._endpoint_op(
        _ROUTE,
        query={"t": symbol, "s": statement},
        # strict_schema changes parse behavior, so it must join the cache
        # identity: a strict call may never serve a non-strict parsed entry
        # (and vice versa), cache or single-flight.
        representation="statement-json+strict" if strict_schema else "statement-json",
        parser_version=_PARSER_VERSION,
        schema_version=_SCHEMA_VERSION,
        refresh=refresh,
        proxy=proxy,
        parse=parse,
    )


async def statements_async(
    symbols: str | Iterable[str],
    *,
    statement: str = "IA",
    client: FinvizClient | None = None,
    refresh: bool = False,
    proxy: str | bool | None = None,
    strict_schema: bool = False,
) -> FetchResult[Any]:
    """Fetch one statement form for one symbol; long Arrow table result.

    Args:
        symbols: one symbol or an iterable resolving to exactly one canonical
            symbol (original spellings are preserved in the metadata).
        statement: reviewed code — ``IA``/``IQ``/``BA``/``BQ``/``CA``/``CQ``.
        client: reusable client; a transient one is created and closed when
            omitted.
        refresh: bypass the cache and replace any cached entry.
        proxy: per-call proxy override (URL, ``False`` forces direct, ``None``
            keeps client config).
        strict_schema: promote recoverable Arrow conversion drift to typed
            errors (foundation contract).

    Returns an immutable :class:`FetchResult` whose ``.data`` is the long
    ``statements`` Arrow table (``.table`` accessor). A provider
    ``{"error": "no data"}`` payload is a recognized ``EMPTY`` result.
    """
    kind, periodicity = _validate(statement)
    canonical, records = resolve_symbols(symbols)
    if len(canonical) != 1:
        msg = (
            "statements_async() takes exactly one symbol; use statements_batch_async() for batches"
        )
        raise FinvizQueryError(msg)
    # One canonical fetch; metadata keeps every original input position and
    # spelling (multiple spellings may collapse onto the one canonical form).
    spellings = [record.requested for record in records]
    symbol = canonical[0]
    if client is None:
        async with _transient_client() as transient:
            return await _statements_via(
                transient,
                spellings,
                symbol,
                statement,
                kind,
                periodicity,
                refresh=refresh,
                proxy=proxy,
                strict_schema=strict_schema,
            )
    return await _statements_via(
        client,
        spellings,
        symbol,
        statement,
        kind,
        periodicity,
        refresh=refresh,
        proxy=proxy,
        strict_schema=strict_schema,
    )


async def _statements_via(
    client: FinvizClient,
    spellings: list[str],
    symbol: str,
    statement: str,
    kind: str,
    periodicity: str,
    *,
    refresh: bool,
    proxy: str | bool | None,
    strict_schema: bool,
) -> FetchResult[Any]:
    """Run the one canonical cached op, then stamp per-input provenance."""
    op = _symbol_op(
        client,
        symbol,
        statement,
        kind,
        periodicity,
        refresh=refresh,
        proxy=proxy,
        strict_schema=strict_schema,
    )
    result = await op()
    return dataclasses.replace(
        result,
        metadata=dataclasses.replace(
            result.metadata,
            symbols=tuple(
                SymbolResolutionRecord(position=position, requested=requested, canonical=symbol)
                for position, requested in enumerate(spellings)
            ),
        ),
    )


def statements(
    symbols: str | Iterable[str],
    *,
    statement: str = "IA",
    client: FinvizClient | None = None,
    refresh: bool = False,
    proxy: str | bool | None = None,
    strict_schema: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`statements_async`; fails inside a running loop."""
    return run_sync(
        statements_async(
            symbols,
            statement=statement,
            client=client,
            refresh=refresh,
            proxy=proxy,
            strict_schema=strict_schema,
        )
    )


def _concat_tables(tables: list[Any]) -> Any:
    """Deterministic vertical concatenation of same-schema Arrow tables."""
    import pyarrow as pa

    if len(tables) == 1:
        return tables[0]
    return pa.concat_tables(tables)


async def statements_batch_async(
    symbols: str | Iterable[str],
    *,
    statement: str = "IA",
    client: FinvizClient | None = None,
    allow_partial: bool = False,
    refresh: bool = False,
    proxy: str | bool | None = None,
    strict_schema: bool = False,
) -> FetchResult[Any]:
    """Fetch one statement form for many symbols, bounded and concurrent.

    Each canonical symbol is fetched once (deduped, first-occurrence order);
    per-symbol results concatenate into one long Arrow table. ``metadata`` keeps
    one :class:`SymbolResolutionRecord` per original input position/spelling.
    Default strict mode raises :class:`FinvizPartialError` carrying the
    immutable partial result (with per-unit failure detail) when any unit
    fails; ``allow_partial=True`` returns it instead. An all-failed batch
    always raises :class:`FinvizBatchError`.
    """
    kind, periodicity = _validate(statement)
    canonical, records = resolve_symbols(symbols)
    if len(canonical) > MAX_BATCH_SYMBOLS:
        msg = f"batch exceeds the bounded limit of {MAX_BATCH_SYMBOLS} symbols"
        raise FinvizQueryError(msg)

    if client is None:
        async with _transient_client() as transient:
            return await _run_batch(
                transient,
                canonical,
                records,
                statement,
                kind,
                periodicity,
                allow_partial=allow_partial,
                refresh=refresh,
                proxy=proxy,
                strict_schema=strict_schema,
            )
    return await _run_batch(
        client,
        canonical,
        records,
        statement,
        kind,
        periodicity,
        allow_partial=allow_partial,
        refresh=refresh,
        proxy=proxy,
        strict_schema=strict_schema,
    )


async def _run_batch(
    client: FinvizClient,
    canonical: list[str],
    records: list[SymbolResolutionRecord],
    statement: str,
    kind: str,
    periodicity: str,
    *,
    allow_partial: bool,
    refresh: bool,
    proxy: str | bool | None,
    strict_schema: bool,
) -> FetchResult[Any]:
    ops = [
        _symbol_op(
            client,
            symbol,
            statement,
            kind,
            periodicity,
            refresh=refresh,
            proxy=proxy,
            strict_schema=strict_schema,
        )
        for symbol in canonical
    ]
    # Immediate propagation: a child cancellation cancels the siblings and
    # re-raises CancelledError without awaiting their completion (gather with
    # return_exceptions=True only observes cancellation after ALL children
    # finish, so it is not usable here). Reaping is non-blocking: done
    # callbacks consume each task's outcome so the loop never reports
    # "exception was never retrieved".
    tasks = [asyncio.ensure_future(op()) for op in ops]

    def _consume(task: asyncio.Future[Any]) -> None:
        if not task.cancelled():
            task.exception()

    for task in tasks:
        task.add_done_callback(_consume)
    pending: set[asyncio.Future[Any]] = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if any(task.cancelled() for task in done):
                # A child was cancelled: cancel the siblings and propagate
                # immediately — their in-flight work is never awaited; the
                # done callbacks reap their outcomes in the background.
                for other in pending:
                    other.cancel()
                raise asyncio.CancelledError()
    except BaseException:
        # The batch itself was cancelled (or a child cancellation surfaced):
        # cancel every still-running child and propagate without waiting.
        for task in tasks:
            if not task.done():
                task.cancel()
        raise

    succeeded: list[Any] = []
    failed: list[tuple[str, BaseException]] = []
    for symbol, task in zip(canonical, tasks, strict=True):
        exc = task.exception()
        if exc is not None:
            failed.append((symbol, exc))
        else:
            succeeded.append(task.result())

    unit_errors = tuple(
        UnitError(
            code="unit_failed",
            message=f"{type(exc).__name__}: {exc}",
            symbol=symbol,
        )
        for symbol, exc in failed
    )
    warnings = tuple(warning for result in succeeded for warning in result.metadata.warnings)

    def _combined(status: ResultStatus, succ: list, fail: list) -> FetchResult[Any]:
        tables = [result.data for result in succ]
        data = (
            _concat_tables(tables)
            if tables
            else fa.build_table("statements", [], fetched_at=dt.datetime.now(dt.UTC))
        )
        return FetchResult(
            data=data,
            metadata=ResultMetadata(
                endpoint=_ROUTE,
                status=status,
                access_tier=succ[0].metadata.access_tier if succ else AccessTier.UNKNOWN,
                fetched_at=max(r.metadata.fetched_at for r in succ)
                if succ
                else dt.datetime.now(dt.UTC),
                query={"s": statement, "symbols": list(canonical)},
                # One provenance record per original input position/spelling,
                # even when several spellings collapse onto one canonical fetch.
                symbols=tuple(records),
                requested_units=len(canonical),
                succeeded_units=len(succ),
                failed_units=len(fail),
                response_hash=succ[0].metadata.response_hash if len(succ) == 1 else None,
                unit_errors=unit_errors,
                warnings=warnings,
            ),
        )

    if not succeeded:
        raise FinvizBatchError(
            "all statement batch units failed",
            context={"statement": statement, "failed": [s for s, _ in failed]},
        )
    if failed:
        partial = _combined(ResultStatus.PARTIAL, succeeded, failed)
        if not allow_partial:
            raise FinvizPartialError(
                f"{len(failed)} of {len(canonical)} statement batch units failed",
                partial_result=partial,
            )
        return partial
    return _combined(ResultStatus.COMPLETE, succeeded, [])


def statements_batch(
    symbols: str | Iterable[str],
    *,
    statement: str = "IA",
    client: FinvizClient | None = None,
    allow_partial: bool = False,
    refresh: bool = False,
    proxy: str | bool | None = None,
    strict_schema: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`statements_batch_async`; fails inside a loop."""
    return run_sync(
        statements_batch_async(
            symbols,
            statement=statement,
            client=client,
            allow_partial=allow_partial,
            refresh=refresh,
            proxy=proxy,
            strict_schema=strict_schema,
        )
    )
