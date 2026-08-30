"""Bounded live smoke for the integrated public 0.1 surface (opt-in).

Run: uv run pytest -q tests/live -m live_public
One sequential request per 0.1 family against the canonical public
endpoints, proving current access and shape only. Never replaces fixtures;
never enumerates, retries aggressively, or escalates rate. Failures classify
access/network problems (typed FinvizError) separately from parse drift
(FinvizParseError), and known drift is skipped for review rather than failing
the pipeline.
"""

from __future__ import annotations

import pytest

import finvizp
from finvizp import (
    FinvizClient,
    ResultStatus,
    quote_async,
    search_symbols_async,
    statements_async,
    symbols_async,
)
from finvizp.quote import ratings_async
from tests.live._smoke import fetch

pytestmark = pytest.mark.live_public


async def test_live_symbols_universe_is_one_manifest_request() -> None:
    async with FinvizClient() as client:
        result = await fetch(lambda: symbols_async(client=client))
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    table = result.table
    assert table.column_names[0] == "symbol"
    # Observation, not assertion: record the count, never crawl entries.
    print(f"live: symbol universe rows = {table.num_rows}")


async def test_live_symbol_search_bounded() -> None:
    async with FinvizClient() as client:
        result = await fetch(lambda: search_symbols_async("AAPL", client=client))
    table = result.table
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    # Bounded provider ranking: observed maximum is ten matches.
    assert table.num_rows <= 10
    assert all(table.column("symbol").to_pylist())


async def test_live_statements_income_annual() -> None:
    async with FinvizClient() as client:
        result = await fetch(lambda: statements_async("AAPL", statement="IA", client=client))
    table = result.table
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        assert set(table.column("statement_kind").to_pylist()) == {"income"}
        assert set(table.column("periodicity").to_pylist()) == {"annual"}
        assert all(table.column("currency").to_pylist())


async def test_live_quote_bundle_and_cached_projection() -> None:
    async with FinvizClient(cache_ttl=300.0) as client:
        result = await fetch(lambda: quote_async("AAPL", client=client), skip_drift=True)
        bundles = result.data
        assert bundles and bundles[0].symbol == "AAPL"
        ratings = await fetch(lambda: ratings_async("AAPL", client=client), skip_drift=True)
    assert ratings.metadata.projected_from == "quote"


async def test_live_recognized_empty_search() -> None:
    async with FinvizClient() as client:
        result = await fetch(lambda: search_symbols_async("ZZZZZZ", client=client))
    # Recognized empty result, not drift and not a crash.
    assert result.metadata.status is ResultStatus.EMPTY


def test_smoke_contract() -> None:
    assert finvizp.__version__
