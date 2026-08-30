"""Bounded live smoke for the integrated public 0.4 surface (opt-in).

Run: uv run pytest -q tests/live -m live_public
One sequential request per 0.4 family against the canonical public
endpoints, proving current access and shape only. Never replaces fixtures;
never enumerates, retries aggressively, or escalates rate. Failures classify
access/network problems (typed FinvizError) separately from parse drift
(FinvizParseError), and known drift is skipped for review rather than failing
the pipeline. Artifact downloads are explicit and bounded (one small chart);
Elite export endpoints are never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import finvizp
from finvizp import (
    FinvizClient,
    FinvizError,
    FinvizParseError,
    ResultStatus,
    chart_descriptor,
    download_artifact_async,
    futures_async,
)
from finvizp.artifacts import DOWNLOAD_LIMIT
from finvizp.crypto import tiles_async as crypto_tiles_async
from finvizp.forex import tiles_async as forex_tiles_async

pytestmark = pytest.mark.live_public


async def _fetch(coro_factory, *, skip_parse_drift: bool = False):
    """Run one smoke request, classifying failures per the smoke contract."""
    try:
        return await coro_factory()
    except FinvizError as exc:
        if skip_parse_drift and isinstance(exc, FinvizParseError):
            pytest.skip(f"live parse drift, route for review: {exc}")
        pytest.skip(f"live access unavailable (network/transport): {exc}")


async def test_live_forex_tiles_bundle() -> None:
    async with FinvizClient() as client:
        result = await _fetch(lambda: forex_tiles_async(client=client), skip_parse_drift=True)
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        bundle = result.data
        assert bundle.rows
        first = bundle.rows[0]
        assert first.ticker
        # Honest temporal semantics: the sparkline stays a verbatim payload.
        assert first.sparkline_timestamps is None
        assert first.sparkline_interval_seconds is None


async def test_live_crypto_tiles_bundle() -> None:
    async with FinvizClient() as client:
        result = await _fetch(lambda: crypto_tiles_async(client=client), skip_parse_drift=True)
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        bundle = result.data
        assert bundle.rows
        assert bundle.rows[0].last > 0


async def test_live_futures_tiles_table() -> None:
    async with FinvizClient() as client:
        result = await _fetch(lambda: futures_async(client=client), skip_parse_drift=True)
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        table = result.table
        assert table.num_rows >= 5
        for name in ("symbol", "name", "category", "last", "change_percent", "delay_minutes"):
            assert name in table.column_names
        # Observation, not assertion: record the tile count.
        print(f"live: futures tiles = {table.num_rows}")


async def test_live_chart_artifact_download_is_bounded() -> None:
    descriptor = chart_descriptor("AAPL", timeframe="1d", fetched_at=datetime.now(UTC))
    # Descriptor construction is pure: no bytes, no hash, no path yet.
    assert descriptor.content_hash is None
    assert descriptor.content is None
    assert descriptor.path is None
    async with FinvizClient() as client:
        downloaded = await _fetch(
            lambda: download_artifact_async(descriptor, client=client),
            skip_parse_drift=True,
        )
    # Explicit download stamps the immutable descriptor with bounded bytes.
    assert downloaded.source_url == descriptor.source_url
    assert downloaded.content_length is not None
    assert downloaded.content_length <= DOWNLOAD_LIMIT
    assert downloaded.content_hash
    assert downloaded.content or downloaded.path


def test_smoke_contract() -> None:
    assert finvizp.__version__
    assert "chart_descriptor" in finvizp.__all__
    assert "download_artifact" in finvizp.__all__
    assert "download_artifact_async" in finvizp.__all__
    assert "futures" in finvizp.__all__
