"""Bounded live smoke for the integrated public 0.5 surface (opt-in).

Run: uv run pytest -q tests/live -m live_public
One sequential request per 0.5 public family — the three 0.3/0.4-detail
operations without a family smoke yet (publisher news, fund disclosure,
release detail) plus the forex/crypto performance tables routed here by the
0.4 audit (the performance-table parser has no other live coverage). Exactly
one explicit slug each: sitemaps are never enumerated. Proves current access
and shape only; never replaces fixtures. Failures classify through
``tests/live/_smoke.py`` instead of collapsing into one red build.
"""

from __future__ import annotations

import pytest

from finvizp import FinvizClient, ResultStatus
from finvizp.calendar import calendar_detail_async
from finvizp.crypto import performance_async as crypto_performance_async
from finvizp.forex import performance_async as forex_performance_async
from finvizp.insider import fund_insider_async
from finvizp.news import publisher_news_async
from tests.live._smoke import fetch

pytestmark = pytest.mark.live_public


async def test_live_publisher_news_single_slug() -> None:
    # One explicit provider slug (evidence: ``zacks`` in the 25-slug news
    # sitemap); never a crawl of the publisher index.
    async with FinvizClient() as client:
        result = await fetch(lambda: publisher_news_async("zacks", client=client))
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        table = result.table
        assert table.column_names[:3] == ["title", "url", "publisher"]
        assert all(table.column("title").to_pylist())
        print(f"live: publisher 'zacks' items = {table.num_rows}")


async def test_live_fund_disclosure_single_slug() -> None:
    # One explicit provider sitemap slug form (``na-0000002230``).
    async with FinvizClient() as client:
        result = await fetch(lambda: fund_insider_async("na-0000002230", client=client))
    assert result.metadata.status is ResultStatus.COMPLETE
    payload = result.data
    assert "details" in payload
    filer = payload["details"]["filer"]
    assert filer.get("investorType") == "nport_fund"


async def test_live_release_detail_single_slug() -> None:
    # One explicit provider URL-form release slug (the live URL form, e.g.
    # ``USACSA``); the detail sitemap is never enumerated.
    async with FinvizClient() as client:
        result = await fetch(
            lambda: calendar_detail_async("USACSA", client=client), skip_drift=True
        )
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        table = result.table
        assert "release_date" in table.column_names
        assert "release_date_raw" in table.column_names
        print(f"live: 'USACPI' history rows = {table.num_rows}")


async def test_live_forex_performance_table() -> None:
    async with FinvizClient() as client:
        result = await fetch(lambda: forex_performance_async(client=client))
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        table = result.table
        assert table.column_names[:2] == ["rank", "pair"]
        # Every Perf column keeps its verbatim raw display companion.
        perf = [n for n in table.column_names if n.startswith("perf_") and not n.endswith("_raw")]
        assert perf and all(f"{name}_raw" in table.column_names for name in perf)


async def test_live_crypto_performance_table() -> None:
    async with FinvizClient() as client:
        result = await fetch(lambda: crypto_performance_async(client=client))
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        table = result.table
        assert table.column_names[:3] == ["rank", "ticker", "name"]
        assert "price" in table.column_names
        print(f"live: crypto performance rows = {table.num_rows}")


def test_smoke_contract() -> None:
    import finvizp

    assert finvizp.__version__
    assert "publisher_news_async" in finvizp.__all__
    assert "fund_insider_async" in finvizp.__all__
    assert "calendar_detail_async" in finvizp.__all__
