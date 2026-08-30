"""RED-first tests for structured crypto data (Card 0.4-B, TDD step 1).

Mirrors the forex contract with the crypto surface's own verified shape:
the performance table carries a Ticker and Name column, the tile payload
event is ``FinvizInitCrypto``, and chart descriptors resolve from the
crypto charts gallery. Hermetic only; no live HTTP, no JS execution.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp.client import FinvizClient
from finvizp.errors import FinvizError, FinvizParseError, FinvizQueryError
from finvizp.results import AccessTier, ResultStatus

FIXTURES = Path(__file__).parent / "fixtures" / "markets"
_PERF = (FIXTURES / "crypto-performance.html").read_text("utf-8")
_TILES = (FIXTURES / "crypto-tiles.html").read_text("utf-8")
BASE = "https://finviz.com"


def _resp(body: str, path: str, *, kind: str = "text/html") -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=200,
        headers={"Content-Type": f"{kind}; charset=utf-8"},
        content=body.encode(),
        url=f"{BASE}{path}",
        is_json=False,
    )


class CryptoTransport(Backend):
    """Serves the fixture crypto pages; records every request for contracts."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    @property
    def name(self) -> str:
        return "crypto-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls.append(config)
        path = urlsplit(str(config.url)).path
        if path == "/crypto_performance.ashx":
            return _resp(_PERF, path)
        if path == "/crypto.ashx":
            return _resp(_TILES, path)
        if path == "/crypto_charts.ashx":
            return _resp(
                (FIXTURES / "crypto-charts.html").read_text("utf-8"),
                path,
            )
        return _resp("not found", path, kind="text/html")

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> CryptoTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _paths(fake: CryptoTransport) -> list[str]:
    return [urlsplit(str(c.url)).path for c in fake.calls]


# --- performance table -----------------------------------------------------------


async def test_crypto_performance_one_request_wide_table() -> None:
    from finvizp.crypto import performance_async

    fake = CryptoTransport()
    async with FinvizClient(transport=fake) as client:
        result = await performance_async(client=client)
    assert _paths(fake) == ["/crypto_performance.ashx"]
    assert result.metadata.endpoint == "/crypto_performance.ashx"
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.access_tier is AccessTier.PUBLIC
    table = result.table
    assert table.column_names[0:4] == ["rank", "ticker", "name", "price"]
    assert "perf_year" in table.column_names
    assert "perf_year_raw" in table.column_names
    data = table.to_pylist()
    assert len(data) == 31
    uni = data[0]
    assert uni["ticker"] == "UNI"
    assert uni["name"] == "Uniswap"
    assert uni["price"] == pytest.approx(5.398)
    assert uni["perf_day"] == pytest.approx(0.101)
    assert uni["perf_year_raw"] == "-45.55%"


# --- tile bundle -----------------------------------------------------------------


async def test_crypto_tiles_bundle_semantics() -> None:
    from finvizp.crypto import tiles_async

    fake = CryptoTransport()
    async with FinvizClient(transport=fake) as client:
        result = await tiles_async(client=client)
    assert _paths(fake) == ["/crypto.ashx"]
    assert result.metadata.status is ResultStatus.COMPLETE
    bundle = result.data
    aave = next(row for row in bundle.rows if row.ticker == "AAVEUSD")
    assert aave.label == "AAVE/USD"
    assert aave.last == pytest.approx(128.16)
    assert aave.change == pytest.approx(0.0179)  # percent -> fraction
    assert aave.change_usd == pytest.approx(2.25)
    assert aave.prev_close == pytest.approx(125.91)
    assert aave.high == pytest.approx(130.01)
    assert aave.low == pytest.approx(123.23)
    assert len(aave.sparkline) == 10  # first ten verbatim points in the fixture
    assert aave.sparkline_timestamps is None  # no provider timestamps: no history
    assert len(bundle.rows) == 31


# --- chart descriptors -----------------------------------------------------------


async def test_crypto_chart_descriptor_validates_grammar() -> None:
    from finvizp.crypto import chart_async

    fake = CryptoTransport()
    async with FinvizClient(transport=fake) as client:
        with pytest.raises(FinvizQueryError, match="timeframe"):
            await chart_async(symbol="BTCUSD", timeframe="nope", client=client)
    assert _paths(fake) == []  # rejected before any I/O


async def test_crypto_chart_descriptor_carries_provider_image_url() -> None:
    from finvizp.crypto import chart_async

    fake = CryptoTransport()
    async with FinvizClient(transport=fake) as client:
        result = await chart_async(symbol="BTCUSD", timeframe="d", client=client)
    descriptor = result.data
    assert descriptor.kind == "chart"
    assert descriptor.symbol == "BTCUSD"
    assert descriptor.timeframe == "d"
    assert descriptor.source_url.startswith("https://charts2-node.finviz.com/chart?")
    assert "t=@btcusd" in descriptor.source_url
    assert result.metadata.status is ResultStatus.COMPLETE


async def test_crypto_chart_rejects_unknown_symbol() -> None:
    from finvizp.crypto import chart_async

    fake = CryptoTransport()
    async with FinvizClient(transport=fake) as client:
        with pytest.raises(FinvizError):
            await chart_async(symbol="ZZZZZZ", timeframe="d", client=client)


# --- empty and drift -------------------------------------------------------------


async def test_crypto_performance_recognized_empty() -> None:
    from finvizp.crypto import performance_async

    empty = (
        "<html><head><title>Crypto Performance</title></head><body>"
        '<div id="crypto_performance"><table class="groups_table"><thead>'
        "<tr><th>No.</th><th>Ticker</th><th>Name</th><th>Price</th></tr></thead>"
        "</table></div></body></html>"
    )

    class EmptyTransport(CryptoTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            self.calls.append(config)
            path = urlsplit(str(config.url)).path
            if path == "/crypto_performance.ashx":
                return _resp(empty, path)
            return _resp("not found", path, kind="text/html")

    fake = EmptyTransport()
    async with FinvizClient(transport=fake) as client:
        result = await performance_async(client=client)
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0


async def test_crypto_tiles_malformed_embedded_json_is_typed_drift() -> None:
    from finvizp._parsers.markets import parse_market_tiles
    from finvizp.crypto import CRYPTO_TILE_EVENT

    with pytest.raises(FinvizParseError, match="tile"):
        parse_market_tiles(
            _TILES.replace('"last":128.16', '"last":broken'),
            tile_event=CRYPTO_TILE_EVENT,
        )


# --- cache / sync ----------------------------------------------------------------


async def test_crypto_tiles_warm_call_replays_cache() -> None:
    from finvizp.crypto import tiles_async

    fake = CryptoTransport()
    async with FinvizClient(transport=fake, cache_ttl=60.0) as client:
        first = await tiles_async(client=client)
        warm = await tiles_async(client=client)
    assert _paths(fake) == ["/crypto.ashx"]
    assert warm.metadata.cache_hit is True
    assert first.data == warm.data


async def test_crypto_performance_cache_false_bypasses() -> None:
    from finvizp.crypto import performance_async

    fake = CryptoTransport()
    async with FinvizClient(transport=fake, cache_ttl=60.0) as client:
        await performance_async(client=client)
        again = await performance_async(client=client, cache=False)
    assert _paths(fake) == ["/crypto_performance.ashx", "/crypto_performance.ashx"]
    assert again.metadata.cache_hit is False


def test_crypto_sync_wrappers() -> None:
    from finvizp.crypto import performance, tiles

    fake = CryptoTransport()
    client = FinvizClient(transport=fake, retry_attempts=0, retry_backoff=0.0)
    try:
        assert performance(client=client).table.num_rows == 31
        assert tiles(client=client).data.rows
    finally:
        asyncio.run(client.close())


def test_crypto_sync_rejects_active_loop() -> None:
    from finvizp.crypto import performance

    fake = CryptoTransport()
    client = FinvizClient(transport=fake, retry_attempts=0, retry_backoff=0.0)

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="event loop"):
            performance(client=client)
        await client.close()

    asyncio.run(inside())
