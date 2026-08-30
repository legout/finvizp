"""RED-first tests for structured map bundles (Card 0.3-B, TDD step 2).

Every test fails until ``finvizp.maps`` and ``finvizp.models.MapBundle``
exist. Hermetic: the transport double serves the scrubbed map page and its
preloaded hierarchy asset exactly as the verified two-request contract
prescribes. No live HTTP, no JavaScript execution, no renderer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp.client import FinvizClient
from finvizp.errors import FinvizParseError, FinvizQueryError
from finvizp.results import AccessTier, ResultStatus

FIXTURES = Path(__file__).parent / "fixtures" / "maps"
_PAGE = (FIXTURES / "sp500-embedded.html").read_text("utf-8")
_ASSET = (FIXTURES / "sp500-hierarchy.js").read_text("utf-8")
_CHUNK_PATH = "/assets/dist/mapbase.v1.0f1xTURE.js"
BASE = "https://finviz.com"


def _resp(body: str, path: str, *, kind: str = "text/html") -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=200,
        headers={"Content-Type": f"{kind}; charset=utf-8"},
        content=body.encode(),
        url=f"{BASE}{path}",
        is_json=False,
    )


class MapTransport(Backend):
    """Serves the fixture map page and its preloaded hierarchy asset."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    @property
    def name(self) -> str:
        return "map-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls.append(config)
        path = urlsplit(str(config.url)).path
        if path == "/map.ashx":
            return _resp(_PAGE, path)
        if path == _CHUNK_PATH:
            return _resp(_ASSET, path, kind="text/javascript")
        return _resp("not found", path, kind="text/html")

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> MapTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _paths(fake: MapTransport) -> list[str]:
    return [urlsplit(str(c.url)).path for c in fake.calls]


# --- the two-request contract ---------------------------------------------------


async def test_map_bundle_two_requests_page_then_preloaded_asset() -> None:
    from finvizp.maps import map_async

    fake = MapTransport()
    async with FinvizClient(transport=fake) as client:
        result = await map_async(client=client)
    assert _paths(fake) == ["/map.ashx", _CHUNK_PATH]
    assert result.metadata.endpoint == "/map.ashx"
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.access_tier is AccessTier.PUBLIC


async def test_map_bundle_warm_call_replays_cache_without_requests() -> None:
    from finvizp.maps import map_async

    fake = MapTransport()
    async with FinvizClient(transport=fake, cache_ttl=60.0) as client:
        first = await map_async(client=client)
        warm = await map_async(client=client)
    assert _paths(fake) == ["/map.ashx", _CHUNK_PATH]
    assert warm.metadata.cache_hit is True
    assert first.data == warm.data


async def test_map_cache_false_bypasses_cache() -> None:
    from finvizp.maps import map_async

    fake = MapTransport()
    async with FinvizClient(transport=fake, cache_ttl=60.0) as client:
        await map_async(client=client)
        again = await map_async(client=client, cache=False)
    assert _paths(fake) == ["/map.ashx", _CHUNK_PATH, "/map.ashx", _CHUNK_PATH]
    assert again.metadata.cache_hit is False


async def test_map_refresh_replaces_cached_bundle() -> None:
    from finvizp.maps import map_async

    fake = MapTransport()
    async with FinvizClient(transport=fake, cache_ttl=60.0) as client:
        await map_async(client=client)
        fresh = await map_async(client=client, refresh=True)
        warm = await map_async(client=client)
    assert _paths(fake) == ["/map.ashx", _CHUNK_PATH, "/map.ashx", _CHUNK_PATH]
    assert fresh.metadata.cache_hit is False
    assert warm.data == fresh.data


# --- the MapBundle: one row of map observations ---------------------------------


async def test_map_bundle_joins_hierarchy_leaves_with_perf() -> None:
    from finvizp.maps import map_async
    from finvizp.models import MapBundle

    fake = MapTransport()
    async with FinvizClient(transport=fake) as client:
        result = await map_async(client=client)
    bundle = result.data
    assert isinstance(bundle, MapBundle)
    assert bundle.symbol == "SP500"
    assert bundle.access_tier is AccessTier.PUBLIC
    assert bundle.delay_minutes == 1.0
    # Hierarchy drives row order; perf joins by ticker (verified contract:
    # hierarchy leaves are the constituents, perf may be missing per leaf).
    software = next(
        industry
        for sector in bundle.root.children
        for industry in sector.children
        if industry.name == "Software"
    )
    assert [(leaf.name, leaf.value, leaf.perf) for leaf in software.children] == [
        ("AAA", 12000.0, 0.51),
        ("BBB", 8000.0, -0.29),
        ("CCC", 6000.0, 1.22),
    ]
    # perf-only share-class symbol (AAA-P): drift evidence, never invented data.
    assert "AAA-P" in bundle.unmapped_perf


async def test_map_bundle_constituents_flat_order() -> None:
    from finvizp.maps import map_async

    fake = MapTransport()
    async with FinvizClient(transport=fake) as client:
        result = await map_async(client=client)
    symbols = [c.symbol for c in result.data.constituents]
    assert symbols == ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
    first = result.data.constituents[0]
    assert (first.sector, first.industry) == ("Technology", "Software")
    assert (first.description, first.value, first.perf) == ("Alpha App Corp", 12000.0, 0.51)


async def test_map_bundle_schema_version() -> None:
    from finvizp.maps import map_async

    fake = MapTransport()
    async with FinvizClient(transport=fake) as client:
        result = await map_async(client=client)
    assert result.metadata.schema_version >= 1


# --- provenance and recognized empty --------------------------------------------


async def test_map_page_without_delay_statement_is_typed_drift() -> None:
    from finvizp._parsers.maps import parse_map_page
    from tests.fixtures.maps._build import map_page

    body = map_page().replace(
        "Stock quotes delayed by 1 minute. Futures and options delayed by 15 minutes.", ""
    )
    with pytest.raises(FinvizParseError, match="delay"):
        parse_map_page(body)


async def test_map_bundle_delay_and_access_provenance() -> None:
    from finvizp.maps import map_async

    fake = MapTransport()
    async with FinvizClient(transport=fake) as client:
        result = await map_async(client=client)
    assert result.data.delay_minutes == 1.0
    assert result.metadata.access_tier is AccessTier.PUBLIC


async def test_map_empty_page_is_recognized_empty_bundle() -> None:
    from finvizp.maps import map_async

    empty_page = (
        "<html><head><title>S&amp;P 500 Map</title></head>"
        '<body><div id="root"></div>'
        "Stock quotes delayed by 1 minute. Futures and options delayed by 15 minutes."
        "</body></html>"
    )
    empty_asset = 'e.exports={name:"Root"}'

    class EmptyTransport(MapTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            self.calls.append(config)
            path = urlsplit(str(config.url)).path
            if path == "/map.ashx":
                return _resp(empty_page, path)
            if path == _CHUNK_PATH:
                return _resp(empty_asset, path, kind="text/javascript")
            return _resp("not found", path, kind="text/html")

    fake = EmptyTransport()
    async with FinvizClient(transport=fake) as client:
        result = await map_async(client=client)
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.data.perf == {}
    assert result.data.unmapped_perf == ()


# --- input validation and sync wrappers -----------------------------------------


async def test_map_rejects_unknown_map_argument() -> None:
    from finvizp.maps import map_async

    fake = MapTransport()
    async with FinvizClient(transport=fake) as client:
        with pytest.raises(FinvizQueryError, match="map"):
            await map_async(map="nsdq", client=client)


def test_map_sync_wrapper_outside_loop() -> None:
    from finvizp.maps import map

    fake = MapTransport()
    client = FinvizClient(transport=fake, retry_attempts=0, retry_backoff=0.0)
    try:
        result = map(client=client)
    finally:
        asyncio.run(client.close())
    assert result.data.symbol == "SP500"


def test_map_sync_rejects_active_event_loop() -> None:
    from finvizp.maps import map

    fake = MapTransport()
    client = FinvizClient(transport=fake, retry_attempts=0, retry_backoff=0.0)

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="event loop"):
            map(client=client)
        await client.close()

    asyncio.run(inside())
