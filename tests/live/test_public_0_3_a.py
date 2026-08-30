"""Bounded live smoke for the public 0.3 groups surface (opt-in).

Run: uv run pytest -q tests/live -m live_public
One sequential request per 0.3-A family against the canonical public
endpoints, proving current access and shape only. Never replaces fixtures;
never enumerates, retries aggressively, or escalates rate. Failures classify
access/network problems (typed FinvizError) separately from parse drift
(FinvizParseError).
"""

from __future__ import annotations

import pytest

from finvizp import FinvizClient, FinvizError, FinvizParseError, ResultStatus
from finvizp.groups import GroupDimension, GroupQuery, GroupView, group_async, spectrum_async

pytestmark = pytest.mark.live_public


def _skip_offline(exc: FinvizError) -> pytest.SkipTest:
    """Classify access/network problems as skips, never as parse drift."""
    return pytest.SkipTest(f"live access unavailable (network/transport): {exc}")


async def _fetch(coro_factory, *, skip_parse_drift: bool = False):
    """Run one smoke request, classifying failures per the smoke contract."""
    try:
        return await coro_factory()
    except FinvizError as exc:
        if skip_parse_drift and isinstance(exc, FinvizParseError):
            pytest.skip(f"live parse drift, route for review: {exc}")
        raise _skip_offline(exc) from exc


async def test_live_groups_overview_table() -> None:
    async with FinvizClient() as client:
        query = GroupQuery(dimension=GroupDimension.SECTOR, view=GroupView.OVERVIEW)
        result = await _fetch(lambda: group_async(query, client=client))
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    table = result.table
    if result.metadata.status is ResultStatus.COMPLETE:
        assert table.num_rows >= 5
        names = table.column("name").to_pylist()
        assert "Technology" in names


async def test_live_groups_spectrum_descriptor() -> None:
    async with FinvizClient() as client:
        result = await _fetch(
            lambda: spectrum_async(
                GroupQuery(dimension=GroupDimension.SECTOR, view=GroupView.SPECTRUM),
                client=client,
            )
        )
    artifact = result.data
    assert artifact.source_url
    assert artifact.kind == "image"
    assert artifact.group == "sector"


async def test_live_groups_industry_dimension() -> None:
    async with FinvizClient() as client:
        result = await _fetch(
            lambda: group_async(
                GroupQuery(dimension=GroupDimension.INDUSTRY, view=GroupView.OVERVIEW),
                client=client,
            )
        )
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.table.num_rows >= 100
