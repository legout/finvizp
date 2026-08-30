"""RED-first tests for structured forex data (Card 0.4-B, TDD step 1).

Every test fails until ``finvizp.forex`` and ``finvizp._parsers.markets``
exist. Hermetic: the transport double serves the scrubbed fixture pages
exactly as the verified one-request contract prescribes. No live HTTP, no
JavaScript execution. Sparkline payloads are preserved verbatim with honest
temporal semantics: the provider sends no per-point timestamps, so no
historical bars are ever inferred.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp.client import FinvizClient
from finvizp.errors import FinvizError, FinvizParseError, FinvizQueryError
from finvizp.results import AccessTier, ResultStatus

FIXTURES = Path(__file__).parent / "fixtures" / "markets"
_PERF = (FIXTURES / "forex-performance.html").read_text("utf-8")
_TILES = (FIXTURES / "forex-tiles.html").read_text("utf-8")
BASE = "https://finviz.com"


def _resp(body: str, path: str, *, kind: str = "text/html") -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=200,
        headers={"Content-Type": f"{kind}; charset=utf-8"},
        content=body.encode(),
        url=f"{BASE}{path}",
        is_json=False,
    )


class MarketTransport(Backend):
    """Serves the fixture forex pages; records every request for contracts."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    @property
    def name(self) -> str:
        return "forex-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls.append(config)
        path = urlsplit(str(config.url)).path
        if path == "/forex_performance.ashx":
            return _resp(_PERF, path)
        if path == "/forex.ashx":
            return _resp(_TILES, path)
        return _resp("not found", path, kind="text/html")

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> MarketTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _paths(fake: MarketTransport) -> list[str]:
    return [urlsplit(str(c.url)).path for c in fake.calls]


# --- performance table -----------------------------------------------------------


async def test_forex_performance_one_request_wide_table() -> None:
    from finvizp.forex import performance_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake) as client:
        result = await performance_async(client=client)
    assert _paths(fake) == ["/forex_performance.ashx"]
    assert result.metadata.endpoint == "/forex_performance.ashx"
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.access_tier is AccessTier.PUBLIC
    assert result.metadata.parser_version == "1"
    assert result.metadata.schema_version >= 1


async def test_forex_performance_semantic_columns_and_rows() -> None:
    from finvizp.forex import performance_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake) as client:
        result = await performance_async(client=client)
    table = result.table
    assert table.column_names == (
        [
            "rank",
            "pair",
            "price",
            "perf_5min",
            "perf_hour",
            "perf_day",
            "perf_week",
            "perf_month",
            "perf_quart",
            "perf_half",
            "perf_ytd",
            "perf_year",
            "price_raw",
            "perf_5min_raw",
            "perf_hour_raw",
            "perf_day_raw",
            "perf_week_raw",
            "perf_month_raw",
            "perf_quart_raw",
            "perf_half_raw",
            "perf_ytd_raw",
            "perf_year_raw",
            "fetched_at",
            "extra_fields",
        ]
    )
    data = table.to_pylist()
    assert len(data) == 10
    btc = data[0]
    assert btc["pair"] == "BTC/USD"
    assert btc["price"] == 78900.1
    # Percentages are decimal fractions (decision register).
    assert btc["perf_day"] == pytest.approx(0.0102)
    assert btc["perf_month"] == pytest.approx(0.2272)
    assert btc["price_raw"] == "78900.1000"
    assert btc["perf_day_raw"] == "1.02%"
    assert btc["extra_fields"] == []


async def test_forex_performance_pips_variant_is_raw_int_displays() -> None:
    from finvizp.forex import performance_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake) as client:
        result = await performance_async(client=client, change="PIPS")
    # Same page shape (the provider varies ordering server-side); PIPS views
    # keep the identical semantic schema — the raw displays carry pips units.
    assert result.table.column_names[0:3] == ["rank", "pair", "price"]


# --- tile bundle -----------------------------------------------------------------


async def test_forex_tiles_one_request_bundle() -> None:
    from finvizp.forex import tiles_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake) as client:
        result = await tiles_async(client=client)
    assert _paths(fake) == ["/forex.ashx"]
    assert result.metadata.status is ResultStatus.COMPLETE
    bundle = result.data
    assert bundle.rows[0].ticker == "AUDUSD"
    assert bundle.rows[0].label == "AUD/USD"
    assert bundle.rows[0].last == pytest.approx(0.71591)
    assert bundle.rows[0].change == pytest.approx(-0.0046)  # percent -> fraction
    assert bundle.rows[0].change_usd == pytest.approx(-0.0033)
    assert bundle.rows[0].prev_close == pytest.approx(0.71925)
    assert bundle.rows[0].high == pytest.approx(0.72079)
    assert bundle.rows[0].low == pytest.approx(0.71554)
    # Unknown tile fields survive as extra fields with the raw values.
    assert bundle.rows[0].extra_fields == {}
    assert bundle.access_tier is AccessTier.PUBLIC


async def test_forex_tiles_sparkline_verbatim_no_invented_history() -> None:
    from finvizp.forex import tiles_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake) as client:
        result = await tiles_async(client=client)
    row = result.data.rows[0]
    # The provider payload is preserved verbatim: no timestamps are inferred
    # and no historical bars are constructed from the points.
    assert row.sparkline == (
        [
            0.71953,
            0.71959,
            0.71961,
            0.71958,
            0.71956,
            0.71948,
            0.71949,
            0.71951,
            0.71947,
            0.71953,
        ]
    )
    assert row.sparkline_timestamps is None
    assert row.sparkline_interval_seconds is None


async def test_forex_tiles_unknown_field_lands_in_extra_fields() -> None:
    from finvizp._parsers.markets import parse_market_tiles
    from finvizp.forex import FOREX_TILE_EVENT

    body = _TILES.replace(
        '"prevClose":0.71925', '"prevClose":0.71925,"newField":"x"'
    )
    rows = parse_market_tiles(body, tile_event=FOREX_TILE_EVENT)
    assert rows[0].extra_fields == {"newField": "x"}


async def test_forex_tiles_malformed_embedded_json_is_typed_drift() -> None:
    from finvizp._parsers.markets import parse_market_tiles
    from finvizp.forex import FOREX_TILE_EVENT

    with pytest.raises(FinvizParseError, match="tile"):
        parse_market_tiles(
            _TILES.replace('"last":0.71591', '"last":broken'),
            tile_event=FOREX_TILE_EVENT,
        )


# --- chart descriptors -----------------------------------------------------------


async def test_forex_chart_descriptor_validates_grammar() -> None:
    from finvizp.forex import chart_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake) as client:
        with pytest.raises(FinvizQueryError, match="timeframe"):
            await chart_async(symbol="EURUSD", timeframe="nope", client=client)
    assert _paths(fake) == []  # rejected before any I/O


async def test_forex_chart_descriptor_carries_provider_image_url() -> None:
    from finvizp.forex import chart_async

    fake = ChartTransport()
    async with FinvizClient(transport=fake) as client:
        result = await chart_async(symbol="EURUSD", timeframe="d", client=client)
    descriptor = result.data
    assert descriptor.kind == "chart"
    assert descriptor.symbol == "EURUSD"
    assert descriptor.timeframe == "d"
    # The URL is the page's own embedded image host, taken verbatim — never
    # constructed locally, never fetched by this call.
    assert descriptor.source_url.startswith("https://charts2-node.finviz.com/chart?")
    assert "t=@eurusd" in descriptor.source_url
    assert result.metadata.status is ResultStatus.COMPLETE


class ChartTransport(MarketTransport):
    """Adds the verified forex charts gallery page."""

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls.append(config)
        path = urlsplit(str(config.url)).path
        if path == "/forex_charts.ashx":
            return _resp((FIXTURES / "forex-charts.html").read_text("utf-8"), path)
        return await super().request(config, stream_callback)


async def test_forex_chart_descriptor_from_gallery_page() -> None:
    from finvizp.forex import chart_async

    fake = ChartTransport()
    async with FinvizClient(transport=fake) as client:
        result = await chart_async(symbol="EURUSD", timeframe="d", client=client)
    assert _paths(fake) == ["/forex_charts.ashx"]
    assert result.data.symbol == "EURUSD"
    assert "t=@eurusd" in result.data.source_url


async def test_forex_chart_rejects_unknown_symbol_page() -> None:
    from finvizp.forex import chart_async

    class MissingTransport(ChartTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            return await super().request(config, stream_callback)

    fake = ChartTransport()
    async with FinvizClient(transport=fake) as client:
        with pytest.raises(FinvizError):
            await chart_async(symbol="ZZZZZZ", timeframe="d", client=client)


# --- empty and drift -------------------------------------------------------------


async def test_forex_performance_recognized_empty() -> None:
    from finvizp.forex import performance_async

    empty = (
        "<html><head><title>Forex Performance</title></head><body>"
        '<div id="forex_performance"><table class="groups_table"><thead>'
        "<tr><th>No.</th><th>Pair</th><th>Price</th></tr></thead></table></div>"
        "</body></html>"
    )

    class EmptyTransport(MarketTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            self.calls.append(config)
            path = urlsplit(str(config.url)).path
            if path == "/forex_performance.ashx":
                return _resp(empty, path)
            return _resp("not found", path, kind="text/html")

    fake = EmptyTransport()
    async with FinvizClient(transport=fake) as client:
        result = await performance_async(client=client)
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0


async def test_forex_performance_structural_drift_raises() -> None:
    from finvizp.forex import performance_async

    class DriftTransport(MarketTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            self.calls.append(config)
            path = urlsplit(str(config.url)).path
            if path == "/forex_performance.ashx":
                return _resp("<html><body>no table here</body></html>", path)
            return _resp("not found", path, kind="text/html")

    fake = DriftTransport()
    async with FinvizClient(transport=fake) as client:
        with pytest.raises(FinvizParseError, match="groups_table"):
            await performance_async(client=client)


async def test_forex_performance_missing_column_is_drift() -> None:
    from finvizp.forex import performance_async

    body = _PERF.replace("<th>Perf Year</th>", "<th>Renamed</th>")

    class DriftTransport(MarketTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            self.calls.append(config)
            path = urlsplit(str(config.url)).path
            if path == "/forex_performance.ashx":
                return _resp(body, path)
            return _resp("not found", path, kind="text/html")

    fake = DriftTransport()
    async with FinvizClient(transport=fake) as client:
        with pytest.raises(FinvizParseError, match="Renamed"):
            await performance_async(client=client)


# --- cache / sync ----------------------------------------------------------------


async def test_forex_performance_warm_call_replays_cache() -> None:
    from finvizp.forex import performance_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake, cache_ttl=60.0) as client:
        first = await performance_async(client=client)
        warm = await performance_async(client=client)
    assert _paths(fake) == ["/forex_performance.ashx"]
    assert warm.metadata.cache_hit is True
    assert first.table == warm.table


async def test_forex_performance_cache_false_bypasses() -> None:
    from finvizp.forex import performance_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake, cache_ttl=60.0) as client:
        await performance_async(client=client)
        again = await performance_async(client=client, cache=False)
    assert _paths(fake) == ["/forex_performance.ashx", "/forex_performance.ashx"]
    assert again.metadata.cache_hit is False


async def test_forex_tiles_refresh_replaces_cache() -> None:
    from finvizp.forex import tiles_async

    fake = MarketTransport()
    async with FinvizClient(transport=fake, cache_ttl=60.0) as client:
        await tiles_async(client=client)
        fresh = await tiles_async(client=client, refresh=True)
    assert _paths(fake) == ["/forex.ashx", "/forex.ashx"]
    assert fresh.metadata.cache_hit is False


def test_forex_sync_wrappers() -> None:
    from finvizp.forex import performance, tiles

    fake = MarketTransport()
    client = FinvizClient(transport=fake, retry_attempts=0, retry_backoff=0.0)
    try:
        assert performance(client=client).table.num_rows == 10
        assert tiles(client=client).data.rows[0].ticker == "AUDUSD"
    finally:
        asyncio.run(client.close())


def test_forex_sync_rejects_active_loop() -> None:
    from finvizp.forex import performance

    fake = MarketTransport()
    client = FinvizClient(transport=fake, retry_attempts=0, retry_backoff=0.0)

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="event loop"):
            performance(client=client)
        await client.close()

    asyncio.run(inside())


def test_forex_tile_fixture_json_parses() -> None:
    """The scrubbed fixture embeds exactly the verified tile shape."""
    from finvizp.forex import FOREX_TILE_EVENT

    from finvizp._parsers.markets import _tile_payload

    payload = _tile_payload(_TILES, tile_event=FOREX_TILE_EVENT)
    first = next(iter(payload.values()))
    assert set(first) >= {"label", "ticker", "last", "change", "prevClose", "sparkline"}
    assert json.dumps(payload)  # json-serializable evidence
