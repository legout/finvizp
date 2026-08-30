"""Earnings screens: session-aware earnings-date projections over the shared collector.

Earnings are a projection over :func:`finvizp.screener.screen_async`, never a
second transport: the earnings ``when``/``session`` selection composes a
registry option of the ``Earnings Date`` filter (so every combination is
validated against checked-in provider evidence before any network I/O), the
custom view requests the ``Earnings Date`` column, and the shared screener
collector paginates it exactly like any other screen.

Provider displays split into three separately-typed fields:

- ``earnings_date``: Arrow ``date32`` — the provider's own date, no clock
  time, no timezone shift;
- ``earnings_date_raw``: the exact display (``Nov 12 BMO``);
- ``earnings_session``: ``BMO``/``AMC`` only when the provider says so —
  session labels are provider evidence and are never invented from clock
  time; date-only displays leave the session null.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import replace
from typing import Any

import pyarrow as pa

from finvizp._queries.screener import CustomColumns, Filter, Order, ScreenerQuery, screener_registry
from finvizp._sync import run_sync
from finvizp.client import FinvizClient
from finvizp.errors import FinvizParseError, FinvizQueryError
from finvizp.results import FetchResult, ResultStatus
from finvizp.screener import ProgressCallback, _field_name, screen_async

__all__ = ["earnings_async", "earnings_options", "earnings_screen"]

_EARNINGS_DATE_LABEL = "Earnings Date"
# The provider's grid header for custom-column code 68 is ``Earnings``, while
# the registry/contract name is ``Earnings Date`` (code identical).
_EARNINGS_GRID_LABELS = ("Earnings Date", "Earnings")
_WHEN_DAY_WINDOWS = ("Today", "Tomorrow", "Yesterday")
_WHEN_WINDOWS = (*_WHEN_DAY_WINDOWS, "This Week", "Next Week", "This Month")
_SESSIONS = {"Before Market Open": "BMO", "After Market Close": "AMC"}
_DATE_DISPLAY = re.compile(
    r"^(?P<month>[A-Za-z]{3,})\s+(?P<day>\d{1,2})(?:,?\s*(?P<year>\d{4}))?"
    r"(?:\s+(?P<session>BMO|AMC)|/(?P<compact_session>[ab]))?$"
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


def earnings_options() -> Any:
    """Return ``(when, session=None) -> registry option name`` callables.

    The mapping is derived from the checked-in registry, so the reviewed
    ``when``/``session`` vocabulary can never drift from provider codes:

    - ``session=None`` -> the option named exactly ``when`` (``Today``,
      ``This Week``, ...);
    - ``session`` given -> ``<when> <session>`` (``Today Before Market
      Open``, ``Tomorrow After Market Close``, ...).
    """

    def compose(when: str, session: str | None = None) -> str:
        return when if session is None else f"{when} {session}"

    return compose


def _option_for(when: str | None, session: str | None) -> str:
    """Validate ``when``/``session`` against the registry; return the option name.

    Raises :class:`FinvizQueryError` for any combination the checked-in
    registry does not carry — before any network I/O.
    """
    options = {
        option.name: option.code
        for option in screener_registry().filters[_EARNINGS_DATE_LABEL].options
    }
    if when is not None and when not in options:
        valid = ", ".join(_WHEN_WINDOWS)
        msg = (
            f"earnings when must be one of {valid}; {when!r} is not a registry Earnings Date option"
        )
        raise FinvizQueryError(msg)
    if session is not None:
        if session not in _SESSIONS:
            msg = f"earnings session must be one of {sorted(_SESSIONS)}, got {session!r}"
            raise FinvizQueryError(msg)
        if when is None:
            msg = "earnings session requires a when window"
            raise FinvizQueryError(msg)
        if when not in _WHEN_DAY_WINDOWS:
            msg = (
                "session screens require a day when window "
                f"({', '.join(_WHEN_DAY_WINDOWS)}), got {when!r}"
            )
            raise FinvizQueryError(msg)
        composed = f"{when} {session}"
        if composed not in options:
            msg = f"the checked-in registry carries no {composed!r} Earnings Date option"
            raise FinvizQueryError(msg)
        return composed
    if when is None:
        msg = "earnings screens need a when window or explicit names"
        raise FinvizQueryError(msg)
    return when


def _earnings_query(
    *,
    when: str | None,
    session: str | None,
    filters: tuple[Filter, ...] | None = None,
    order: Order | None = None,
) -> ScreenerQuery | None:
    """Build the validated screener query for one earnings screen.

    ``None`` when no date window is requested (names-only screens carry no
    ``Earnings Date`` filter). ``Ticker`` is always requested: the provider
    renders exactly the requested custom columns, and without a ticker cell
    the grid's positional contract cannot name a row.
    """
    if when is None and session is None:
        return None
    earnings_filter = Filter(
        name=_EARNINGS_DATE_LABEL,
        option=_option_for(when, session),
    )
    return ScreenerQuery(
        view="custom",
        filters=(earnings_filter, *(filters or ())),
        order=order,
        columns=CustomColumns(("Ticker", _EARNINGS_DATE_LABEL)),
    )


def _normalize_names(names: str | list[str] | tuple[str, ...] | None) -> str | None:
    if names is None:
        return None
    if isinstance(names, str):
        return names
    if isinstance(names, (list, tuple)) and len(names) == 1:
        return names[0]
    msg = "earnings queries take one ticker; pass a string or a one-name sequence"
    raise FinvizQueryError(msg)


def _session_of(raw: str) -> str | None:
    """``BMO``/``AMC`` only when the provider display says so.

    Two provider shapes carry a session: the verbose ``Nov 12 BMO`` suffix and
    the compact grid suffix ``Aug 26/a`` / ``Aug 26/b`` (``/a`` = AMC, ``/b``
    = BMO, verified against quote pages 2026-08-30). A display with neither
    suffix leaves the session null — sessions are never invented from clock
    time.
    """
    match = _DATE_DISPLAY.match(raw)
    if match is None:
        return None
    if match["session"]:
        return match["session"]
    if match["compact_session"] == "a":
        return "AMC"
    if match["compact_session"] == "b":
        return "BMO"
    return None


def _normalize_date(raw: str, *, row_key: str) -> dt.date:
    """``Nov 12``/``Nov 12, 2026``/``Nov 12 BMO`` -> the provider's own date."""
    match = _DATE_DISPLAY.match(raw)
    if match is None:
        msg = f"cannot parse earnings date display for {row_key}"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    month = _MONTHS.get(match["month"][:3].lower())
    day = int(match["day"])
    if month is None or not 1 <= day <= 31:
        msg = f"cannot parse earnings date display for {row_key}"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    if match["year"]:
        return dt.date(int(match["year"]), month, day)
    # ponytail: provider month/day displays anchor to the fetch date's year;
    # a proper America/New_York anchor + ZoneInfo would be stricter if drift shows.
    anchor = dt.datetime.now(dt.UTC).astimezone(dt.timezone(dt.timedelta(hours=-5))).date()
    candidate = dt.date(anchor.year, month, day)
    if (candidate - anchor).days > 180:
        candidate = candidate.replace(year=anchor.year - 1)
    elif (anchor - candidate).days > 180:
        candidate = candidate.replace(year=anchor.year + 1)
    return candidate


def _earnings_table(combined: Any) -> Any:
    """Project the combined screen table into the session-aware earnings schema."""
    source_names = combined.column_names
    # The date column rides under the contract field name (``earnings_date``,
    # from the registry label) or the provider's own grid label (``Earnings``).
    date_field = next(
        (
            _field_name(label)
            for label in _EARNINGS_GRID_LABELS
            if _field_name(label) in source_names
        ),
        None,
    )
    missing = [
        name
        for name in ("rank", "symbol", date_field or _EARNINGS_DATE_LABEL)
        if name not in source_names
    ]
    if missing:
        msg = f"earnings screen table is missing columns: {', '.join(missing)}"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    ranks = combined.column("rank").to_pylist()
    symbols = combined.column("symbol").to_pylist()
    raws = combined.column(date_field).to_pylist()
    dates: list[dt.date | None] = []
    raw_values: list[str] = []
    sessions: list[str | None] = []
    for rank, symbol, raw in zip(ranks, symbols, raws, strict=True):
        raw_text = raw if isinstance(raw, str) else ""
        raw_values.append(raw_text)
        sessions.append(_session_of(raw_text))
        dates.append(
            None if raw_text == "" else _normalize_date(raw_text, row_key=f"rank {rank} ({symbol})")
        )
    carried = [
        field
        for field in combined.schema
        if field.name not in ("rank", "symbol", date_field, "fetched_at", "extra_fields")
    ]
    schema = pa.schema(
        [
            pa.field("rank", pa.int64(), nullable=False),
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("earnings_date", pa.date32()),
            pa.field("earnings_date_raw", pa.string()),
            pa.field("earnings_session", pa.string()),
            *carried,
            combined.schema.field("fetched_at"),
            combined.schema.field("extra_fields"),
        ]
    )
    arrays = [
        pa.array(ranks, type=schema.field("rank").type),
        pa.array(symbols, type=schema.field("symbol").type),
        pa.array(dates, type=schema.field("earnings_date").type),
        pa.array(raw_values, type=schema.field("earnings_date_raw").type),
        pa.array(sessions, type=schema.field("earnings_session").type),
    ]
    arrays.extend(combined.column(field.name).combine_chunks() for field in carried)
    arrays.append(combined.column("fetched_at").combine_chunks())
    arrays.append(combined.column("extra_fields").combine_chunks())
    return pa.Table.from_arrays(arrays, schema=schema)


async def earnings_async(
    *,
    when: str | None = None,
    session: str | None = None,
    names: str | list[str] | tuple[str, ...] | None = None,
    filters: Filter | list[Filter] | tuple[Filter, ...] | None = None,
    order: Order | None = None,
    client: FinvizClient,
    allow_partial: bool = False,
    max_pages: int | None = None,
    max_rows: int | None = None,
    refresh: bool = False,
    cache: bool = True,
    on_progress: ProgressCallback | None = None,
) -> FetchResult[Any]:
    """Fetch one earnings screen and project session-aware earnings dates.

    ``when`` is a checked-in registry ``Earnings Date`` window (``Today``,
    ``Tomorrow``, ``Yesterday``, ``This Week``, ``Next Week``, ``This
    Month``); ``session`` restricts a day window to before market open
    (``BMO``) or after market close (``AMC``). Any combination the registry
    does not carry is rejected before any network I/O. ``names`` scopes the
    same screen to one explicit ticker (one ticker per query — call per
    name for several). Collector, pagination, cache, and provenance are the
    shared screener's own; the combined table is projected into
    ``earnings_date`` (date32), ``earnings_date_raw``, and
    ``earnings_session``.
    """
    if when is None and session is None and names is None:
        msg = "earnings screens need a when window or explicit names"
        raise FinvizQueryError(msg)
    query = _earnings_query(
        when=when,
        session=session,
        filters=tuple(filters) if isinstance(filters, (list, tuple)) else None,
        order=order,
    )
    if query is None:
        # Names-only screen: one ticker, no date window, same minimal columns.
        query = ScreenerQuery(
            view="custom",
            filters=tuple(filters) if isinstance(filters, (list, tuple)) else (),
            order=order,
            columns=CustomColumns(("Ticker", _EARNINGS_DATE_LABEL)),
        )
    ticker = _normalize_names(names)
    if ticker is not None:
        query = replace(query, ticker=ticker)
    result = await screen_async(
        query,
        client=client,
        allow_partial=allow_partial,
        max_pages=max_pages,
        max_rows=max_rows,
        refresh=refresh,
        cache=cache,
        on_progress=on_progress,
    )
    if result.metadata.status is not ResultStatus.EMPTY:
        table = _earnings_table(result.table)
    else:
        table = result.table
    return FetchResult(
        table,
        replace(result.metadata, projected_from="screener"),
    )


def earnings_screen(
    names: str | list[str] | tuple[str, ...] | None = None,
    *,
    when: str | None = None,
    session: str | None = None,
    filters: Filter | list[Filter] | tuple[Filter, ...] | None = None,
    order: Order | None = None,
    client: FinvizClient,
    allow_partial: bool = False,
    max_pages: int | None = None,
    max_rows: int | None = None,
    refresh: bool = False,
    cache: bool = True,
    on_progress: ProgressCallback | None = None,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`earnings_async`; rejects an active event loop."""
    return run_sync(
        earnings_async(
            when=when,
            session=session,
            names=names,
            filters=filters,
            order=order,
            client=client,
            allow_partial=allow_partial,
            max_pages=max_pages,
            max_rows=max_rows,
            refresh=refresh,
            cache=cache,
            on_progress=on_progress,
        )
    )


# Every public async operation carries a sync twin with the async suffix
# stripped (curated-export contract); ``earnings_screen`` is the descriptive
# spelling of the same wrapper, ``earnings`` its pair-form name.
earnings = earnings_screen
__all__ = [*__all__, "earnings"]
