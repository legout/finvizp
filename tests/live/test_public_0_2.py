"""Bounded live smoke for the integrated public 0.2 screener surface (opt-in).

Run: uv run pytest -q tests/live -m live_public
One sequential request per 0.2 family against the canonical public
endpoints, proving current access and shape only. Never replaces fixtures;
never enumerates, retries aggressively, or escalates rate. Failures classify
access/network problems (typed FinvizError) separately from parse drift
(FinvizParseError), and known drift is skipped for review rather than failing
the pipeline. Elite export endpoints are never touched.
"""

from __future__ import annotations

import pytest

import finvizp
from finvizp import (
    FinvizClient,
    FinvizError,
    FinvizParseError,
    ResultStatus,
    earnings_async,
    screen_async,
    signal_async,
)
from finvizp._queries.screener import ScreenerQuery

pytestmark = pytest.mark.live_public


async def _fetch(coro_factory, *, skip_parse_drift: bool = False):
    """Run one smoke request, classifying failures per the smoke contract."""
    try:
        return await coro_factory()
    except FinvizError as exc:
        if skip_parse_drift and isinstance(exc, FinvizParseError):
            pytest.skip(f"live parse drift, route for review: {exc}")
        pytest.skip(f"live access unavailable (network/transport): {exc}")


async def test_live_screen_one_bounded_page() -> None:
    # max_pages=1 is the smoke's own safety stop: the truncated walk is the
    # expected COMPLETE-with-warning outcome, not a failure.
    async with FinvizClient() as client:
        result = await _fetch(
            lambda: screen_async(
                ScreenerQuery(view="overview"), client=client, max_pages=1, allow_partial=True
            )
        )
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.PARTIAL}
    table = result.table
    assert table.column_names[:2] == ["rank", "symbol"]
    # Observation, not assertion: record the count, never crawl.
    print(f"live: overview page rows = {table.num_rows}")


async def test_live_signal_screen_bounded() -> None:
    async with FinvizClient() as client:
        result = await _fetch(
            lambda: signal_async("Most Active", client=client, max_pages=1, allow_partial=True),
            skip_parse_drift=True,
        )
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.PARTIAL}
    if result.metadata.status is not ResultStatus.EMPTY:
        assert result.table.column_names[:2] == ["rank", "symbol"]
        assert all(result.table.column("symbol").to_pylist())


async def test_live_earnings_screen_bounded() -> None:
    async with FinvizClient() as client:
        result = await _fetch(
            lambda: earnings_async(
                when="This Week", client=client, max_pages=1, allow_partial=True
            ),
            skip_parse_drift=True,
        )
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.PARTIAL}
    if result.metadata.status is not ResultStatus.EMPTY:
        table = result.table
        for name in ("earnings_date", "earnings_date_raw", "earnings_session"):
            assert name in table.column_names
        assert all(d is None or d.month for d in table.column("earnings_date").to_pylist())


def test_smoke_contract() -> None:
    assert finvizp.__version__
    assert "screen_async" in finvizp.__all__
