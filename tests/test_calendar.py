"""Economic calendar and release detail tests (Card 0.3-E).

RED-first: every test below fails until ``finvizp.calendar`` and
``finvizp._parsers.calendar`` exist. Hermetic: the transport double serves the
scrubbed fixture pages; no live HTTP.

Representation evidence (2026-08-30 bounded live probes, one request each):
``/calendar.ashx`` renders a SPA shell whose single
``<script id="route-init-data" type="application/json">`` payload carries the
complete current calendar (``data.entries``); no legacy ``table.calendar``
HTML remains on the page. ``/calendar/economic/detail/<RELEASE>`` embeds the
release metadata plus its history rows in ``data.table``; an unknown release
returns HTTP 404 (the client classifies it typed not-found). Sessions/
country/impact stay provider evidence: the payload has no country field
(verified), impact is an integer importance rank, and ``allDay`` entries have
no unambiguous clock time.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from finvizp.calendar import calendar_async, calendar_detail, calendar_detail_async
from finvizp.errors import FinvizNotFoundError, FinvizParseError, FinvizQueryError
from finvizp.results import FetchResult, ResultStatus

FIXTURES = Path(__file__).parent / "fixtures" / "calendar"
FETCHED_AT = dt.datetime(2026, 8, 30, 14, 30, tzinfo=dt.UTC)

CALENDAR_PATH = "/calendar.ashx"
DETAIL_PREFIX = "/calendar/economic/detail/"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


CURRENT_PAGE = _fixture("current-embedded.html")
DETAIL_PAGE = _fixture("detail.html")
NOT_FOUND_PAGE = _fixture("not-found.html")


class PageTransport:
    """Transport double serving one scripted body per requested path."""

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
        is_json = False
        return NormalizedResponse.from_backend(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=outcome.encode(),
            url=config.url,
            is_json=is_json,
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


def _payload(html: str) -> dict[str, Any]:
    match = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
    return match


# --- pure parser: representation proof ---------------------------------------------------------


def test_embedded_json_is_the_complete_representation() -> None:
    """The current page payload carries every reviewed field — no HTML fallback needed."""

    payload = _payload(CURRENT_PAGE)["data"]
    assert isinstance(payload["entries"], list) and payload["entries"]
    entry = payload["entries"][0]
    for field in (
        "calendarId",
        "ticker",
        "event",
        "category",
        "date",
        "reference",
        "referenceDate",
        "actual",
        "previous",
        "forecast",
        "importance",
        "allDay",
    ):
        assert field in entry
    # The verified representation has no country field: provider evidence only.
    assert "country" not in entry


def test_parse_calendar_page_rows_are_source_near() -> None:
    from finvizp._parsers.calendar import parse_calendar_page

    records = parse_calendar_page(
        CURRENT_PAGE, fetched_at=FETCHED_AT, on_warning=lambda warning: None
    )
    assert records.rows
    assert [row["event"] for row in records.rows] == [
        "CPI MoM",
        "Initial Jobless Claims",
        "FOMC Rate Decision",
    ]
    assert [row["symbol"] for row in records.rows] == ["USACPI", "UNITEDSTAJOBLES", "FDTR"]
    first = records.rows[0]
    # The parser hands over the ISO day string; the Arrow builder's date unit
    # converts it to date32.
    assert first["release_date"] == "2026-08-12"
    assert first["release_time"] == "08:30"
    assert first["importance"] == 3
    assert first["reference_period"] == "Jul"
    assert first["reference_date"] == "2026-07-31"
    assert first["actual"] == "0.2%"
    assert first["forecast"] == "0.2%"
    assert first["previous"] == "0.3%"


def test_parse_calendar_page_preserves_raw_displays() -> None:
    from finvizp._parsers.calendar import parse_calendar_page

    records = parse_calendar_page(
        CURRENT_PAGE, fetched_at=FETCHED_AT, on_warning=lambda warning: None
    )
    first = records.rows[0]
    # The source-near row keeps the provider's own value displays verbatim;
    # the split temporal shape is restored to raw displays by the endpoint
    # module (displays carry the exact ``date`` strings per row).
    assert first["actual"] == "0.2%"
    assert first["previous"] == "0.3%"
    assert records.displays["release_date"][0] == "2026-08-12T08:30:00"
    # Not-yet-released rows keep a null typed value.
    upcoming = records.rows[1]
    assert upcoming["actual"] is None


def test_parse_calendar_page_all_day_rows_have_no_clock_time() -> None:
    """allDay (or date-only) entries are date-only: time null, never invented."""
    from finvizp._parsers.calendar import parse_calendar_page

    page = _fixture("current-embedded.html").replace(
        '"date":"2026-08-14T18:00:00"', '"date":"2026-08-14"'
    )
    records = parse_calendar_page(page, fetched_at=FETCHED_AT, on_warning=lambda w: None)
    fomc = records.rows[-1]
    assert fomc["release_date"] == "2026-08-14"
    assert fomc["release_time"] is None


def test_parse_calendar_page_malformed_entries_is_drift() -> None:
    from finvizp._parsers.calendar import parse_calendar_page

    with pytest.raises(FinvizParseError, match="entries"):
        parse_calendar_page(
            _fixture("_drift-malformed-entries.html"),
            fetched_at=FETCHED_AT,
            on_warning=lambda warning: None,
        )


def test_parse_calendar_page_missing_entries_is_drift() -> None:
    from finvizp._parsers.calendar import parse_calendar_page

    with pytest.raises(FinvizParseError, match="entries"):
        parse_calendar_page(
            _fixture("_drift-missing-entries.html"),
            fetched_at=FETCHED_AT,
            on_warning=lambda warning: None,
        )


def test_parse_detail_page_rows_are_source_near() -> None:
    from finvizp._parsers.calendar import parse_release_detail_page

    records = parse_release_detail_page(
        DETAIL_PAGE, fetched_at=FETCHED_AT, on_warning=lambda warning: None
    )
    assert records.category == "Consumer Price Index"
    assert [row["reference_period"] for row in records.rows] == ["Aug", "Jul", "Jun"]
    released = records.rows[1]
    assert released["release_date"] == "2026-08-12"
    assert released["actual"] == "0.2%"
    upcoming = records.rows[0]
    assert upcoming["actual"] is None


# --- dataset / Arrow table ---------------------------------------------------------------------


async def test_calendar_returns_registered_arrow_table() -> None:
    client = _client({CALENDAR_PATH: CURRENT_PAGE})
    result = await calendar_async(client=client)
    assert isinstance(result, FetchResult)
    table = result.table
    assert isinstance(table, pa.Table)
    from finvizp import arrow as fa

    assert table.schema.names == list(fa.dataset_field_names("economic_calendar"))
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.endpoint == CALENDAR_PATH
    assert table.num_rows == 3
    rows = table.to_pylist()
    assert rows[0]["symbol"] == "USACPI"
    assert rows[0]["release_date"] == dt.date(2026, 8, 12)
    assert rows[0]["importance"] == 3
    assert rows[0]["actual"] == pytest.approx(0.2)
    assert rows[0]["actual_raw"] == "0.2%"
    assert rows[1]["actual"] is None
    assert rows[2]["event"] == "FOMC Rate Decision"


async def test_calendar_value_fields_convert_with_raw_companions() -> None:
    """Comma/percent/compact displays normalize; raw keeps the exact display."""
    client = _client({CALENDAR_PATH: CURRENT_PAGE})
    table = (await calendar_async(client=client)).table
    previous = table.to_pylist()[0]
    assert previous["previous"] == pytest.approx(0.3)
    assert previous["previous_raw"] == "0.3%"
    assert previous["release_date_raw"] == "2026-08-12T08:30:00"
    # reference_date is date32
    assert table.schema.field("reference_date").type == pa.date32()
    # release_time is text (HH:MM), not a timestamp: no invented date anchoring
    assert table.schema.field("release_time").type == pa.string()
    # Date-only rows keep the raw display and a null time/timestamp.
    assert table.to_pylist()[1]["previous_raw"] == "211K"
    # release_timestamp: exact US Eastern -> UTC (08:30 EDT = 12:30 UTC)
    stamp = previous["release_timestamp"]
    assert stamp == dt.datetime(2026, 8, 12, 12, 30, tzinfo=dt.UTC)
    assert table.schema.field("release_timestamp").type == pa.timestamp("us", tz="UTC")


async def test_detail_raw_companions_preserve_verbatim_displays() -> None:
    """Detail rows restore the provider's exact ``date`` displays per row."""
    client = _client({DETAIL_PREFIX + "USACPI": DETAIL_PAGE})
    rows = (await calendar_detail_async("USACPI", client=client)).table.to_pylist()
    assert [row["release_date_raw"] for row in rows] == [
        "2026-09-11T08:30:00",
        "2026-08-12T08:30:00",
        "2026-07-14T08:30:00",
    ]
    assert rows[1]["release_time"] == "08:30"
    assert rows[1]["release_timestamp"] == dt.datetime(2026, 8, 12, 12, 30, tzinfo=dt.UTC)


async def test_calendar_recognized_empty_result() -> None:
    """Zero entries is a positively recognized empty, not drift."""
    page = _page_with_entries([])
    client = _client({CALENDAR_PATH: page})
    result = await calendar_async(client=client)
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0
    from finvizp import arrow as fa

    assert result.table.schema.names == list(fa.dataset_field_names("economic_calendar"))


def _page_with_entries(entries: list[dict[str, Any]]) -> str:
    payload = {"data": {"initialDateFrom": "2026-08-31", "entries": entries}, "version": 3}
    body = json.dumps(payload, separators=(",", ":"))
    return (
        "<!DOCTYPE html><html><head><title>Economic Calendar</title></head><body>"
        '<script id="route-init-data" type="application/json">'
        f"{body}</script></body></html>"
    )


async def test_calendar_malformed_payload_raises_parse_error() -> None:
    client = _client({CALENDAR_PATH: _fixture("_drift-malformed-entries.html")})
    with pytest.raises(FinvizParseError):
        await calendar_async(client=client)


# --- explicit release slug validation ----------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "usacpi", "USACPI/x", "a b", "x" * 65, None])
async def test_detail_rejects_invalid_slugs_before_network(bad: str | None) -> None:
    transport = StatusTransport(DETAIL_PREFIX + "USACPI", 200, DETAIL_PAGE)

    async def refusing(config: Any, stream_callback: Any = None) -> Any:  # pragma: no cover
        raise AssertionError("network reached")

    transport.request = refusing  # type: ignore[method-assign]
    from finvizp.client import FinvizClient

    client = FinvizClient(transport=transport, retry_attempts=0)
    with pytest.raises(FinvizQueryError, match="release"):
        await calendar_detail_async(bad, client=client)  # type: ignore[arg-type]
    assert transport.calls == []


async def test_detail_fetches_only_the_explicit_release() -> None:
    calls: list[Any] = []
    transport = StatusTransport(DETAIL_PREFIX + "USACPI", 200, DETAIL_PAGE)

    original_request = transport.request

    async def recording(config: Any, stream_callback: Any = None) -> Any:
        calls.append(config)
        return await original_request(config, stream_callback)

    transport.request = recording  # type: ignore[method-assign]
    from finvizp.client import FinvizClient

    client = FinvizClient(transport=transport, retry_attempts=0)
    result = await calendar_detail_async("USACPI", client=client)
    assert [c.url for c in calls] == ["https://finviz.com/calendar/economic/detail/USACPI"]
    table = result.table
    from finvizp import arrow as fa

    assert table.schema.names == list(fa.dataset_field_names("economic_details"))
    assert table.num_rows == 3
    rows = table.to_pylist()
    assert all(row["symbol"] == "USACPI" for row in rows)
    assert rows[1]["actual"] == pytest.approx(0.2)


async def test_detail_unknown_release_is_typed_not_found() -> None:
    from finvizp.client import FinvizClient

    transport = StatusTransport(DETAIL_PREFIX + "NOSUCHSLUGXYZ", 404, NOT_FOUND_PAGE)
    client = FinvizClient(transport=transport, retry_attempts=0)
    with pytest.raises(FinvizNotFoundError):
        await calendar_detail_async("NOSUCHSLUGXYZ", client=client)


def test_detail_sync_wrapper_matches_async() -> None:
    from finvizp.client import FinvizClient

    transport = StatusTransport(DETAIL_PREFIX + "USACPI", 200, DETAIL_PAGE)
    client = FinvizClient(transport=transport, retry_attempts=0)
    result = calendar_detail("USACPI", client=client)
    assert result.table.num_rows == 3


def test_calendar_has_no_enumeration_surface() -> None:
    """Detail takes one explicit release; the module exposes no list-all API."""
    # ``finvizp.calendar`` the curated export shadows the module attribute, so
    # resolve the module through sys.modules (shadowing-proof, as in test_news).
    import importlib

    cal = importlib.import_module("finvizp.calendar")

    public = {name for name in cal.__all__ if not name.endswith(("_async",))}
    assert {"calendar", "calendar_detail"}.issubset(public)
    assert not any("list" in name or "all" in name or "sitemap" in name for name in cal.__all__)


# --- cache -------------------------------------------------------------------------------------


async def test_calendar_cache_reuse_without_new_request() -> None:
    client = _client({CALENDAR_PATH: CURRENT_PAGE})
    client._cache_ttl = 60.0
    first = await calendar_async(client=client)
    second = await calendar_async(client=client)
    assert client._transport.calls and len(client._transport.calls) == 1
    assert second.metadata.cache_hit is True
    assert second.metadata.fetched_at == first.metadata.fetched_at


async def test_detail_and_calendar_have_distinct_cache_keys() -> None:
    client = _client({CALENDAR_PATH: CURRENT_PAGE, DETAIL_PREFIX + "USACPI": DETAIL_PAGE})
    client._cache_ttl = 60.0
    await calendar_async(client=client)
    result = await calendar_detail_async("USACPI", client=client)
    assert result.metadata.cache_hit is False
    assert len(client._transport.calls) == 2


async def test_calendar_cache_false_requests_without_storing() -> None:
    client = _client({CALENDAR_PATH: CURRENT_PAGE})
    client._cache_ttl = 60.0
    await calendar_async(client=client, cache=False)
    await calendar_async(client=client, cache=False)
    assert len(client._transport.calls) == 2
    # cache=False left nothing in the cache: a default call re-fetches.
    third = await calendar_async(client=client)
    assert third.metadata.cache_hit is False
    assert len(client._transport.calls) == 3


async def test_detail_cache_false_requests_without_storing() -> None:
    client = _client({DETAIL_PREFIX + "USACPI": DETAIL_PAGE})
    client._cache_ttl = 60.0
    await calendar_detail_async("USACPI", client=client, cache=False)
    await calendar_detail_async("USACPI", client=client, cache=False)
    assert len(client._transport.calls) == 2
    third = await calendar_detail_async("USACPI", client=client)
    assert third.metadata.cache_hit is False
    assert len(client._transport.calls) == 3


# --- sync wrapper ------------------------------------------------------------------------------


def test_calendar_sync_wrapper_runs_outside_loop() -> None:
    from finvizp.calendar import calendar

    client = _client({CALENDAR_PATH: CURRENT_PAGE})
    result = calendar(client=client)
    assert result.table.num_rows == 3


def test_calendar_sync_wrapper_rejects_active_loop() -> None:
    from finvizp.calendar import calendar

    client = _client({CALENDAR_PATH: CURRENT_PAGE})

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            calendar(client=client)

    asyncio.run(inside())
