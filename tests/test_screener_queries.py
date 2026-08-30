"""Tests for typed screener query models: immutability, validation, encoding."""

from __future__ import annotations

import json

import pytest

from finvizp._queries.screener import (
    CustomColumns,
    Filter,
    Order,
    ScreenerQuery,
    Signal,
    View,
    screener_registry,
)
from finvizp.errors import FinvizQueryError

REG = screener_registry()


def _filter(name: str, option: str) -> Filter | None:
    """Build a Filter; ``Any`` selections normalize to ``None`` (no-op)."""
    f = Filter(name=name, option=option)
    return None if f.is_noop else f


# --- immutable value objects -------------------------------------------------


def test_value_objects_are_frozen() -> None:
    f = _filter("Sector", "Technology")
    s = Signal(name="Top Gainers")
    o = Order(name="Market Cap.", descending=True)
    v = View(name="overview")
    for obj in (f, s, o, v):
        with pytest.raises((AttributeError, TypeError), match=r"cannot assign|frozen"):
            obj.name = "x"  # type: ignore[misc]


def test_value_objects_hashable() -> None:
    f1 = _filter("Sector", "Technology")
    f2 = _filter("Sector", "Technology")
    assert f1 == f2 and hash(f1) == hash(f2)
    assert len({f1, f2}) == 1


def test_screener_query_is_frozen_and_hashable() -> None:
    q1 = ScreenerQuery(view="overview", filters=[_filter("Sector", "Technology")])
    q2 = ScreenerQuery(view="overview", filters=[_filter("Sector", "Technology")])
    assert q1 == q2 and hash(q1) == hash(q2)
    with pytest.raises((AttributeError, TypeError)):
        q1.view = "valuation"  # type: ignore[misc]


# --- filter validation -------------------------------------------------------


def test_unknown_filter_name_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown filter"):
        Filter(name="Not A Filter", option="Any")


def test_unknown_filter_option_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown option"):
        Filter(name="Sector", option="Not An Option")


def test_any_option_normalizes_to_noop() -> None:
    f = _filter("Sector", "Any")
    assert f is None


def test_duplicate_filters_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="duplicate filter"):
        ScreenerQuery(filters=[_filter("Sector", "Technology"), _filter("Sector", "Energy")])


# --- order / view / signal validation ----------------------------------------


def test_unknown_order_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown order"):
        Order(name="Nope")


def test_unknown_view_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown view"):
        ScreenerQuery(view="nope")


def test_unknown_signal_rejected() -> None:
    with pytest.raises(FinvizQueryError, match="unknown signal"):
        Signal(name="Nope")


def test_page_must_be_positive() -> None:
    with pytest.raises(FinvizQueryError, match="page"):
        ScreenerQuery(page=0)
    with pytest.raises(FinvizQueryError, match="page"):
        ScreenerQuery(page=-3)


def test_max_rows_must_be_positive_or_none() -> None:
    with pytest.raises(FinvizQueryError, match="max_rows"):
        ScreenerQuery(max_rows=0)


def test_signal_with_fixed_view_is_incompatible() -> None:
    with pytest.raises(FinvizQueryError, match="incompatible"):
        ScreenerQuery(view="overview", signal="Top Gainers")


def test_signal_requires_custom_view() -> None:
    q = ScreenerQuery(
        view="custom",
        signal="Top Gainers",
        columns=CustomColumns(names=["Ticker", "Price"]),
    )
    assert q.signal is not None


# --- custom columns ----------------------------------------------------------


def test_custom_columns_must_be_registered() -> None:
    with pytest.raises(FinvizQueryError, match="unknown column"):
        CustomColumns(names=["Ticker", "Not A Column"])


def test_custom_columns_reject_duplicates() -> None:
    with pytest.raises(FinvizQueryError, match="duplicate column"):
        CustomColumns(names=["Ticker", "Ticker"])


def test_custom_columns_required_for_custom_view() -> None:
    with pytest.raises(FinvizQueryError, match="custom columns"):
        ScreenerQuery(view="custom")


def test_custom_columns_ignored_for_fixed_view() -> None:
    with pytest.raises(FinvizQueryError, match="only valid"):
        ScreenerQuery(view="overview", columns=CustomColumns(names=["Ticker", "Price"]))


# --- raw-code escape hatch ----------------------------------------------------


def test_raw_code_requires_reviewed_registry_entry() -> None:
    with pytest.raises(FinvizQueryError, match="not a reviewed"):
        ScreenerQuery.from_raw(raw_filter="fa_pe_under5")


def test_raw_code_grammar_enforced() -> None:
    with pytest.raises(FinvizQueryError):
        ScreenerQuery.from_raw(raw_filter="fa_pe;drop")
    with pytest.raises(FinvizQueryError):
        ScreenerQuery.from_raw(raw_filter="")


def test_raw_code_resolves_to_typed_filter() -> None:
    q = ScreenerQuery.from_raw(raw_filter="fa_pe_u5")
    assert q.filters is not None
    (f,) = q.filters
    assert f.name == "P/E"
    assert f.option == "Under 5"


def test_raw_signal_and_order_codes() -> None:
    q = ScreenerQuery.from_raw(
        raw_signal="ta_topgainers", raw_order="marketcap", columns=["Ticker", "Price"]
    )
    assert q.signal == Signal(name="Top Gainers")
    assert q.order == Order(name="Market Cap.", descending=False)
    with pytest.raises(FinvizQueryError):
        ScreenerQuery.from_raw(raw_signal="not_a_signal")


def test_error_messages_do_not_echo_arbitrary_values() -> None:
    payload = "cookie=secret123; " + "x" * 300
    with pytest.raises(FinvizQueryError) as excinfo:
        ScreenerQuery.from_raw(raw_signal=payload)
    assert "secret123" not in str(excinfo.value)


# --- canonical serialization & hash -------------------------------------------


def test_canonical_serialization_is_stable_and_order_independent() -> None:
    a = ScreenerQuery(
        view="custom",
        filters=[_filter("Sector", "Technology"), _filter("Exchange", "NYSE")],
        order=Order(name="Market Cap.", descending=True),
        columns=CustomColumns(names=["Ticker", "Market Cap.", "Price"]),
        page=3,
        max_rows=500,
    )
    b = ScreenerQuery(
        max_rows=500,
        page=3,
        order=Order(name="Market Cap.", descending=True),
        columns=CustomColumns(names=["Ticker", "Market Cap.", "Price"]),
        filters=[_filter("Exchange", "NYSE"), _filter("Sector", "Technology")],
        view="custom",
    )
    ca, cb = a.to_json(), b.to_json()
    assert ca == cb
    assert a.hash() == b.hash()
    parsed = json.loads(ca)
    assert parsed == json.loads(b.to_json())


def test_hash_differs_for_meaningful_changes() -> None:
    base = ScreenerQuery(view="overview")
    changed = ScreenerQuery(view="overview", order=Order(name="Price", descending=False))
    assert base.hash() != changed.hash()
    paged = ScreenerQuery(view="overview", page=2)
    assert base.hash() != paged.hash()


def test_to_json_roundtrip() -> None:
    q = ScreenerQuery(
        view="custom",
        filters=[_filter("Sector", "Technology")],
        order=Order(name="Price"),
        columns=CustomColumns(names=["Ticker", "Price"]),
    )
    revived = ScreenerQuery.from_json(q.to_json())
    assert revived == q


# --- provider-code encoding ----------------------------------------------------


def test_provider_params_encode_registry_codes() -> None:
    q = ScreenerQuery(
        view="custom",
        filters=[_filter("Sector", "Technology"), _filter("P/E", "Under 5")],
        order=Order(name="Market Cap.", descending=True),
        columns=CustomColumns(names=["No.", "Ticker", "Price"]),
        page=2,
    )
    params = q.provider_params()
    assert params["v"] == REG.views["custom"].code
    assert params["f"] == "sec_technology,fa_pe_u5"
    assert params["o"] == "-marketcap"
    assert params["c"] == "0,1,65"
    assert params["r"] == "21"


def test_provider_params_minimal_query() -> None:
    params = ScreenerQuery(view="overview").provider_params()
    assert params == {"v": REG.views["overview"].code}


def test_ticker_param_encoding() -> None:
    params = ScreenerQuery(
        view="custom", ticker="AAPL", columns=CustomColumns(names=["Ticker", "Price"])
    ).provider_params()
    assert params["t"] == "AAPL"


def test_ticker_grammar_validated() -> None:
    with pytest.raises(FinvizQueryError, match="ticker"):
        ScreenerQuery(ticker="not a ticker!")
