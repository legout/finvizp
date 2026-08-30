"""Bounded live smoke for the integrated public 0.3 surface (opt-in).

Run: uv run pytest -q tests/live -m live_public
One sequential request per 0.3 family against the canonical public
endpoints, proving current access and shape only. Never replaces fixtures;
never enumerates, retries aggressively, or escalates rate. Failures classify
access/network problems (typed FinvizError) separately from parse drift
(FinvizParseError), and known drift is skipped for review rather than failing
the pipeline. Elite export endpoints (/grp_export, screener export) are never
touched.
"""

from __future__ import annotations

import pytest

import finvizp
from finvizp import (
    FinvizClient,
    FinvizError,
    FinvizParseError,
    ResultStatus,
    calendar_async,
    global_insider_async,
    global_news_async,
    group_async,
    map_async,
)
from finvizp._queries.groups import GroupDimension, GroupQuery, GroupView

pytestmark = pytest.mark.live_public


async def _fetch(coro_factory, *, skip_parse_drift: bool = False):
    """Run one smoke request, classifying failures per the smoke contract."""
    try:
        return await coro_factory()
    except FinvizError as exc:
        if skip_parse_drift and isinstance(exc, FinvizParseError):
            pytest.skip(f"live parse drift, route for review: {exc}")
        pytest.skip(f"live access unavailable (network/transport): {exc}")


async def test_live_groups_overview_table() -> None:
    async with FinvizClient() as client:
        result = await _fetch(
            lambda: group_async(
                GroupQuery(dimension=GroupDimension.SECTOR, view=GroupView.OVERVIEW),
                client=client,
            ),
            skip_parse_drift=True,
        )
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        assert result.table.num_rows >= 5
        assert "name" in result.table.column_names


async def test_live_map_bundle_bounded() -> None:
    async with FinvizClient() as client:
        result = await _fetch(lambda: map_async(client=client), skip_parse_drift=True)
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        bundle = result.data
        assert bundle.symbol == "SP500"
        assert len(bundle.constituents) >= 400
        assert bundle.delay_minutes is not None


async def test_live_global_news_tables() -> None:
    async with FinvizClient() as client:
        result = await _fetch(lambda: global_news_async(client=client), skip_parse_drift=True)
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        tables = result.data
        assert set(tables) == {"news", "blogs"}
        columns = set(tables["news"].column_names)
        assert {"title", "url", "published_at_raw", "published_at_status"} <= columns


async def test_live_global_insider_single_window() -> None:
    async with FinvizClient() as client:
        result = await _fetch(lambda: global_insider_async(client=client), skip_parse_drift=True)
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        table = result.table
        # quote_insider contract columns.
        for name in ("symbol", "owner", "transaction_date", "value", "value_raw"):
            assert name in table.column_names


async def test_live_calendar_current_table() -> None:
    async with FinvizClient() as client:
        result = await _fetch(lambda: calendar_async(client=client), skip_parse_drift=True)
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        table = result.table
        for name in ("symbol", "event", "release_date", "release_date_raw"):
            assert name in table.column_names
        # Observation, not enumeration: record the window size.
        print(f"live: calendar entries = {table.num_rows}")


def test_smoke_contract() -> None:
    assert finvizp.__version__
    assert "group_async" in finvizp.__all__
    assert "map_async" in finvizp.__all__
    assert "calendar_async" in finvizp.__all__
