"""Endpoint tests for symbol universe manifest and ranked search (Card 0.1-E).

RED-first: every test below failed until ``finvizp.symbols`` existed.
Transport is hermetic (scripted fake backend); no live network.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp import symbols as symbols_api
from finvizp.client import FinvizClient
from finvizp.errors import FinvizParseError, FinvizQueryError
from finvizp.results import ResultStatus

BASE = "https://finviz.com"
FIXTURES = Path(__file__).parent / "fixtures" / "symbols"


def _sitemap() -> str:
    return (FIXTURES / "stock-sitemap.xml").read_text("utf-8")


def _suggestions() -> str:
    return (FIXTURES / "suggestions.json").read_text("utf-8")


def _resp(body: bytes, content_type: str, url: str) -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=200,
        headers={"Content-Type": content_type},
        content=body,
        url=url,
        is_json="json" in content_type,
    )


class RecordingTransport(Backend):
    """Records every request URL/params; replays scripted responses."""

    def __init__(self, *scripted: NormalizedResponse) -> None:
        self.scripted = list(scripted)
        self.urls: list[str] = []
        self.params: list[dict[str, Any] | None] = []

    @property
    def name(self) -> str:
        return "recording"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.urls.append(config.url)
        self.params.append(dict(config.params) if config.params else None)
        return self.scripted.pop(0)

    async def close(self) -> None: ...

    async def __aenter__(self) -> RecordingTransport:
        return self

    async def __aexit__(self, *args: Any) -> None: ...

    def supports_http2(self) -> bool:
        return True


def _manifest_transport(*responses: NormalizedResponse) -> RecordingTransport:
    default = _resp(_sitemap().encode(), "text/xml", f"{BASE}/sitemap.xml")
    return RecordingTransport(*responses) if responses else RecordingTransport(default)


def _search_transport() -> RecordingTransport:
    return RecordingTransport(
        _resp(_suggestions().encode(), "application/json", f"{BASE}/api/suggestions")
    )


# --- symbols_async: exactly one manifest request, no following ------------------


async def test_symbols_makes_exactly_one_manifest_request() -> None:
    fake = _manifest_transport()
    result = await symbols_api.symbols_async(client=FinvizClient(transport=fake))
    assert fake.urls == [f"{BASE}/sitemap.xml"]
    assert fake.params == [{"t": "0", "p": "0"}]
    assert result.metadata.endpoint == "/sitemap.xml"
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.cache_hit is False


async def test_symbols_never_follows_listed_urls_or_sibling_sitemaps() -> None:
    fake = _manifest_transport()
    result = await symbols_api.symbols_async(client=FinvizClient(transport=fake))
    # Eleven URLs are listed (some malformed); none was requested.
    assert len(fake.urls) == 1
    table = result.table
    assert table.schema.names == ["symbol", "extra_fields", "fetched_at"]
    assert table.column("symbol").to_pylist() == ["AAPL", "BRK-B", "AAC-U", "REZI-WI", "NA"]
    stamps = table.column("fetched_at").to_pylist()
    assert all(ts.tzinfo is not None for ts in stamps)


async def test_symbols_ticker_na_survives_end_to_end() -> None:
    """Live-drift regression: real ticker ``NA`` must not sentinel-collapse to null.

    Discovered by the bounded live probe (2026-08-28): the manifest lists
    ``/stock?t=NA``; the Arrow builder's null-sentinel table collapsed it and
    the non-nullable key raised. Key identity must never be sentinel-mapped.
    """
    fake = _manifest_transport()
    result = await symbols_api.symbols_async(client=FinvizClient(transport=fake))
    symbols_col = result.table.column("symbol").to_pylist()
    assert "NA" in symbols_col
    assert result.metadata.status is ResultStatus.COMPLETE


def test_builder_preserves_key_sentinel_spellings() -> None:
    """Builder-level pin for the same fix: ``NA`` is identity on a key field."""
    from finvizp import arrow as fa

    now = dt.datetime.now(dt.UTC)
    for dataset in ("symbol_universe", "symbol_search"):
        table = fa.build_table(dataset, [{"symbol": "NA"}], fetched_at=now)
        assert table.column("symbol").to_pylist() == ["NA"]


async def test_symbols_returns_registered_arrow_schema() -> None:
    from finvizp import schemas

    result = await symbols_api.symbols_async(client=FinvizClient(transport=_manifest_transport()))
    assert result.table.schema.equals(schemas.arrow_schema("symbol_universe"))


async def test_symbols_unexpected_urls_surface_as_warnings() -> None:
    result = await symbols_api.symbols_async(client=FinvizClient(transport=_manifest_transport()))
    codes = {w.code for w in result.metadata.warnings}
    assert "unexpected_url" in codes
    assert result.metadata.status is ResultStatus.COMPLETE  # drift warns, never fails


async def test_symbols_empty_manifest_is_recognized_empty() -> None:
    fake = RecordingTransport(_resp(b"<urlset></urlset>", "text/xml", f"{BASE}/sitemap.xml"))
    result = await symbols_api.symbols_async(client=FinvizClient(transport=fake))
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0


async def test_symbols_sync_delegates_through_run_sync() -> None:
    fake = _manifest_transport()

    def call_sync() -> Any:
        return symbols_api.symbols(client=FinvizClient(transport=fake))

    result = await asyncio.to_thread(call_sync)
    assert fake.urls == [f"{BASE}/sitemap.xml"]
    assert result.table.num_rows == 5


async def test_symbols_uses_client_cache_and_refresh() -> None:
    # Two scripted manifest responses: one for the initial miss, one for refresh.
    fake = _manifest_transport(
        _resp(_sitemap().encode(), "text/xml", f"{BASE}/sitemap.xml"),
        _resp(_sitemap().encode(), "text/xml", f"{BASE}/sitemap.xml"),
    )
    client = FinvizClient(transport=fake, cache_ttl=60.0)
    first = await symbols_api.symbols_async(client=client)
    second = await symbols_api.symbols_async(client=client)
    assert len(fake.urls) == 1
    assert second.metadata.cache_hit is True
    assert first.metadata.fetched_at == second.metadata.fetched_at
    third = await symbols_api.symbols_async(client=client, refresh=True)
    assert len(fake.urls) == 2
    assert third.metadata.cache_hit is False
    await client.close()


async def test_transient_client_is_used_when_client_omitted() -> None:
    # The transient-client path is exercised live separately (bounded probe);
    # hermetically we only verify the helper produces a working client.
    from finvizp.symbols import _client_or_transient

    assert isinstance(_client_or_transient(None), FinvizClient)
    client = FinvizClient(transport=_manifest_transport())
    assert _client_or_transient(client) is client


# --- search_symbols_async: bounded ranked suggestions ---------------------------


async def test_search_makes_one_encoded_suggestions_request() -> None:
    fake = _search_transport()
    result = await symbols_api.search_symbols_async("apple", client=FinvizClient(transport=fake))
    assert fake.urls == [f"{BASE}/api/suggestions"]
    assert fake.params == [{"input": "apple"}]
    assert result.metadata.endpoint == "/api/suggestions"
    assert result.metadata.status is ResultStatus.COMPLETE


@pytest.mark.parametrize("bad", ["", "   ", "\t\n", "x" * 65])
async def test_search_validates_nonblank_bounded_input_before_network(bad: str) -> None:
    fake = _search_transport()
    with pytest.raises(FinvizQueryError):
        await symbols_api.search_symbols_async(bad, client=FinvizClient(transport=fake))
    assert fake.urls == []  # rejected before any request


async def test_search_encodes_query_safely() -> None:
    fake = _search_transport()
    await symbols_api.search_symbols_async("brk & co", client=FinvizClient(transport=fake))
    assert fake.params == [{"input": "brk & co"}]


async def test_search_preserves_provider_ranking_and_schema() -> None:
    from finvizp import schemas

    result = await symbols_api.search_symbols_async(
        "AAP", client=FinvizClient(transport=_search_transport())
    )
    table = result.table
    assert table.schema.equals(schemas.arrow_schema("symbol_search"))
    rows = table.to_pylist()
    assert [r["symbol"] for r in rows] == ["AAPL", "AAP"]
    assert rows[0]["company"] == "Apple Inc."
    assert rows[0]["exchange"] == "NASDAQ"


async def test_search_with_indices_passes_provider_parameter() -> None:
    fake = _search_transport()
    await symbols_api.search_symbols_async(
        "AAP", client=FinvizClient(transport=fake), with_indices=True
    )
    assert fake.params == [{"input": "AAP", "withIndices": 1}]


async def test_search_empty_result_is_recognized_empty() -> None:
    fake = RecordingTransport(_resp(b"[]", "application/json", f"{BASE}/api/suggestions"))
    result = await symbols_api.search_symbols_async("zzzz", client=FinvizClient(transport=fake))
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0


async def test_search_sync_delegates_through_run_sync() -> None:
    fake = _search_transport()

    def call_sync() -> Any:
        return symbols_api.search_symbols("apple", client=FinvizClient(transport=fake))

    result = await asyncio.to_thread(call_sync)
    assert fake.urls == [f"{BASE}/api/suggestions"]
    assert result.table.num_rows == 2


async def test_search_rejects_non_string_query_before_network() -> None:
    fake = _search_transport()
    with pytest.raises(FinvizQueryError):
        await symbols_api.search_symbols_async(None, client=FinvizClient(transport=fake))  # type: ignore[arg-type]
    assert fake.urls == []


async def test_search_malformed_json_drift_raises_typed_parse_error() -> None:
    fake = RecordingTransport(_resp(b"{oops", "application/json", f"{BASE}/api/suggestions"))
    with pytest.raises(FinvizParseError):
        await symbols_api.search_symbols_async("AAP", client=FinvizClient(transport=fake))
