"""Endpoint tests for symbol universe manifest and ranked search (Card 0.1-E).

RED-first: every test below failed until ``finvizp.symbols`` existed.
Transport is hermetic (scripted fake backend); no live network.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp import symbols as symbols_api
from finvizp.client import FinvizClient
from finvizp.errors import FinvizParseError, FinvizQueryError, FinvizTransportError
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
        self.entered = 0
        self.closed = 0

    @property
    def name(self) -> str:
        return "recording"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.urls.append(config.url)
        self.params.append(dict(config.params) if config.params else None)
        return self.scripted.pop(0)

    async def close(self) -> None:
        self.closed += 1

    async def __aenter__(self) -> RecordingTransport:
        self.entered += 1
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed += 1

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


def test_builder_still_rejects_other_sentinels_on_key_fields() -> None:
    """The key-field exemption is exactly ``symbol='NA'``; nothing wider."""
    from finvizp import arrow as fa
    from finvizp.errors import FinvizDataError

    now = dt.datetime.now(dt.UTC)
    for dataset in ("symbol_universe", "symbol_search"):
        for value in ("", "-", "--", "None", "null"):
            with pytest.raises(FinvizDataError, match="non-nullable"):
                fa.build_table(dataset, [{"symbol": value}], fetched_at=now)


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
    fake = RecordingTransport(
        _resp(
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
            "text/xml",
            f"{BASE}/sitemap.xml",
        )
    )
    result = await symbols_api.symbols_async(client=FinvizClient(transport=fake))
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0


async def test_symbols_all_unexpected_urls_are_parse_drift() -> None:
    fake = RecordingTransport(
        _resp(
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc>https://finviz.com/screener.ashx?v=1</loc></url></urlset>",
            "text/xml",
            f"{BASE}/sitemap.xml",
        )
    )
    with pytest.raises(FinvizParseError):
        await symbols_api.symbols_async(client=FinvizClient(transport=fake))


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


async def test_symbols_cache_false_bypasses_cache_per_call() -> None:
    fake = _manifest_transport(
        _resp(_sitemap().encode(), "text/xml", f"{BASE}/sitemap.xml"),
        _resp(_sitemap().encode(), "text/xml", f"{BASE}/sitemap.xml"),
    )
    client = FinvizClient(transport=fake, cache_ttl=60.0)
    first = await symbols_api.symbols_async(client=client)
    second = await symbols_api.symbols_async(client=client, cache=False)
    assert len(fake.urls) == 2  # cache=False never reads or writes the cache
    assert second.metadata.cache_hit is False
    assert second.metadata.fetched_at > first.metadata.fetched_at
    third = await symbols_api.symbols_async(client=client)
    assert third.metadata.cache_hit is True  # cache=False left the cache intact
    await client.close()


async def test_search_cache_false_bypasses_cache_per_call() -> None:
    fake = RecordingTransport(
        _resp(_suggestions().encode(), "application/json", f"{BASE}/api/suggestions"),
        _resp(_suggestions().encode(), "application/json", f"{BASE}/api/suggestions"),
    )
    client = FinvizClient(transport=fake, cache_ttl=60.0)
    await symbols_api.search_symbols_async("apple", client=client)
    second = await symbols_api.search_symbols_async("apple", client=client, cache=False)
    assert len(fake.urls) == 2
    assert second.metadata.cache_hit is False
    await client.close()


async def test_transient_client_is_used_when_client_omitted() -> None:
    from finvizp.symbols import _client_or_transient

    async with _client_or_transient(None) as transient:
        assert isinstance(transient, FinvizClient)
    client = FinvizClient(transport=_manifest_transport())
    async with _client_or_transient(client) as yielded:
        assert yielded is client


async def test_transient_client_is_closed_on_success_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Omitted client: the lifecycle is owned by the call, so the transport is
    # entered and closed exactly once — on success and on failure alike.
    for scripted, expect_raise in (
        (_resp(_sitemap().encode(), "text/xml", f"{BASE}/sitemap.xml"), False),
        (_resp(b"{oops", "application/json", f"{BASE}/sitemap.xml"), True),
    ):
        fake = RecordingTransport(scripted)

        def factory(*_a: Any, fake: RecordingTransport = fake, **_kw: Any) -> FinvizClient:
            return FinvizClient(transport=fake)

        monkeypatch.setattr(symbols_api, "FinvizClient", factory)
        with pytest.raises(Exception) if expect_raise else contextlib.nullcontext():
            await symbols_api.symbols_async()
        assert fake.entered == 1
        assert fake.closed == 1


async def test_caller_client_is_never_closed() -> None:
    fake = _manifest_transport()
    client = FinvizClient(transport=fake)
    await symbols_api.symbols_async(client=client)
    assert client._entered is True  # still open for reuse by the caller
    await client.close()


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


async def test_search_additive_fields_reach_extra_fields_with_warning() -> None:
    # Provider drift survives end to end: unknown fields land in extra_fields
    # and the unknown_field warning reaches result metadata.
    payload = json.dumps(
        [
            {
                "ticker": "AAPL",
                "company": "Apple Inc.",
                "exchange": "NASDAQ",
                "provider_added": "v",
            }
        ]
    )
    fake = RecordingTransport(
        _resp(payload.encode(), "application/json", f"{BASE}/api/suggestions")
    )
    result = await symbols_api.search_symbols_async("apple", client=FinvizClient(transport=fake))
    row = result.table.to_pylist()[0]
    assert row["extra_fields"] == [("provider_added", "v")]
    assert any(w.code == "unknown_field" for w in result.metadata.warnings)


async def test_search_symbol_collision_preserves_ticker_key_with_drift_evidence() -> None:
    payload = json.dumps(
        [
            {
                "ticker": "AAPL",
                "company": "Apple Inc.",
                "exchange": "NASDAQ",
                "symbol": "EVIL",
            }
        ]
    )
    fake = RecordingTransport(
        _resp(payload.encode(), "application/json", f"{BASE}/api/suggestions")
    )
    result = await symbols_api.search_symbols_async("apple", client=FinvizClient(transport=fake))
    row = result.table.to_pylist()[0]
    assert row["symbol"] == "AAPL"
    assert row["extra_fields"] == [("provider_symbol", "EVIL")]
    assert any(w.code == "unknown_field" for w in result.metadata.warnings)


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


async def test_search_json_null_drift_raises_typed_parse_error() -> None:
    fake = RecordingTransport(_resp(b"null", "application/json", f"{BASE}/api/suggestions"))
    with pytest.raises(FinvizParseError):
        await symbols_api.search_symbols_async("AAP", client=FinvizClient(transport=fake))
    assert fake.urls == [f"{BASE}/api/suggestions"]


async def test_search_rejects_non_string_query_before_network() -> None:
    fake = _search_transport()
    with pytest.raises(FinvizQueryError):
        await symbols_api.search_symbols_async(None, client=FinvizClient(transport=fake))  # type: ignore[arg-type]
    assert fake.urls == []


async def test_search_malformed_json_drift_raises_typed_parse_error() -> None:
    fake = RecordingTransport(_resp(b"{oops", "application/json", f"{BASE}/api/suggestions"))
    with pytest.raises(FinvizParseError):
        await symbols_api.search_symbols_async("AAP", client=FinvizClient(transport=fake))


# --- post-review remediation regressions -----------------------------------------


async def test_manifest_same_origin_redirect_is_not_followed() -> None:
    """One request, even when /sitemap.xml answers with a same-origin redirect.

    The symbols contract is one request per call. A 301 to another finviz.com
    URL (e.g. a stock page) must surface as transport drift, never be followed
    as a second request.
    """
    fake = RecordingTransport(
        NormalizedResponse.from_backend(
            status_code=301,
            headers={"Content-Type": "text/xml", "Location": f"{BASE}/quote.ashx?t=AAPL"},
            content=b"",
            url=f"{BASE}/sitemap.xml",
            is_json=False,
        ),
    )
    with pytest.raises(FinvizTransportError):
        await symbols_api.symbols_async(client=FinvizClient(transport=fake))
    assert len(fake.urls) == 1  # the stock URL was never requested


async def test_search_malformed_ticker_never_reaches_a_complete_result() -> None:
    payload = json.dumps([{"ticker": "BAD!", "company": "C", "exchange": "NYSE"}])
    fake = RecordingTransport(
        _resp(payload.encode(), "application/json", f"{BASE}/api/suggestions")
    )
    with pytest.raises(FinvizParseError):
        await symbols_api.search_symbols_async("bad", client=FinvizClient(transport=fake))
    assert fake.urls == [f"{BASE}/api/suggestions"]


async def test_search_missing_company_is_parse_drift_not_null() -> None:
    payload = json.dumps([{"ticker": "AAPL", "exchange": "NASDAQ"}])
    fake = RecordingTransport(
        _resp(payload.encode(), "application/json", f"{BASE}/api/suggestions")
    )
    with pytest.raises(FinvizParseError):
        await symbols_api.search_symbols_async("apple", client=FinvizClient(transport=fake))


async def test_manifest_encoded_and_padded_tickers_warn_not_normalize() -> None:
    """``t=%C5%BF`` and ``t=AAPL+`` are unexpected URLs, never symbols S/AAPL."""
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://finviz.com/stock?t=%C5%BF</loc></url>"
        "<url><loc>https://finviz.com/stock?t=AAPL+</loc></url>"
        "</urlset>"
    )
    fake = RecordingTransport(_resp(xml.encode(), "text/xml", f"{BASE}/sitemap.xml"))
    # A manifest with only unexpected URLs is parse drift end to end (existing
    # no-canonical-URLs rule); the malformed sources never normalize.
    with pytest.raises(FinvizParseError):
        await symbols_api.symbols_async(client=FinvizClient(transport=fake))
    assert fake.urls == [f"{BASE}/sitemap.xml"]  # one request, nothing followed
