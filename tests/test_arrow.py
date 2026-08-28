"""Normalization tests for the registry-driven Arrow builder."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

from finvizp import arrow as fa
from finvizp.errors import FetchWarning, FinvizDataError

NOW = dt.datetime(2026, 8, 27, 14, 30, tzinfo=dt.UTC)


def _build(dataset: str, rows: list[dict[str, object]], **kwargs: object) -> pa.Table:
    return fa.build_table(dataset, rows, fetched_at=NOW, **kwargs)  # type: ignore[arg-type]


def _rows(table: pa.Table) -> list[dict[str, object]]:
    return table.to_pylist()


def test_common_fields_filled_and_deterministic() -> None:
    table = _build("symbol_universe", [{"symbol": "BRK-B"}])
    assert table.schema.names == list(fa.dataset_field_names("symbol_universe"))
    row = _rows(table)[0]
    assert row["symbol"] == "BRK-B"
    assert row["fetched_at"] == NOW


def test_empty_rows_produce_registered_empty_table() -> None:
    table = _build("symbol_search", [])
    assert table.num_rows == 0
    assert table.schema.names == list(fa.dataset_field_names("symbol_search"))
    for f in table.schema:
        assert f.nullable or f.name in {"symbol", "fetched_at"}


def test_null_sentinels_become_null() -> None:
    for sentinel in ("", "-", "--", "—", "n/a", "N/A", "NA", "None"):
        table = _build("symbol_search", [{"symbol": "AAPL", "company": sentinel}])
        assert _rows(table)[0]["company"] is None, sentinel


def test_comma_number_normalized() -> None:
    table = _build(
        "symbol_search", [{"symbol": "AAPL", "exchange": "NASDAQ", "market_cap": "1,234,567"}]
    )
    assert table.schema.field("market_cap").type == pa.float64()
    row = _rows(table)[0]
    assert row["market_cap"] == 1234567.0


def test_compact_suffixes_to_base_units() -> None:
    cases = {"1.5T": 1.5e12, "890.5B": 890.5e9, "12.3M": 12.3e6, "456K": 456e3, "789": 789.0}
    for text, expected in cases.items():
        table = _build("symbol_search", [{"symbol": "AAPL", "market_cap": text}])
        assert _rows(table)[0]["market_cap"] == pytest.approx(expected), text


def test_percent_to_fraction() -> None:
    for text, expected in {"3.25%": 0.0325, "-1.5%": -0.015, "3.25": 0.0325}.items():
        table = _build("symbol_search", [{"symbol": "AAPL", "div_yield": text}])
        assert _rows(table)[0]["div_yield"] == pytest.approx(expected), text


def test_counts_to_int64() -> None:
    table = _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT", "rank": "3"}])
    assert table.schema.field("rank").type == pa.int64()
    assert _rows(table)[0]["rank"] == 3


def test_floats_and_negative_compact() -> None:
    table = _build(
        "symbol_search",
        [{"symbol": "AAPL", "market_cap": "-2.4B", "div_yield": "0.5%"}],
    )
    row = _rows(table)[0]
    assert row["market_cap"] == pytest.approx(-2.4e9)
    assert row["div_yield"] == pytest.approx(0.005)


def test_date32_and_utc_timestamp() -> None:
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "price": "232.04", "ex_dividend_date": "2026-08-15"}],
    )
    assert table.schema.field("ex_dividend_date").type == pa.date32()
    assert _rows(table)[0]["ex_dividend_date"] == dt.date(2026, 8, 15)
    ts = _build(
        "quote_news",
        [
            {
                "symbol": "AAPL",
                "title": "x",
                "url": "https://example.com/a",
                "published_at": "10:00:00",
            }
        ],
    )
    published = _rows(ts)[0]["published_at"]
    assert published.tzinfo is not None and str(published.tzinfo) == "UTC"


def test_string_retention_and_raw_companions() -> None:
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "price": "232.04"}],
    )
    row = _rows(table)[0]
    assert row["price"] == pytest.approx(232.04)
    assert row["price_raw"] == "232.04"
    # text columns keep their exact display without any raw companion.
    plain = _build("symbol_search", [{"symbol": "AAPL", "company": "Apple Inc."}])
    assert _rows(plain)[0]["company"] == "Apple Inc."


def test_typed_value_null_retains_raw_and_warns() -> None:
    records: list[FetchWarning] = []

    def _warn(w: FetchWarning) -> None:
        records.append(w)

    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "price": "N/A"}],
        on_warning=_warn,
    )
    row = _rows(table)[0]
    assert row["price"] is None
    assert row["price_raw"] == "N/A"
    codes = [w.code for w in records]
    assert any(code == "null_sentinel" for code in codes)
    assert all("cookie" not in w.message.lower() for w in records)


def test_unknown_field_goes_to_extra_fields_with_drift_warning() -> None:
    records: list[FetchWarning] = []
    table = _build(
        "symbol_search",
        [{"symbol": "AAPL", "company": "Apple", "brand_new_ratio": "3.5%"}],
        on_warning=records.append,
    )
    row = _rows(table)[0]
    assert row["extra_fields"] == [("brand_new_ratio", "3.5%")]
    assert any(w.code == "unknown_field" for w in records)


def test_extra_fields_typed_as_map() -> None:
    table = _build("symbol_search", [{"symbol": "AAPL"}])
    assert pa.types.is_map(table.schema.field("extra_fields").type)


def test_strict_schema_promotes_drift_to_errors() -> None:
    with pytest.raises(FinvizDataError):
        _build("symbol_search", [{"symbol": "AAPL", "brand_new_ratio": "3.5%"}], strict_schema=True)
    with pytest.raises(FinvizDataError):
        _build(
            "quote_snapshot",
            [{"symbol": "AAPL", "price": "garbage"}],
            strict_schema=True,
        )


def test_required_conversion_failure_raises_data_error() -> None:
    with pytest.raises(FinvizDataError):
        _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT", "rank": "many"}])


def test_field_order_is_registry_deterministic() -> None:
    table_a = _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT"}])
    table_b = _build("quote_peers", [{"symbol": "AAPL", "peer": "GOOG"}])
    assert table_a.schema.equals(table_b.schema)
    assert table_a.schema.names == table_b.schema.names


def test_missing_required_symbol_rejected() -> None:
    with pytest.raises(FinvizDataError):
        _build("symbol_search", [{"company": "Apple"}])
