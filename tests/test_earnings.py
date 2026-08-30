"""Tests for earnings screens: session filters, projections, preflight, shared transport.

RED-first: fails until ``finvizp.earnings`` exists. Hermetic: the transport
double serves the scrubbed fixture pages; no live HTTP.

Registry evidence (checked-in ``screener_registry.json``, filter
``Earnings Date`` / ``earningsdate``): every ``when`` maps to a registry
option named exactly ``<when>``, and every ``session`` maps to
``<when><session>`` (Today Before Market Open, Tomorrow After Market Close,
...). Week/month ``when`` values have no session variants by evidence.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from finvizp._queries.screener import screener_registry
from finvizp.earnings import earnings_async, earnings_options, earnings_screen
from finvizp.errors import FinvizQueryError
from finvizp.results import ResultStatus
from finvizp.screener import SCREEN_PATH
from tests.test_screener import ScreenTransport, _client, _rs

WHEN_OPTIONS = ("Today", "Tomorrow", "Yesterday", "This Week", "Next Week", "This Month")
SESSION_OPTIONS = ("Before Market Open", "After Market Close")


# --- registry evidence (stale-count guard lives here, not in a hard-coded number) ---------


def test_every_when_and_session_maps_to_a_registry_option() -> None:
    compose = earnings_options()
    registry = screener_registry().filters["Earnings Date"].options
    by_name = {option.name for option in registry}
    for when in WHEN_OPTIONS:
        assert compose(when) in by_name
    for when in ("Today", "Tomorrow", "Yesterday"):
        for session in SESSION_OPTIONS:
            assert compose(when, session) in by_name


# --- query composition / preflight ----------------------------------------------------------


async def test_week_screen_encodes_registry_filter() -> None:
    fake = ScreenTransport(default=DATE_PAGE, total=2)
    await earnings_async(when="This Week", client=_client(fake))
    assert fake.calls[0]["f"] == "earningsdate_thisweek"


async def test_before_open_composes_session_option() -> None:
    fake = ScreenTransport(default=DATE_PAGE, total=2)
    await earnings_async(when="Tomorrow", session="Before Market Open", client=_client(fake))
    assert fake.calls[0]["f"] == "earningsdate_tomorrowbefore"


async def test_unknown_when_rejected_before_network() -> None:
    fake = ScreenTransport()
    with pytest.raises(FinvizQueryError, match="Earnings Date"):
        await earnings_async(when="Somehow", client=_client(fake))
    assert fake.calls == []


async def test_session_with_week_when_rejected_before_network() -> None:
    # Registry evidence: week/month windows have no BMO/AMC variants.
    fake = ScreenTransport()
    with pytest.raises(FinvizQueryError, match="session"):
        await earnings_async(when="This Week", session="Before Market Open", client=_client(fake))
    assert fake.calls == []


async def test_session_without_when_rejected_before_network() -> None:
    fake = ScreenTransport()
    with pytest.raises(FinvizQueryError, match="session"):
        await earnings_async(when=None, session="After Market Close", client=_client(fake))
    assert fake.calls == []


async def test_filters_require_when_or_names() -> None:
    fake = ScreenTransport()
    with pytest.raises(FinvizQueryError, match="when"):
        await earnings_async(when=None, names=None, client=_client(fake))
    assert fake.calls == []


async def test_extra_filters_and_order_flow_through_the_query() -> None:
    from finvizp._queries.screener import Filter, Order

    fake = ScreenTransport(default=DATE_PAGE, total=2)
    await earnings_async(
        when="Today",
        filters=[Filter(name="Market Cap.", option="+Large (over $10bln)")],
        order=Order(name="Market Cap.", descending=True),
        client=_client(fake),
    )
    f_value = fake.calls[0]["f"]
    assert f_value.startswith("earningsdate_today,") or f_value.endswith(",earningsdate_today")
    assert fake.calls[0]["o"] == "-marketcap"


# --- earnings projection ----------------------------------------------------------------------


async def test_earnings_columns_normalize_separately() -> None:
    fake = ScreenTransport(default=DATE_PAGE, total=2)
    result = await earnings_async(when="Today", client=_client(fake))
    table = result.table
    assert table.column_names == [
        "rank",
        "symbol",
        "earnings_date",
        "earnings_date_raw",
        "earnings_session",
        "fetched_at",
        "extra_fields",
    ]
    dates = table.column("earnings_date").to_pylist()
    assert all(isinstance(value, dt.date) for value in dates)
    assert table.column("earnings_date_raw").to_pylist() == [
        "Nov 12 BMO",
        "Nov 13 AMC",
    ]
    assert table.column("earnings_session").to_pylist() == ["BMO", "AMC"]


async def test_date_only_values_stay_date32_and_session_stays_null() -> None:
    fake = ScreenTransport(default=DATE_ONLY_PAGE, total=2)
    result = await earnings_async(when="Today", client=_client(fake))
    field = result.table.schema.field("earnings_date")
    assert str(field.type) == "date32[day]"
    assert result.table.column("earnings_session").null_count == 2
    assert result.table.column("earnings_date_raw").to_pylist() == ["Nov 12", "Nov 13"]


async def test_bmo_amc_sessions_are_provider_evidence_not_clock_time() -> None:
    # Session labels come only from the provider display suffix; the
    # date32 value is the provider's own date. No datetime, no tz shift.
    fake = ScreenTransport(default=DATE_PAGE, total=2)
    table = (await earnings_async(when="Today", client=_client(fake))).table
    sessions = table.column("earnings_session").to_pylist()
    dates = table.column("earnings_date").to_pylist()
    assert sessions == ["BMO", "AMC"]
    assert dates == [dt.date(2026, 11, 12), dt.date(2026, 11, 13)]


async def test_unparseable_date_display_preserves_raw_and_raises_strict() -> None:
    fake = ScreenTransport(default=BAD_DATE_PAGE, total=1)
    with pytest.raises(Exception, match="earnings date"):
        await earnings_async(when="Today", client=_client(fake))


# --- shared collector / provenance -----------------------------------------------------------


async def test_earnings_screen_rides_the_shared_screener_transport() -> None:
    fake = ScreenTransport(default=DATE_PAGE, total=2)
    result = await earnings_async(when="Today", client=_client(fake))
    assert _rs(fake) == [1]
    assert fake.calls[0]["v"] == "151"  # custom view, no duplicate earnings transport
    assert result.metadata.endpoint == SCREEN_PATH
    assert result.metadata.status is ResultStatus.COMPLETE


def test_earnings_names_screen_scopes_one_ticker() -> None:
    fake = ScreenTransport(default=DATE_PAGE, total=2)
    result = earnings_screen("AAPL", client=_client(fake))
    assert fake.calls[0]["t"] == "AAPL"
    assert result.table.num_rows == 2


def test_earnings_names_require_a_single_ticker() -> None:
    fake = ScreenTransport()

    async def inside() -> None:
        with pytest.raises(FinvizQueryError, match="one ticker"):
            await earnings_async(names=["AAPL", "MSFT"], when="This Week", client=_client(fake))

    asyncio.run(inside())
    assert fake.calls == []


# --- sync wrapper ------------------------------------------------------------------------------


def test_earnings_sync_wrapper_runs_outside_loop() -> None:
    fake = ScreenTransport(default=DATE_PAGE, total=2)
    result = earnings_screen("AAPL", client=_client(fake))
    assert result.table.num_rows == 2


def test_earnings_sync_wrapper_rejects_active_loop() -> None:
    fake = ScreenTransport(default=DATE_PAGE, total=2)

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            earnings_screen("AAPL", client=_client(fake))

    asyncio.run(inside())


# --- fixture-shaped pages (synthetic; header labels = provider contract) ------------------------


def _date_page(rows: list[tuple[str, str]]) -> str:
    """Custom-view page whose one data column renders ``Earnings Date`` displays."""
    from tests.fixtures.screener._build import _head, _table

    body = _table(
        ["No.", "Ticker", "Earnings Date"], [(i + 1, t, [d]) for i, (t, d) in enumerate(rows)]
    )
    return _head(len(rows), 1) + body + "</div></body></html>"


DATE_PAGE = _date_page(
    [
        ("AAPL", "Nov 12 BMO"),
        ("MSFT", "Nov 13 AMC"),
    ]
)
DATE_ONLY_PAGE = _date_page(
    [
        ("AAPL", "Nov 12"),
        ("MSFT", "Nov 13"),
    ]
)
BAD_DATE_PAGE = _date_page([("AAPL", "not a date")])
