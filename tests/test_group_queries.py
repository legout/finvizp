"""Typed group query models: dimensions, views, orders, custom columns.

RED-first: every test fails until ``finvizp._queries.groups`` exists. Queries
are immutable and validated before any network I/O against a checked-in
registry whose provider codes were verified against the live 2026-08-30
``/groups`` surface (source ledger + bounded probes).
"""

from __future__ import annotations

import json

import pytest

from finvizp._queries.groups import (
    GroupColumn,
    GroupDimension,
    GroupOrder,
    GroupQuery,
    GroupView,
    groups_registry,
)
from finvizp.errors import FinvizQueryError

REG = groups_registry()


# --- registry ---------------------------------------------------------------


def test_registry_is_versioned_and_complete() -> None:
    assert REG.version >= 1
    assert REG.observation_date == "2026-08-30"
    # Every audited dimension and view is present.
    assert set(REG.dimensions) == {"Sector", "Industry", "Country", "Capitalization"}
    assert set(REG.views) >= {"overview", "valuation", "performance", "custom", "spectrum"}


def test_registry_codes_match_live_provider_evidence() -> None:
    assert REG.dimensions["Sector"] == "sector"
    assert REG.dimensions["Industry"] == "industry"
    assert REG.dimensions["Country"] == "country"
    assert REG.dimensions["Capitalization"] == "capitalization"
    assert REG.views["overview"] == "110"
    assert REG.views["valuation"] == "120"
    assert REG.views["performance"] == "140"
    assert REG.views["custom"] == "152"
    assert REG.views["spectrum"] == "310"
    # Column codes harvested one-by-one from the live custom view (v=152).
    assert REG.columns["Market Cap"] == "2"
    assert REG.columns["Stocks"] == "26"
    assert REG.orders["Name"] == "name"
    assert REG.orders["Market Capitalization"] == "marketcap"
    # Sector sub-industry codes reuse the screener registry's Sector options.
    sub = REG.sub_industries["Technology"]
    assert sub == "technology"
    assert set(REG.sub_industries) == {
        "Basic Materials",
        "Communication Services",
        "Consumer Cyclical",
        "Consumer Defensive",
        "Energy",
        "Financial",
        "Healthcare",
        "Industrials",
        "Real Estate",
        "Technology",
        "Utilities",
    }


# --- typed value objects ------------------------------------------------------


def test_dimension_is_frozen_and_hashable() -> None:
    dim = GroupDimension("Sector")
    with pytest.raises((AttributeError, TypeError)):
        dim.name = "x"  # type: ignore[misc]
    assert dim == GroupDimension("Sector")
    assert hash(dim) == hash(GroupDimension("Sector"))


def test_unknown_dimension_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown dimension"):
        GroupDimension("Galaxy")


def test_unknown_view_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown view"):
        GroupView("nope")


def test_unknown_order_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown order"):
        GroupOrder("Nope")


def test_unknown_column_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown column"):
        GroupColumn("Nope")


def test_sub_industry_requires_industry_dimension() -> None:
    with pytest.raises(FinvizQueryError, match="sub-industry"):
        GroupDimension("Sector", sub_industry="Technology")


def test_sub_industry_must_be_a_sector() -> None:
    with pytest.raises(FinvizQueryError, match="sub-industry"):
        GroupDimension("Industry", sub_industry="Not A Sector")


def test_industry_with_sub_industry_accepted() -> None:
    dim = GroupDimension("Industry", sub_industry="Technology")
    assert dim.spec.code == "industry"
    assert dim.sub_industry == "Technology"


# --- query validation -----------------------------------------------------------


def test_query_defaults_to_sector_overview_name_order() -> None:
    q = GroupQuery()
    assert q.dimension == GroupDimension("Sector")
    assert q.view == GroupView("overview")
    assert q.order == GroupOrder("Name")


def test_query_is_frozen_and_hashable() -> None:
    q1 = GroupQuery()
    q2 = GroupQuery()
    assert q1 == q2 and hash(q1) == hash(q2)
    with pytest.raises((AttributeError, TypeError)):
        q1.view = "valuation"  # type: ignore[misc]


def test_query_rejects_duplicate_columns() -> None:
    with pytest.raises(FinvizQueryError, match="duplicate column"):
        GroupQuery(columns=[GroupColumn("Market Cap"), GroupColumn("Market Cap")])


def test_query_rejects_columns_for_fixed_views() -> None:
    with pytest.raises(FinvizQueryError, match="only valid"):
        GroupQuery(view="overview", columns=[GroupColumn("Market Cap")])


def test_custom_view_requires_columns() -> None:
    with pytest.raises(FinvizQueryError, match="requires"):
        GroupQuery(view="custom")


def test_spectrum_never_carries_columns() -> None:
    q = GroupQuery(view="spectrum")
    assert q.view == GroupView("spectrum")


# --- canonical serialization & provider encoding -------------------------------


def test_to_json_roundtrip_and_order_independence() -> None:
    a = GroupQuery(
        order=GroupOrder("Market Capitalization", descending=True),
        columns=[GroupColumn("Market Cap"), GroupColumn("P/E")],
        view="custom",
    )
    b = GroupQuery(
        view="custom",
        columns=[GroupColumn("Market Cap"), GroupColumn("P/E")],
        order=GroupOrder("Market Capitalization", descending=True),
    )
    assert a.to_json() == b.to_json()
    assert a.hash() == b.hash()
    assert json.loads(a.to_json())["order"] == {"name": "Market Capitalization", "descending": True}


def test_hash_differs_for_meaningful_changes() -> None:
    base = GroupQuery()
    assert base.hash() != GroupQuery(dimension="Country").hash()
    assert base.hash() != GroupQuery(view="valuation").hash()
    assert (
        base.hash()
        != GroupQuery(dimension=GroupDimension("Industry", sub_industry="Energy")).hash()
    )


def test_provider_params_encode_registry_codes() -> None:
    params = GroupQuery().provider_params()
    assert params == {"v": "110", "g": "sector", "o": "name", "st": "d1"}


def test_provider_params_custom_view_and_columns() -> None:
    q = GroupQuery(
        view="custom",
        columns=[GroupColumn("Market Cap")],
        order=GroupOrder("Market Capitalization", descending=True),
    )
    params = q.provider_params()
    assert params["v"] == "152"
    # The provider always renders No. (0) and Name (1) first, live-verified.
    assert params["c"] == "0,1,2"
    assert params["o"] == "-marketcap"


def test_provider_params_sub_industry() -> None:
    q = GroupQuery(dimension=GroupDimension("Industry", sub_industry="Technology"))
    params = q.provider_params()
    assert params["g"] == "industry"
    assert params["sg"] == "technology"


def test_provider_params_spectrum() -> None:
    params = GroupQuery(
        view="spectrum", order=GroupOrder("Market Capitalization")
    ).provider_params()
    assert params == {"v": "310", "g": "sector", "o": "marketcap", "st": "d1"}


def test_error_messages_do_not_echo_arbitrary_values() -> None:
    payload = "cookie=secret123; " + "x" * 300
    with pytest.raises(FinvizQueryError) as excinfo:
        GroupDimension(payload)
    assert "secret123" not in str(excinfo.value)
