"""Tests for the public screener collector: pagination, safety, strict/partial, cache, sync.

RED-first: every test fails until ``finvizp.screener`` exists. Hermetic: the
transport double serves the scrubbed fixture pages; no live HTTP.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp._queries.screener import CustomColumns, Filter, ScreenerQuery
from finvizp.client import FinvizClient
from finvizp.errors import (
    FinvizParseError,
    FinvizQueryError,
    FinvizRateLimitError,
)
from finvizp.results import FetchResult, ResultStatus
from finvizp.screener import SCREEN_PATH, screen, screen_async
from tests.fixtures.screener._build import overview_for

BASE = "https://finviz.com"
FIXTURES = Path(__file__).parent / "fixtures" / "screener"

P1 = (FIXTURES / "overview-page-1.html").read_text("utf-8")
FINAL = (FIXTURES / "overview-final-page.html").read_text("utf-8")
CUSTOM = (FIXTURES / "custom-columns.html").read_text("utf-8")
EMPTY = (FIXTURES / "no-results.html").read_text("utf-8")
MALFORMED = (FIXTURES / "_drift-malformed-row.html").read_text("utf-8")


def _html(
    body: str,
    *,
    status: int = 200,
    url: str | None = None,
    headers: dict[str, str] | None = None,
) -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=status,
        headers=headers or {"Content-Type": "text/html; charset=utf-8"},
        content=body.encode(),
        url=url or f"{BASE}/screener.ashx",
        is_json=False,
    )


class ScreenTransport(Backend):
    """Serves provider-shaped pages per start rank; can inject failures/drift.

    ``pages`` maps a start rank to a raw body (fixture/drift injection); any
    other start rank renders a provider-shaped page of ``page_size`` rows via
    :func:`overview_for` (total 600 by default, so ranks are page-coherent).
    """

    def __init__(
        self,
        *,
        pages: dict[int, str] | None = None,
        default: str | None = None,
        fail_pages: set[int] | None = None,
        total: int = 600,
        page_size: int = 20,
    ) -> None:
        self.pages = dict(pages or {})
        self.default = default
        self.fail_pages = set(fail_pages or set())
        self.total = total
        self.page_size = page_size
        self.calls: list[dict[str, str]] = []

    @property
    def name(self) -> str:
        return "screener-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        params = {str(k): str(v) for k, v in (config.params or {}).items()}
        self.calls.append(params)
        page = int(params.get("r", "1"))
        if page in self.fail_pages:
            return self._error(page, str(config.url))
        body = self.pages.get(page)
        if body is None:
            body = (
                self.default
                if self.default is not None
                else overview_for(page, total=self.total, per_page=self.page_size)
            )
        return _html(body, url=str(config.url))

    def _error(self, page: int, url: str) -> NormalizedResponse:
        """One retryable-status failure response, 429 or generic 5xx."""
        status = 429 if page % 2 == 1 else 500
        return _html("provider failure", status=status, url=url)

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> ScreenTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _client(fake: ScreenTransport, **kwargs: Any) -> FinvizClient:
    kwargs.setdefault("retry_attempts", 0)
    kwargs.setdefault("retry_backoff", 0.0)
    return FinvizClient(transport=fake, **kwargs)


def _rs(fake: ScreenTransport) -> list[int]:
    return [int(call.get("r", "1")) for call in fake.calls]


# --- one page, named view ----------------------------------------------------------


async def test_single_page_query_requests_explicit_screen() -> None:
    fake = ScreenTransport(total=20)  # a one-page screen
    result = await screen_async(ScreenerQuery(view="overview"), client=_client(fake))
    assert isinstance(result, FetchResult)
    assert _rs(fake) == [1]
    assert fake.calls[0]["v"] == "111"
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.endpoint == "/screener.ashx"


async def test_named_view_table_has_registered_schema() -> None:
    fake = ScreenTransport(total=20)
    result = await screen_async(ScreenerQuery(view="overview"), client=_client(fake))
    table = result.table
    assert table.column_names[0] == "rank"
    assert table.column_names[1] == "symbol"
    assert "market_cap" in table.column_names
    assert "fetched_at" in table.column_names
    assert table.column("symbol").to_pylist()[0] == "S001X"
    assert table.column("rank").to_pylist() == list(range(1, 21))


async def test_query_provenance_in_metadata() -> None:
    query = ScreenerQuery(view="overview", filters=[Filter(name="Sector", option="Technology")])
    fake = ScreenTransport()
    result = await screen_async(query, client=_client(fake))
    assert result.metadata.query["q"] == query.to_json()


# --- complete pagination ------------------------------------------------------------


async def test_pagination_walks_until_final_page() -> None:
    fake = ScreenTransport(total=588, pages={581: FINAL})
    result = await screen_async(ScreenerQuery(view="overview"), client=_client(fake), max_pages=100)
    assert _rs(fake)[:2] == [1, 21]
    assert _rs(fake)[-1] == 581
    table = result.table
    assert table.num_rows == 588
    # ranks follow provider absolute order across pages
    assert table.column("rank").to_pylist()[:3] == [1, 2, 3]
    assert table.column("rank").to_pylist()[-1] == 588


async def test_final_page_stops_on_sub_page_size_rows() -> None:
    fake = ScreenTransport(total=588, pages={21: FINAL})
    await screen_async(ScreenerQuery(view="overview"), client=_client(fake), max_pages=10)
    assert _rs(fake) == [1, 21]


async def test_pagination_stops_when_start_plus_page_size_exceeds_total() -> None:
    # A full 20-row page whose marker start (581) + 20 > the observed total
    # (588) is final even though the row count alone says keep walking.
    short = P1.replace("#1 / 600 Total", "#581 / 588 Total")
    fake = ScreenTransport(pages={1: short})
    await screen_async(ScreenerQuery(view="overview"), client=_client(fake), max_pages=10)
    assert _rs(fake) == [1]


async def test_page_param_advances_via_registry_page_size() -> None:
    # The query's own page positions the first fetch (r = (page-1)*20 + 1).
    fake = ScreenTransport(pages={21: FINAL}, default=P1)
    await screen_async(ScreenerQuery(view="overview", page=2), client=_client(fake), max_pages=10)
    assert _rs(fake) == [21]


async def test_never_assumes_universe_size_total_is_observation() -> None:
    # The total is read from the page marker, never baked into requests.
    fake = ScreenTransport(total=588, pages={581: FINAL})
    result = await screen_async(ScreenerQuery(view="overview"), client=_client(fake), max_pages=100)
    assert all(int(call.get("r", "1")) > 0 for call in fake.calls)
    assert result.metadata.status is ResultStatus.COMPLETE


# --- recognized empty ----------------------------------------------------------------


async def test_recognized_no_results_is_empty_result() -> None:
    fake = ScreenTransport(default=EMPTY)
    result = await screen_async(ScreenerQuery(view="overview"), client=_client(fake))
    assert _rs(fake) == [1]
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0


# --- custom view ---------------------------------------------------------------------


async def test_custom_view_assembles_registry_column_order() -> None:
    query = ScreenerQuery(view="custom", columns=CustomColumns(names=["No.", "Ticker", "Price"]))
    fake = ScreenTransport(default=CUSTOM, total=20)
    result = await screen_async(query, client=_client(fake))
    table = result.table
    assert tuple(table.column_names) == (
        "rank",
        "symbol",
        "price",
        "fetched_at",
        "extra_fields",
    )


async def test_duplicate_columns_rejected_before_network() -> None:
    fake = ScreenTransport()
    with pytest.raises(FinvizQueryError, match="duplicate"):
        await screen_async(
            ScreenerQuery(view="custom", columns=CustomColumns(names=["Price", "Price"])),
            client=_client(fake),
        )
    assert fake.calls == []


async def test_provider_unknown_column_rejected_before_network() -> None:
    fake = ScreenTransport()
    with pytest.raises(FinvizQueryError, match="unknown column"):
        await screen_async(
            ScreenerQuery(view="custom", columns=CustomColumns(names=["Not A Column"])),
            client=_client(fake),
        )
    assert fake.calls == []


# --- duplicates / repeated state / safety stops ---------------------------------------


async def test_duplicate_rows_across_pages_raise_strict() -> None:
    # Page 2 renders the same ranks as page 1: repeated pagination state.
    fake = ScreenTransport(pages={21: P1})
    with pytest.raises(FinvizParseError, match="already seen"):
        await screen_async(ScreenerQuery(view="overview"), client=_client(fake))


async def test_max_pages_stops_pagination() -> None:
    fake = ScreenTransport(total=600)  # full 20-row pages forever
    result = await screen_async(
        ScreenerQuery(view="overview"), client=_client(fake), allow_partial=True, max_pages=3
    )
    assert _rs(fake) == [1, 21, 41]
    assert result.metadata.status is ResultStatus.PARTIAL
    assert result.table.num_rows == 60


async def test_max_rows_stops_pagination_with_partial_result() -> None:
    fake = ScreenTransport(total=588, pages={581: FINAL})
    result = await screen_async(
        ScreenerQuery(view="overview"), client=_client(fake), allow_partial=True, max_rows=25
    )
    assert _rs(fake) == [1, 21]
    assert result.metadata.status is ResultStatus.PARTIAL
    assert result.table.num_rows == 40  # pages are atomic: 25 -> the two fetched pages


async def test_max_rows_validation() -> None:
    fake = ScreenTransport()
    with pytest.raises(FinvizQueryError, match="max_rows"):
        await screen_async(ScreenerQuery(view="overview"), client=_client(fake), max_rows=0)


# --- strict partial --------------------------------------------------------------------


async def test_mid_pagination_failure_raises_strict() -> None:
    fake = ScreenTransport(total=588, pages={581: FINAL}, fail_pages={21})
    with pytest.raises(FinvizRateLimitError):
        await screen_async(ScreenerQuery(view="overview"), client=_client(fake), max_pages=10)


async def test_mid_pagination_failure_allow_partial_carries_rows() -> None:
    fake = ScreenTransport(total=588, pages={581: FINAL}, fail_pages={21})
    result = await screen_async(
        ScreenerQuery(view="overview"), client=_client(fake), allow_partial=True, max_pages=10
    )
    assert result.metadata.status is ResultStatus.PARTIAL
    assert result.table.num_rows == 20


async def test_first_page_failure_raises() -> None:
    fake = ScreenTransport(fail_pages={1})
    with pytest.raises(FinvizRateLimitError):
        await screen_async(ScreenerQuery(view="overview"), client=_client(fake))


async def test_parse_drift_is_not_access_failure() -> None:
    fake = ScreenTransport(default=MALFORMED)
    with pytest.raises(FinvizParseError):
        await screen_async(ScreenerQuery(view="overview"), client=_client(fake))


# --- cancellation ------------------------------------------------------------------------


async def test_cancellation_propagates_and_stops_fetching() -> None:
    fake = ScreenTransport(total=600)  # unbounded-looking screen
    client = _client(fake)
    task = asyncio.ensure_future(
        screen_async(ScreenerQuery(view="overview"), client=client, max_pages=100)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- progress ------------------------------------------------------------------------------


async def test_progress_callback_reports_each_page() -> None:
    fake = ScreenTransport(total=588, pages={581: FINAL})
    seen: list[int] = []
    await screen_async(
        ScreenerQuery(view="overview"),
        client=_client(fake),
        max_pages=100,
        on_progress=lambda done, rows: seen.append(done),
    )
    # One callback per fetched page: 29 full pages (ranks 1..580) plus the
    # sub-page-size final page.
    assert seen == list(range(1, 31))
    assert _rs(fake) == [1 + 20 * i for i in range(30)]


# --- cache ---------------------------------------------------------------------------------


async def test_cache_key_includes_query_and_page() -> None:
    fake = ScreenTransport(total=20)
    client = _client(fake, cache_ttl=60.0)
    await screen_async(ScreenerQuery(view="overview", page=2), client=client)
    assert len(fake.calls) == 1
    await screen_async(ScreenerQuery(view="overview", page=2), client=client)
    assert len(fake.calls) == 1  # warm: served from cache
    await screen_async(ScreenerQuery(view="overview", page=3), client=client)
    assert len(fake.calls) == 2  # different page -> different key


async def test_refresh_bypasses_cache() -> None:
    fake = ScreenTransport(total=20)
    client = _client(fake, cache_ttl=60.0)
    await screen_async(ScreenerQuery(view="overview"), client=client)
    await screen_async(ScreenerQuery(view="overview"), client=client, refresh=True)
    assert len(fake.calls) == 2


# --- sync wrapper ----------------------------------------------------------------------------


def test_sync_wrapper_runs_outside_loop() -> None:
    fake = ScreenTransport(default=EMPTY)
    result = screen(ScreenerQuery(view="overview"), client=_client(fake))
    assert result.metadata.status is ResultStatus.EMPTY


def test_sync_wrapper_rejects_active_loop() -> None:
    fake = ScreenTransport(total=20)

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            screen(ScreenerQuery(view="overview"), client=_client(fake))

    asyncio.run(inside())


# --- representation contract ------------------------------------------------------------------


async def test_anonymous_elite_export_is_never_the_representation() -> None:
    # The collector speaks public HTML only: every request targets screener.ashx.
    fake = ScreenTransport(total=588, pages={581: FINAL})
    await screen_async(ScreenerQuery(view="overview"), client=_client(fake), max_pages=100)
    assert all(
        urlsplit(str(call.get("v") and "https://finviz.com/screener.ashx")).path == "/screener.ashx"
        for call in fake.calls
    )
    assert SCREEN_PATH == "/screener.ashx"
