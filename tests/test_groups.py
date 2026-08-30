"""Groups family: pure parser, public collector, spectrum descriptors.

RED-first: every test fails until ``finvizp._parsers.groups`` and
``finvizp.groups`` exist. Hermetic: the transport double serves the scrubbed
fixture pages; no live HTTP. The spectrum regression proves valid typed input
never reaches finvizfinance's broken ``order_dict`` attribute path (it has no
``order_dict`` to mis-call) while describing the artifact without bytes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp._parsers.groups import GroupPage, parse_groups_page, parse_spectrum_page
from finvizp._queries.groups import GroupColumn, GroupOrder, GroupQuery, GroupView
from finvizp.client import FinvizClient
from finvizp.errors import FinvizNotFoundError, FinvizParseError
from finvizp.groups import GROUPS_PATH, SPECTRUM_VIEW_CODE, group, group_async, spectrum_async
from finvizp.models import Artifact
from finvizp.results import FetchResult, ResultStatus

FIXTURES = Path(__file__).parent / "fixtures" / "groups"

OVERVIEW = (FIXTURES / "overview.html").read_text("utf-8")
VALUATION = (FIXTURES / "valuation.html").read_text("utf-8")
SPECTRUM = (FIXTURES / "spectrum.html").read_text("utf-8")
EMPTY = (FIXTURES / "empty.html").read_text("utf-8")
NO_NAME = (FIXTURES / "_drift-no-name.html").read_text("utf-8")
MALFORMED = (FIXTURES / "_drift-malformed-row.html").read_text("utf-8")
DUPLICATE = (FIXTURES / "_drift-duplicate-table.html").read_text("utf-8")
HEADER_DRIFT = (FIXTURES / "_drift-header.html").read_text("utf-8")
SPECTRUM_DRIFT = (FIXTURES / "_drift-spectrum-missing-image.html").read_text("utf-8")

GROUPS_URL = "https://finviz.com/groups.ashx"


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
        url=url or GROUPS_URL,
        is_json=False,
    )


class GroupsTransport(Backend):
    """Serves one fixture body per view code; records every request."""

    def __init__(
        self,
        *,
        bodies: dict[str, str] | None = None,
        fail_views: set[str] | None = None,
    ) -> None:
        self.bodies = dict(bodies or {})
        self.fail_views = set(fail_views or set())
        self.calls: list[dict[str, str]] = []

    @property
    def name(self) -> str:
        return "groups-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        params = {str(k): str(v) for k, v in (config.params or {}).items()}
        self.calls.append(params)
        view = params.get("v", "110")
        if view in self.fail_views:
            return _html("provider failure", status=500, url=str(config.url))
        body = self.bodies.get(view, OVERVIEW)
        return _html(body, url=str(config.url))

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> GroupsTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _client(fake: GroupsTransport, **kwargs: Any) -> FinvizClient:
    kwargs.setdefault("retry_attempts", 0)
    kwargs.setdefault("retry_backoff", 0.0)
    return FinvizClient(transport=fake, **kwargs)


# --- pure parser -----------------------------------------------------------------


def test_parser_reads_header_driven_columns() -> None:
    page = parse_groups_page(OVERVIEW)
    assert isinstance(page, GroupPage)
    assert not page.is_empty
    assert page.columns == (
        "No.",
        "Name",
        "Stocks",
        "Market Cap",
        "Dividend",
        "P/E",
        "Fwd P/E",
        "PEG",
        "LTDebt/Eq",
        "Debt/Eq",
        "Float Short",
        "Recom",
        "Change %",
        "Volume",
    )


def test_parser_rows_carry_index_name_and_raw_displays() -> None:
    page = parse_groups_page(OVERVIEW)
    first = page.rows[0]
    assert first.index == 2
    assert first.name == "Communication Services"
    assert first.raw == (
        "258",
        "12551.11B",
        "0.61%",
        "18.00",
        "19.71",
        "1.00",
        "0.59",
        "0.65",
        "3.15%",
        "1.40",
        "1.44%",
        "495.43M",
    )
    assert page.rows[-1].name == "Basic Materials"


def test_parser_fingerprint_is_stable_and_content_free() -> None:
    assert parse_groups_page(OVERVIEW).fingerprint == parse_groups_page(OVERVIEW).fingerprint
    assert parse_groups_page(OVERVIEW).fingerprint != parse_groups_page(VALUATION).fingerprint


def test_parser_recognized_empty_state() -> None:
    page = parse_groups_page(EMPTY)
    assert page.is_empty
    assert page.rows == ()


def test_parser_missing_name_column_is_drift() -> None:
    with pytest.raises(FinvizParseError, match="Name"):
        parse_groups_page(NO_NAME)


def test_parser_row_arity_mismatch_is_drift() -> None:
    with pytest.raises(FinvizParseError, match="column count"):
        parse_groups_page(MALFORMED)


def test_parser_duplicate_tables_are_drift() -> None:
    with pytest.raises(FinvizParseError, match="groups_table"):
        parse_groups_page(DUPLICATE)


def test_parser_missing_header_is_drift() -> None:
    with pytest.raises(FinvizParseError, match="header"):
        parse_groups_page(HEADER_DRIFT)


def test_spectrum_parser_reads_descriptor_without_bytes() -> None:
    descriptor = parse_spectrum_page(SPECTRUM)
    assert isinstance(descriptor, Artifact)
    assert descriptor.kind == "image"
    assert descriptor.media_type == "image/png"
    assert descriptor.source_url.endswith("grp_image?spectrum_sector.png&rev=123")
    assert descriptor.chart_type == "spectrum"
    assert descriptor.content_hash is None  # bytes are never fetched here


def test_spectrum_parser_missing_image_is_drift() -> None:
    with pytest.raises(FinvizParseError, match="spectrum"):
        parse_spectrum_page(SPECTRUM_DRIFT)


# --- legacy spectrum regression ----------------------------------------------------


def test_legacy_broken_order_dict_path_is_never_used() -> None:
    """finvizfinance's Spectrum treats group_order_dict as an object with an
    ``order_dict`` attribute even though it is a dictionary; a valid call
    raised AttributeError. finvizp's typed query path must never grow such an
    attribute access: the provider params come from the checked-in registry,
    and the artifact descriptor resolves without any ``order_dict``."""
    with pytest.raises(AttributeError):
        # The legacy defect, reproduced: a plain dict has no order_dict attr.
        {"Name": "name"}.order_dict  # type: ignore[attr-defined]  # noqa: B018
    query = GroupQuery(view="spectrum", order=GroupOrder("Market Capitalization"))
    params = query.provider_params()
    assert params["o"] == "marketcap" and params["v"] == SPECTRUM_VIEW_CODE


def test_spectrum_result_carries_descriptor_not_table() -> None:
    fake = GroupsTransport(bodies={"310": SPECTRUM})
    result = asyncio.run(
        spectrum_async(GroupQuery(view=GroupView("spectrum")), client=_client(fake))
    )
    assert isinstance(result.data, Artifact)
    with pytest.raises(Exception, match="table"):
        result.table  # noqa: B018  # descriptor results are not tables


# --- collector: one explicit group page ---------------------------------------------


async def test_group_async_returns_header_driven_wide_table() -> None:
    fake = GroupsTransport()
    result = await group_async(GroupQuery(), client=_client(fake))
    assert isinstance(result, FetchResult)
    assert fake.calls[0] == {"v": "110", "g": "sector", "o": "name", "st": "d1"}
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.endpoint == GROUPS_PATH
    table = result.table
    assert table.column_names[0] == "rank"
    assert table.column_names[1] == "name"
    assert "market_cap" in table.column_names
    assert "market_cap_raw" in table.column_names
    assert "fetched_at" in table.column_names
    assert table.column("name").to_pylist()[0] == "Communication Services"
    # compact unit: 12551.11B -> base units; percent: 0.61% -> 0.0061
    mc = table.column("market_cap").to_pylist()[0]
    div = table.column("dividend").to_pylist()[0]
    assert mc == pytest.approx(12551.11e9)
    assert div == pytest.approx(0.0061)


async def test_group_query_provenance_and_raw_companions() -> None:
    fake = GroupsTransport(bodies={"152": VALUATION})
    query = GroupQuery(dimension="Industry", view="custom", columns=[GroupColumn("Market Cap")])
    result = await group_async(query, client=_client(fake))
    assert fake.calls[0]["g"] == "industry"
    assert fake.calls[0]["v"] == "152"
    # The provider always renders No. (0) and Name (1) first, live-verified.
    assert fake.calls[0]["c"] == "0,1,2"
    assert result.metadata.query["v"] == "152"
    assert result.metadata.query["g"] == "industry"
    raw = result.table.column("market_cap_raw").to_pylist()
    assert raw == ["12551.11B", "21400.15B"]


async def test_group_custom_view_missing_requested_column_is_drift() -> None:
    # The query asks for a column the served page does not render.
    fake = GroupsTransport(bodies={"152": VALUATION})
    with pytest.raises(FinvizParseError, match="column"):
        await group_async(
            GroupQuery(view="custom", columns=[GroupColumn("Market Cap"), GroupColumn("Recom")]),
            client=_client(fake),
        )


async def test_group_recognized_empty_result() -> None:
    fake = GroupsTransport(bodies={"110": EMPTY})
    result = await group_async(GroupQuery(), client=_client(fake))
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0


async def test_group_transport_failure_raises_typed_error() -> None:
    fake = GroupsTransport(fail_views={"110"})
    with pytest.raises(Exception) as excinfo:
        await group_async(GroupQuery(), client=_client(fake))
    assert not isinstance(excinfo.value, FinvizParseError)


async def test_group_not_found_soft_404_propagates() -> None:
    class NotFoundTransport(GroupsTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            self.calls.append({})
            return _html("<html><head><title>Page was not found</title></head></html>")

    with pytest.raises(FinvizNotFoundError):
        await group_async(GroupQuery(), client=_client(NotFoundTransport()))


async def test_group_parse_drift_raises() -> None:
    fake = GroupsTransport(bodies={"110": MALFORMED})
    with pytest.raises(FinvizParseError):
        await group_async(GroupQuery(), client=_client(fake))


async def test_group_cache_key_includes_query() -> None:
    fake = GroupsTransport()
    client = _client(fake, cache_ttl=60.0)
    await group_async(GroupQuery(), client=client)
    await group_async(GroupQuery(), client=client)
    assert len(fake.calls) == 1  # warm: served from cache
    await group_async(GroupQuery(dimension="Country"), client=client)
    assert len(fake.calls) == 2  # different dimension -> different key
    await group_async(GroupQuery(), client=client, refresh=True)
    assert len(fake.calls) == 3


async def test_cancellation_propagates() -> None:
    started = asyncio.Event()

    class SlowBackend(GroupsTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            started.set()
            await asyncio.sleep(30)
            raise AssertionError("never reached")  # pragma: no cover

    fake = SlowBackend()
    task = asyncio.ensure_future(group_async(GroupQuery(), client=_client(fake)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_sync_wrapper_runs_outside_loop() -> None:
    fake = GroupsTransport(bodies={"110": EMPTY})
    result = group(GroupQuery(), client=_client(fake))
    assert result.metadata.status is ResultStatus.EMPTY


def test_sync_wrapper_rejects_active_loop() -> None:
    fake = GroupsTransport()

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            group(GroupQuery(), client=_client(fake))

    asyncio.run(inside())


async def test_public_html_is_the_representation_never_grp_export() -> None:
    fake = GroupsTransport()
    await group_async(GroupQuery(), client=_client(fake))
    assert GROUPS_PATH == "/groups.ashx"
    assert all("grp_export" not in str(call) for call in fake.calls)
