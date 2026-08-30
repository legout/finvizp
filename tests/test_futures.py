"""Current futures tile data tests (Card 0.4-C).

RED-first: every test below fails until ``finvizp.futures`` and
``finvizp._parsers.futures`` exist. Hermetic: the transport double serves the
scrubbed fixture page; no live HTTP.

Representation evidence (2026-08-30 bounded live probe, one request):
``/futures.ashx`` answers 301 -> ``/futures``; the page renders one inline
script carrying ``var groups = [...]`` (category groups whose contracts join
the tiles by ticker) and ``var tiles = {...}`` (one current tile per
contract: ``label``/``ticker``/``last``/``change``/``changeUsd``/``prevClose``
/``high``/``low``/``sparkline``/``sparklineDateChanges``). The page has no
``<table>`` element at all — the legacy ``finvizfinance`` performance-table
target returns nothing — and no chart/image artifact references. Every
``sparkline`` array was empty with an empty ``sparklineDateChanges`` and the
payload carries no provider timestamps: sparkline payloads are preserved
verbatim only, never relabeled as history. The footer states the provider's
own delay ("Futures and options delayed by 15 minutes"), kept as provenance.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastreq.backends.base import Backend

from finvizp.errors import FinvizNotFoundError, FinvizParseError
from finvizp.results import FetchResult, ResultStatus

FIXTURES = Path(__file__).parent / "fixtures" / "futures"
FETCHED_AT = dt.datetime(2026, 8, 30, 14, 30, tzinfo=dt.UTC)

FUTURES_PATH = "/futures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


CURRENT_PAGE = _fixture("current-tiles.html")
EMPTY_PAGE = _fixture("_empty-tiles.html")


class PageTransport(Backend):
    """Transport double serving one scripted body per requested path."""

    @property
    def name(self) -> str:
        return "futures-fake"

    def __init__(self, pages: dict[str, str | Exception]) -> None:
        self.pages = pages
        self.calls: list[Any] = []

    async def request(self, config: Any, stream_callback: Any = None) -> Any:
        from fastreq.backends.base import NormalizedResponse

        self.calls.append(config)
        path = config.url.removeprefix("https://finviz.com")
        outcome: Any = self.pages.get(path, KeyError(path))
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, KeyError):
            raise AssertionError(f"unexpected route {path}")  # pragma: no cover
        return NormalizedResponse.from_backend(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=outcome.encode(),
            url=config.url,
            is_json=False,
        )

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> PageTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def supports_http2(self) -> bool:
        return True


class StatusTransport(PageTransport):
    """Transport double answering one path with a raw HTTP status."""

    def __init__(self, path: str, status: int, body: str = "") -> None:
        self.pages: dict[str, str] = {path: body}
        self.calls: list[Any] = []
        self.status = status

    async def request(self, config: Any, stream_callback: Any = None) -> Any:
        from fastreq.backends.base import NormalizedResponse

        self.calls.append(config)
        return NormalizedResponse.from_backend(
            status_code=self.status,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=self.pages[config.url.removeprefix("https://finviz.com")].encode(),
            url=config.url,
            is_json=False,
        )


def _client(pages: dict[str, Any]) -> Any:
    from finvizp.client import FinvizClient

    transport = PageTransport(pages)
    client = FinvizClient(transport=transport, retry_attempts=0)
    client._transport = transport  # exposed for call-count assertions
    return client


def _tiles(html: str) -> dict[str, Any]:
    match = re.search(r"var tiles = (\{.*?\});", html, re.S)
    assert match is not None
    return json.loads(match.group(1))


# --- pure parser: representation proof ---------------------------------------------------------


def test_embedded_tiles_are_the_complete_representation() -> None:
    """The verified page carries the tiles payload; no legacy table remains."""
    assert "<table" not in CURRENT_PAGE.lower()
    tiles = _tiles(CURRENT_PAGE)
    assert tiles
    tile = tiles["ES"]
    for field in (
        "label",
        "ticker",
        "last",
        "change",
        "changeUsd",
        "prevClose",
        "high",
        "low",
        "sparkline",
        "sparklineDateChanges",
    ):
        assert field in tile


def test_parse_futures_page_rows_are_source_near() -> None:
    from finvizp._parsers.futures import parse_futures_page

    records = parse_futures_page(CURRENT_PAGE, fetched_at=FETCHED_AT, on_warning=lambda w: None)
    assert records.tiles["ES"]["label"] == "S&P 500"
    assert records.tiles["ES"]["last"] == pytest.approx(7724.75)
    assert records.tiles["ES"]["change"] == pytest.approx(-0.26)
    assert records.tiles["ES"]["change_usd"] == pytest.approx(-20.5)
    assert records.tiles["ES"]["prev_close"] == pytest.approx(7742.5)
    assert records.tiles["ES"]["high"] == pytest.approx(7782.5)
    assert records.tiles["ES"]["low"] == pytest.approx(7711.75)
    # Group join is preserved verbatim: category + contract metadata.
    assert records.groups == {
        "ES": "Indices",
        "NQ": "Indices",
        "VX": "Indices",
        "CL": "Energy",
    }
    assert records.category_of["ES"] == "Indices"
    # Provider delay statement is the page's own footer claim.
    assert records.delay_minutes == 15.0


def test_parse_futures_page_preserves_raw_tile_values() -> None:
    """Tiles keep the provider's numeric values verbatim for the raw columns."""
    from finvizp._parsers.futures import parse_futures_page

    records = parse_futures_page(CURRENT_PAGE, fetched_at=FETCHED_AT, on_warning=lambda w: None)
    tile = records.tiles["NQ"]
    assert tile["last"] == pytest.approx(25180.25)
    assert tile["change"] == pytest.approx(-0.42)
    # The provider's own key spellings survive for raw provenance.
    assert records.raw_tiles["NQ"]["changeUsd"] == pytest.approx(-106.25)
    assert records.raw_tiles["NQ"]["prevClose"] == pytest.approx(25286.5)


def test_parse_futures_page_sparkline_is_payload_not_history() -> None:
    """Sparkline arrays are preserved verbatim and never become bars/timestamps."""
    from finvizp._parsers.futures import parse_futures_page

    records = parse_futures_page(CURRENT_PAGE, fetched_at=FETCHED_AT, on_warning=lambda w: None)
    # Verified empty on the live page: kept as payload text evidence only.
    assert records.tiles["ES"]["sparkline"] == "[]"
    assert records.tiles["ES"]["sparkline_date_changes"] == {}
    # The parser exposes no sparkline-history surface at all.
    assert not any("history" in name or "bars" in name for name in dir(records))


def test_parse_futures_page_unknown_tile_field_is_drift() -> None:
    from finvizp._parsers.futures import parse_futures_page

    warnings: list[Any] = []
    records = parse_futures_page(CURRENT_PAGE, fetched_at=FETCHED_AT, on_warning=warnings.append)
    assert any(w.code == "unknown_field" and w.symbol == "ES" for w in warnings), warnings
    assert records.extra_fields["ES"] == {"someFutureField": "future-value"}


def test_parse_futures_page_missing_tiles_is_drift() -> None:
    from finvizp._parsers.futures import parse_futures_page

    with pytest.raises(FinvizParseError, match="tiles"):
        parse_futures_page(
            _fixture("_drift-missing-tiles.html"),
            fetched_at=FETCHED_AT,
            on_warning=lambda w: None,
        )


def test_parse_futures_page_malformed_tiles_is_drift() -> None:
    from finvizp._parsers.futures import parse_futures_page

    with pytest.raises(FinvizParseError, match="tiles"):
        parse_futures_page(
            _fixture("_drift-malformed-tiles.html"),
            fetched_at=FETCHED_AT,
            on_warning=lambda w: None,
        )


# --- dataset / Arrow table ---------------------------------------------------------------------


async def test_futures_returns_registered_arrow_table() -> None:
    from finvizp import arrow as fa
    from finvizp.futures import futures_async

    client = _client({FUTURES_PATH: CURRENT_PAGE})
    result = await futures_async(client=client)
    assert isinstance(result, FetchResult)
    table = result.table
    assert isinstance(table, pa.Table)
    assert table.schema.names == list(fa.dataset_field_names("futures_tiles"))
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.endpoint == FUTURES_PATH
    assert table.num_rows == 4
    rows = {row["symbol"]: row for row in table.to_pylist()}
    es = rows["ES"]
    assert es["name"] == "S&P 500"
    assert es["category"] == "Indices"
    assert es["last"] == pytest.approx(7724.75)
    # Percent unit stores fractions house-wide (-0.26%% display -> -0.0026);
    # the raw companion keeps the provider's percent-point payload text.
    assert es["change_percent"] == pytest.approx(-0.0026)
    assert es["change_usd"] == pytest.approx(-20.5)
    assert es["prev_close"] == pytest.approx(7742.5)
    assert es["high"] == pytest.approx(7782.5)
    assert es["low"] == pytest.approx(7711.75)
    assert es["sparkline"] == "[]"
    # Provider payload decoration kept as verbatim text (never interpreted).
    assert es["sparkline_date_changes"] == "{}"
    # Tile ``change`` is a percent: no absolute-change column exists, and the
    # raw companion carries the provider's exact payload text.
    assert "change" not in table.schema.names
    assert es["change_percent_raw"] == "-0.26"
    # last_raw keeps the provider's exact numeric payload text.
    assert es["last_raw"] == "7724.75"
    assert es["prev_close_raw"] == "7742.5"
    # Unknown provider field lands in extra_fields (Arrow map -> pair list).
    assert es["extra_fields"] == [("someFutureField", "future-value")]
    # The page's own delay statement rides every row.
    assert rows["CL"]["delay_minutes"] == pytest.approx(15.0)
    # Contract-only tile (no group entry) still yields a row, category null.
    assert "CL" in rows  # joined; and every fixture tile is in a group here


async def test_futures_row_contract_only_tile_has_null_category() -> None:
    """A tiles entry absent from the groups join keeps its row, category null."""
    from finvizp.futures import futures_async

    page = CURRENT_PAGE.replace(" var tiles = ", " var unused = ").replace(
        ' var unused = {"ES"',
        ' var tiles = {"ORPHAN":{"label":"Orphan","ticker":"ORPHAN"'
        ',"last":1.0,"change":0.0,"changeUsd":0.0,"prevClose":1.0,"high":1.0,"low":1.0'
        ',"sparkline":[],"sparklineDateChanges":{}},"ES"',
    )
    client = _client({FUTURES_PATH: page})
    table = (await futures_async(client=client)).table
    rows = {row["symbol"]: row for row in table.to_pylist()}
    orphan = rows["ORPHAN"]
    assert orphan["category"] is None
    assert orphan["last"] == pytest.approx(1.0)


async def test_futures_value_fields_convert_with_raw_companions() -> None:
    """Numeric tile fields convert to float64; raw keeps the payload display."""
    from finvizp.futures import futures_async

    client = _client({FUTURES_PATH: CURRENT_PAGE})
    table = (await futures_async(client=client)).table
    for name in ("last", "change_percent", "change_usd", "prev_close", "high", "low"):
        assert table.schema.field(name).type == pa.float64(), name
        assert table.schema.field(f"{name}_raw").type == pa.string(), name
    assert table.schema.field("delay_minutes").type == pa.float64()
    assert table.schema.field("sparkline").type == pa.string()


async def test_futures_recognized_empty_result() -> None:
    """An empty tiles object is a positively recognized empty, not drift."""
    from finvizp import arrow as fa
    from finvizp.futures import futures_async

    client = _client({FUTURES_PATH: EMPTY_PAGE})
    result = await futures_async(client=client)
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0
    assert result.table.schema.names == list(fa.dataset_field_names("futures_tiles"))


async def test_futures_malformed_payload_raises_parse_error() -> None:
    from finvizp.futures import futures_async

    client = _client({FUTURES_PATH: _fixture("_drift-malformed-tiles.html")})
    with pytest.raises(FinvizParseError):
        await futures_async(client=client)


async def test_futures_unknown_route_is_typed_not_found() -> None:
    from finvizp.futures import futures_async

    transport = StatusTransport(FUTURES_PATH, 404, "<html><body></body></html>")
    from finvizp.client import FinvizClient

    client = FinvizClient(transport=transport, retry_attempts=0)
    with pytest.raises(FinvizNotFoundError):
        await futures_async(client=client)


# --- cache -------------------------------------------------------------------------------------


async def test_futures_cache_reuse_without_new_request() -> None:
    from finvizp.futures import futures_async

    client = _client({FUTURES_PATH: CURRENT_PAGE})
    client._cache_ttl = 60.0
    first = await futures_async(client=client)
    second = await futures_async(client=client)
    assert client._transport.calls and len(client._transport.calls) == 1
    assert second.metadata.cache_hit is True
    assert second.metadata.fetched_at == first.metadata.fetched_at


async def test_futures_cache_false_requests_without_storing() -> None:
    from finvizp.futures import futures_async

    client = _client({FUTURES_PATH: CURRENT_PAGE})
    client._cache_ttl = 60.0
    await futures_async(client=client, cache=False)
    await futures_async(client=client, cache=False)
    assert len(client._transport.calls) == 2
    third = await futures_async(client=client)
    assert third.metadata.cache_hit is False
    assert len(client._transport.calls) == 3


async def test_futures_refresh_replaces_cached_entry() -> None:
    from finvizp.futures import futures_async

    client = _client({FUTURES_PATH: CURRENT_PAGE})
    client._cache_ttl = 60.0
    await futures_async(client=client)
    fresh = await futures_async(client=client, refresh=True)
    assert fresh.metadata.cache_hit is False
    assert len(client._transport.calls) == 2


# --- sync wrapper ------------------------------------------------------------------------------


def test_futures_sync_wrapper_runs_outside_loop() -> None:
    from finvizp.futures import futures

    client = _client({FUTURES_PATH: CURRENT_PAGE})
    result = futures(client=client)
    assert result.table.num_rows == 4


def test_futures_sync_wrapper_rejects_active_loop() -> None:
    from finvizp.futures import futures

    client = _client({FUTURES_PATH: CURRENT_PAGE})

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            futures(client=client)

    asyncio.run(inside())


# --- one-page smoke (opt-in, bounded) -----------------------------------------------------------


@pytest.mark.live_public
async def test_live_futures_smoke() -> None:
    # Env-var opt-in on top of the marker: avoids a duplicate live request
    # when both the futures suite and tests/live run in one invocation.
    import os

    if not os.environ.get("FINVIZP_LIVE_SMOKE"):
        pytest.skip("FINVIZP_LIVE_SMOKE not set")
    from finvizp.client import FinvizClient
    from finvizp.futures import futures_async

    async with FinvizClient() as client:
        result = await futures_async(client=client)
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.table.num_rows >= 0


# --- surface hygiene ---------------------------------------------------------------------------


def test_futures_has_no_enumeration_surface() -> None:
    """One public route, no per-contract or list-all escape hatch."""
    import importlib

    futures_module = importlib.import_module("finvizp.futures")

    public = {name for name in futures_module.__all__ if not name.endswith("_async")}
    assert {"futures"}.issubset(public)
    assert not any(
        "list" in name or "all" in name or "sitemap" in name for name in futures_module.__all__
    )
