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

from finvizp import FinvizClient, ResultStatus
from finvizp.groups import GroupDimension, GroupQuery, GroupView, group_async, spectrum_async
from tests.live._smoke import fetch

pytestmark = pytest.mark.live_public


async def test_live_groups_overview_table() -> None:
    async with FinvizClient() as client:
        query = GroupQuery(dimension=GroupDimension.SECTOR, view=GroupView.OVERVIEW)
        result = await fetch(lambda: group_async(query, client=client))
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    table = result.table
    if result.metadata.status is ResultStatus.COMPLETE:
        assert table.num_rows >= 5
        names = table.column("name").to_pylist()
        assert "Technology" in names


async def test_live_groups_spectrum_descriptor() -> None:
    async with FinvizClient() as client:
        result = await fetch(
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
        result = await fetch(
            lambda: group_async(
                GroupQuery(dimension=GroupDimension.INDUSTRY, view=GroupView.OVERVIEW),
                client=client,
            )
        )
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.table.num_rows >= 100
