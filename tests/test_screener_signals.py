"""Tests for signal screens: every reviewed registry signal through the shared collector.

RED-first: fails until ``finvizp.screener`` exposes ``signal_async``/``signal``.
Hermetic: the transport double serves the scrubbed fixture pages; no live HTTP.
"""

from __future__ import annotations

import asyncio

import pytest

from finvizp._queries.screener import CustomColumns, ScreenerQuery, Signal, screener_registry
from finvizp.errors import FinvizQueryError
from finvizp.results import ResultStatus
from finvizp.screener import SIGNAL_COLUMNS, signal, signal_async
from tests.test_screener import CUSTOM, EMPTY, ScreenTransport, _client, _rs

# --- registry resolution --------------------------------------------------------


def test_every_registered_signal_resolves_through_query_model() -> None:
    registry = screener_registry()
    assert registry.signals  # the checked-in registry carries the count evidence
    for name, spec in registry.signals.items():
        query = ScreenerQuery(
            view="custom", signal=Signal(name), columns=CustomColumns(SIGNAL_COLUMNS)
        )
        params = query.provider_params()
        assert params["s"] == spec.code
        assert params["v"] == registry.views["custom"].code
        assert params["c"].startswith("0,1,")


async def test_unknown_signal_fails_before_network() -> None:
    fake = ScreenTransport()
    with pytest.raises(FinvizQueryError, match="unknown signal"):
        await signal_async("Not A Signal", client=_client(fake))
    assert fake.calls == []


# --- shared collector integration --------------------------------------------------


async def test_signal_screen_uses_shared_collector_with_provenance() -> None:
    fake = ScreenTransport(default=CUSTOM, total=20)
    result = await signal_async("Top Gainers", client=_client(fake))
    assert _rs(fake) == [1]
    call = fake.calls[0]
    assert call["v"] == "151"
    assert call["s"] == "ta_topgainers"
    assert call["c"].startswith("0,1,")
    table = result.table
    assert table.column_names[0] == "rank"
    assert table.column_names[1] == "symbol"
    assert table.column("rank").to_pylist() == list(range(1, 21))
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.endpoint == "/screener.ashx"


async def test_signal_query_provenance_in_metadata() -> None:
    fake = ScreenTransport(default=CUSTOM, total=20)
    result = await signal_async("Top Gainers", client=_client(fake))
    expected = ScreenerQuery(
        view="custom", signal=Signal("Top Gainers"), columns=CustomColumns(SIGNAL_COLUMNS)
    )
    assert result.metadata.query["q"] == expected.to_json()


async def test_signal_screen_paginates_via_shared_collector() -> None:
    # The parser is header-driven, so overview-shaped pages serve any view code.
    fake = ScreenTransport(total=45)
    result = await signal_async("Top Gainers", client=_client(fake))
    assert _rs(fake) == [1, 21, 41]
    assert result.table.num_rows == 45


# --- sync wrapper ---------------------------------------------------------------------


def test_signal_sync_wrapper_runs_outside_loop() -> None:
    fake = ScreenTransport(default=EMPTY)
    result = signal("Top Gainers", client=_client(fake))
    assert result.metadata.status is ResultStatus.EMPTY


def test_signal_sync_wrapper_rejects_active_loop() -> None:
    fake = ScreenTransport(default=CUSTOM, total=20)

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            signal("Top Gainers", client=_client(fake))

    asyncio.run(inside())
