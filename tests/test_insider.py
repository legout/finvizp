"""Tests for global, fund, and manager insider feeds (Card 0.3-D).

RED-first: every test below fails until ``finvizp.insider`` exists. Hermetic:
the transport double serves the scrubbed fixture pages; no live HTTP.

Live evidence (bounded probes 2026-08-30 through the planned transport):
- the global feed is one 200-row window per request; ``b=N`` pages repeat the
  identical rows, so pagination is refused as provider repeated state;
- fund/manager pages render no HTML tables at all: the entire contract is the
  embedded ``initialFundDetailsData``/``initialManagerDetailsData`` JSON
  (quarterly 13F/N-PORT portfolio disclosures with allocation history and
  top buy/sell observations), not Form-4 events;
- an unknown fund/manager slug is a provider 404 -> typed FinvizNotFoundError.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp.client import FinvizClient
from finvizp.errors import FinvizNotFoundError, FinvizQueryError
from finvizp.insider import (
    INSIDER_FEEDS,
    fund_async,
    fund_insider,
    global_async,
    global_insider,
    manager_async,
    manager_insider,
)
from finvizp.results import ResultStatus

BASE = "https://finviz.com"
FIXTURES = Path(__file__).parent / "fixtures" / "insider"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def _html(
    body: str,
    *,
    status: int = 200,
    url: str | None = None,
) -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content=body.encode(),
        url=url or f"{BASE}/insidertrading.ashx",
        is_json=False,
    )


class InsiderTransport(Backend):
    """Records query params per path prefix; serves scripted bodies per path."""

    def __init__(self, bodies: dict[str, str], *, not_found: set[str] | None = None) -> None:
        self.bodies = bodies
        self.not_found = set(not_found or ())
        self.calls: list[tuple[str, dict[str, str]]] = []

    @property
    def name(self) -> str:
        return "insider-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        from urllib.parse import urlsplit

        parts = urlsplit(str(config.url))
        params = {str(k): str(v) for k, v in (config.params or {}).items()}
        self.calls.append((parts.path, params))
        if parts.path in self.not_found:
            return _html("<title>Page was not found</title>", url=str(config.url), status=200)
        body = self.bodies[parts.path]
        return _html(body, url=str(config.url))

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> InsiderTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _client(fake: InsiderTransport, **kwargs: Any) -> FinvizClient:
    kwargs.setdefault("retry_attempts", 0)
    return FinvizClient(transport=fake, **kwargs)


GLOBAL = _fixture("global.html")
FUND = _fixture("fund.html")
MANAGER = _fixture("manager.html")


def _insider_transport() -> InsiderTransport:
    return InsiderTransport(
        {
            "/insidertrading.ashx": GLOBAL,
            "/insidertrading/funds/na-0000002230": FUND,
            "/insidertrading/managers/kingdon-capital-management-llc-1000097": MANAGER,
        }
    )


# --- feed variants: typed, pre-network, explicit -------------------------------------


def test_feed_registry_is_explicit_and_closed() -> None:
    assert set(INSIDER_FEEDS) == {
        "latest",
        "latest_buys",
        "latest_sales",
        "top_week",
        "top_week_buys",
        "top_week_sales",
        "top_owner_trade",
        "top_owner_buys",
        "top_owner_sales",
    }


async def test_unknown_feed_rejected_before_network() -> None:
    fake = _insider_transport()
    with pytest.raises(FinvizQueryError, match="feed"):
        await global_async(feed="everything", client=_client(fake))
    assert fake.calls == []


async def test_latest_feed_encodes_no_query_params() -> None:
    fake = _insider_transport()
    await global_async(feed="latest", client=_client(fake))
    path, params = fake.calls[0]
    assert path == "/insidertrading.ashx"
    assert params == {}


async def test_buys_and_sales_map_to_tc_codes() -> None:
    fake = _insider_transport()
    await global_async(feed="latest_buys", client=_client(fake))
    await global_async(feed="latest_sales", client=_client(fake))
    assert fake.calls[0][1] == {"tc": "1"}
    assert fake.calls[1][1] == {"tc": "2"}


async def test_top_week_variants_encode_registry_query() -> None:
    fake = _insider_transport()
    await global_async(feed="top_week", client=_client(fake))
    assert fake.calls[0][1] == {
        "or": "-10",
        "tv": "100000",
        "tc": "7",
        "o": "-transactionValue",
    }
    await global_async(feed="top_owner_sales", client=_client(fake))
    assert fake.calls[1][1]["or"] == "10"
    assert fake.calls[1][1]["tv"] == "1000000"
    assert fake.calls[1][1]["tc"] == "2"


# --- global rows: values/raw fields/links/semantics ----------------------------------


async def test_global_rows_parse_typed_values_with_raw_displays() -> None:
    result = await global_async(client=_client(_insider_transport()))
    table = result.table
    assert table.num_rows == 3
    rows = table.to_pylist()
    first = rows[0]
    assert first["symbol"] == "VTSI"
    assert first["owner"] == "BARBER GRANT"
    assert first["relationship"] == "Director"
    assert first["transaction_date"] == dt.date(2026, 8, 27)
    assert first["transaction_type"] == "Buy"
    assert first["cost"] == 3.05
    assert first["cost_raw"] == "3.05"
    assert first["shares"] == 2000.0
    assert first["shares_raw"] == "2,000"
    assert first["value"] == 6100.0
    assert first["value_raw"] == "6,100"
    assert first["sec_form_url"].startswith("http://www.sec.gov/")


async def test_global_rows_preserve_provider_order() -> None:
    table = (await global_async(client=_client(_insider_transport()))).table
    assert table.column("owner").to_pylist() == [
        "BARBER GRANT",
        "SUBIN NEIL S",
        "Fesko John",
    ]
    assert table.column("transaction_type").to_pylist() == ["Buy", "Sale", "Sale"]


async def test_owner_link_routes_to_the_owner_feed_not_crawled() -> None:
    # The Owner cell's own href (insidertrading?oc=...) is relationship
    # context, not a page to follow; only the SEC form link is recorded.
    fake = _insider_transport()
    result = await global_async(client=_client(fake))
    assert len(fake.calls) == 1
    assert all(row["sec_form_url"] for row in result.table.to_pylist())


async def test_global_table_matches_registered_quote_insider_contract() -> None:
    from finvizp import arrow as fa
    from finvizp import schemas

    result = await global_async(client=_client(_insider_transport()))
    assert result.table.schema == schemas.arrow_schema("quote_insider")
    assert result.table.schema.names == list(fa.dataset_field_names("quote_insider"))


async def test_global_feed_single_request_no_repeat_walk() -> None:
    fake = _insider_transport()
    await global_async(client=_client(fake))
    assert len(fake.calls) == 1


# --- not found versus recognized empty ------------------------------------------------


async def test_not_found_title_is_typed_not_found() -> None:
    fake = InsiderTransport({}, not_found={"/insidertrading.ashx"})
    with pytest.raises(FinvizNotFoundError):
        await global_async(client=_client(fake))


async def test_page_without_insider_table_is_parse_drift() -> None:
    from finvizp.errors import FinvizParseError

    fake = InsiderTransport({"/insidertrading.ashx": "<html><body><p>nothing</p></body></html>"})
    with pytest.raises(FinvizParseError, match="insider"):
        await global_async(client=_client(fake))


# --- pagination safety: b=N repeated state -------------------------------------------


async def test_explicit_page_request_is_refused_as_repeated_state() -> None:
    # Live evidence: b=2/b=3 return the identical 200-row window; asking for a
    # "page" would silently re-serve the same rows, so the operation exposes
    # no page argument at all (single-window feed) — enforced by signature.
    with pytest.raises(TypeError):
        await global_async(page=2, client=_client(_insider_transport()))  # type: ignore[call-arg]


async def test_global_result_is_complete_single_window() -> None:
    fake = _insider_transport()
    result = await global_async(client=_client(fake))
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.endpoint == "/insidertrading.ashx"
    assert dict(result.metadata.query) == {}


# --- fund / manager: explicit identifiers, embedded JSON ------------------------------


async def test_fund_page_parses_embedded_portfolio_json() -> None:
    fake = _insider_transport()
    result = await fund_async("na-0000002230", client=_client(fake))
    assert fake.calls == [("/insidertrading/funds/na-0000002230", {})]
    data = result.data
    details = data["details"]
    assert details["filer"]["investorId"] == "0000002230"
    assert details["filer"]["investorType"] == "nport_fund"
    assert details["filer"]["filerName"] == "SAMPLE DIVERSIFIED EQUITY FUND, INC."
    assert details["filer"]["filerTicker"] == "ADX"
    assert details["latestSummary"]["reportDate"] == "2026-06-30"
    assert details["latestSummary"]["totalSumOfUsdValue"] == 3297262771
    assert details["latestSummary"]["totalCountOfInvestments"] == 107
    assert details["latestSummary"]["countOfNewPurchased"] == 21
    assert details["latestSummary"]["countOfSoldOut"] == 15
    assert details["latestSummary"]["countOfAddedTo"] == 12
    assert details["latestSummary"]["countOfReduced"] == 27
    assert details["topBuysUsd"][0]["ticker"] == "AMD"
    assert details["topSellsUsd"][0]["ticker"] == "XLV"
    assert details["countryAllocations"][0]["quarter"] == "Q2 2026"
    assert data["report_dates"][0] == "2026-06-30"
    assert "2026-03-31" in data["report_dates"]


async def test_manager_page_parses_embedded_portfolio_json() -> None:
    fake = _insider_transport()
    result = await manager_async("kingdon-capital-management-llc-1000097", client=_client(fake))
    assert fake.calls == [
        ("/insidertrading/managers/kingdon-capital-management-llc-1000097", {}),
    ]
    details = result.data["details"]
    assert details["filer"]["investorId"] == "1000097"
    assert details["filer"]["investorType"] == "manager_13f"
    assert details["filer"]["filerName"] == "KINGDOM SAMPLE MANAGEMENT, L.L.C."
    assert details["latestSummary"]["totalSumOfUsdValue"] == 976167326
    assert details["topBuysUsd"][0]["ticker"] == "SOXX"
    assert details["topBuysUsd"][0]["putCall"] == "put"
    assert details["sectorAllocations"][0]["sector"] == "Manufacturing"


async def test_fund_and_manager_identifiers_are_validated_pre_network() -> None:
    fake = _insider_transport()
    for bad in ("", "Not A Slug!", "../etc", "a" * 200):
        with pytest.raises(FinvizQueryError):
            await fund_async(bad, client=_client(fake))
        with pytest.raises(FinvizQueryError):
            await manager_async(bad, client=_client(fake))
    assert fake.calls == []


async def test_unknown_fund_or_manager_slug_is_typed_not_found() -> None:
    fake = InsiderTransport(
        _insider_transport().bodies,
        not_found={
            "/insidertrading/funds/na-0000002230",
            "/insidertrading/managers/kingdon-capital-management-llc-1000097",
        },
    )
    with pytest.raises(FinvizNotFoundError):
        await fund_async("na-0000002230", client=_client(fake))
    with pytest.raises(FinvizNotFoundError):
        await manager_async("kingdon-capital-management-llc-1000097", client=_client(fake))


async def test_fund_page_without_embedded_json_is_parse_drift() -> None:
    from finvizp.errors import FinvizParseError

    fake = InsiderTransport({"/insidertrading/funds/na-0000002230": "<html><body></body></html>"})
    with pytest.raises(FinvizParseError, match="fund"):
        await fund_async("na-0000002230", client=_client(fake))


# --- disclosure payload is frozen / honest event vs relationship semantics -------------


async def test_disclosure_payload_is_frozen_not_dict() -> None:
    from types import MappingProxyType

    result = await fund_async("na-0000002230", client=_client(_insider_transport()))
    assert isinstance(result.data, MappingProxyType)
    assert isinstance(result.data["details"], MappingProxyType)
    assert isinstance(result.data["details"]["topBuysUsd"], tuple)
    assert isinstance(result.data["details"]["countryAllocations"][0], MappingProxyType)


async def test_disclosure_payload_is_verbatim_provider_json() -> None:
    # Contract guard: the fixture really is the provider shape (embedded JSON
    # in a script tag, zero HTML tables) and the result carries it verbatim
    # (plus report_dates) — no reshaping, no invented typed columns.
    for fixture, root in ((FUND, "initialFundDetailsData"), (MANAGER, "initialManagerDetailsData")):
        assert "<table" not in fixture.lower()
        payload = json.loads(fixture.split("<script>", 1)[1].split("</script>", 1)[0])
        assert root in payload
    fund = await fund_async("na-0000002230", client=_client(_insider_transport()))
    provider = json.loads(FUND.split("<script>", 1)[1].split("</script>", 1)[0])
    served = dict(fund.data)
    served_report_dates = served.pop("report_dates", None)
    # json round-trip compares shapes, not container types (freeze -> tuples);
    # MappingProxyType needs a default encoder.
    served_json = json.dumps(served, sort_keys=True, default=dict)
    provider_json = json.dumps({"details": provider["initialFundDetailsData"]}, sort_keys=True)
    assert served_json == provider_json
    assert list(served_report_dates) == provider["initialFundReportDates"]


async def test_no_identifier_listing_operation_exists() -> None:
    import finvizp.insider as mod

    public = {name for name in dir(mod) if not name.startswith("_")}
    assert not any("list" in name or "search" in name or "sitemap" in name for name in public)


async def test_fund_manager_never_request_sitemaps() -> None:
    fake = _insider_transport()
    await fund_async("na-0000002230", client=_client(fake))
    await manager_async("kingdon-capital-management-llc-1000097", client=_client(fake))
    assert all(path.startswith("/insidertrading/") for path, _ in fake.calls)


# --- cache / refresh / sync ------------------------------------------------------------


async def test_identical_fund_calls_share_one_transport_request_via_cache() -> None:
    fake = _insider_transport()
    client = _client(fake, cache_ttl=60.0)
    first = await fund_async("na-0000002230", client=client)
    second = await fund_async("na-0000002230", client=client)
    assert len(fake.calls) == 1
    assert second.metadata.cache_hit is True
    assert second.metadata.fetched_at == first.metadata.fetched_at


async def test_refresh_bypasses_cache() -> None:
    fake = _insider_transport()
    client = _client(fake, cache_ttl=60.0)
    await fund_async("na-0000002230", client=client)
    await fund_async("na-0000002230", client=client, refresh=True)
    assert len(fake.calls) == 2


async def test_cache_false_bypasses_cache() -> None:
    fake = _insider_transport()
    client = _client(fake, cache_ttl=60.0)
    await fund_async("na-0000002230", client=client, cache=False)
    await fund_async("na-0000002230", client=client, cache=False)
    assert len(fake.calls) == 2


def test_sync_wrappers_run_outside_loop() -> None:
    result = global_insider(client=_client(_insider_transport()))
    assert result.metadata.status is ResultStatus.COMPLETE
    fund = fund_insider("na-0000002230", client=_client(_insider_transport()))
    assert fund.data["details"]["filer"]["investorType"] == "nport_fund"
    row = manager_insider(
        "kingdon-capital-management-llc-1000097", client=_client(_insider_transport())
    ).data
    assert row["details"]["filer"]["investorType"] == "manager_13f"


def test_sync_wrappers_reject_active_loop() -> None:
    fake = _insider_transport()

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            global_insider(client=_client(fake))

    asyncio.run(inside())


# --- strict_schema / builder contract ---------------------------------------------------


async def test_strict_schema_passes_on_clean_fixture() -> None:
    result = await global_async(client=_client(_insider_transport()), strict_schema=True)
    assert result.metadata.warnings == ()
