"""Pure parser tests for the public statement JSON (Card 0.1-F).

RED-first: every test below fails until ``finvizp._parsers.statements`` exists.
Fixtures are minimal scrubbed structures shaped after the verified 2026-08
``/api/statement`` JSON (root currency, Period/Period End Date/Period Length
aligned arrays, per-metric value arrays). No live JSON is committed; every
number is synthetic. The parser is pure JSON -> source-near records; no
pyarrow, network, client, or cache imports.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from finvizp._parsers import statements as stmt_parser
from finvizp.errors import FinvizParseError

FIXTURES = Path(__file__).parent / "fixtures" / "statements"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def _parse(payload: dict, **kwargs: object) -> stmt_parser.StatementRecords:
    kwargs.setdefault("fetched_at", dt.datetime(2026, 8, 28, 14, 30, tzinfo=dt.UTC))
    return stmt_parser.parse_statement_json(payload, **kwargs)  # type: ignore[arg-type]


# --- recognized shapes ---------------------------------------------------------


def test_income_annual_parses_metric_period_records() -> None:
    records = _parse(_fixture("income-annual.json"), symbol="AAPL", statement="IA")
    assert records.currency == "USD"
    assert records.statement == "IA"
    assert records.periodicity == "annual"
    # 6 metrics x 4 periods = 24 long rows, metric-major in source order.
    assert len(records.rows) == 24
    first = records.rows[0]
    assert first["symbol"] == "AAPL"
    assert first["metric"] == "Total Revenue"
    assert first["period_label"] == "TTM"
    assert first["value"] == "401,500.00"
    assert first["currency"] == "USD"


def test_period_metadata_fields_are_aligned_per_period() -> None:
    records = _parse(_fixture("income-annual.json"), symbol="AAPL", statement="IA")
    ttm = [r for r in records.rows if r["period_label"] == "TTM"]
    assert len(ttm) == 6
    for row in ttm:
        assert row["period_end_date"] is None  # blank TTM end date
        assert row["period_length_days"] is None  # no parsed neighbor ends
    fy25 = [r for r in records.rows if r["period_label"] == "2025FY"]
    for row in fy25:
        assert row["period_end_date"] == dt.date(2025, 9, 30)
    # 12 Months between parsed consecutive ends: 9/30/2025 -> 9/30/2024.
    days = {
        r["period_label"]: r["period_length_days"]
        for r in records.rows
        if r["metric"] == "Net Income"
    }
    assert days["2025FY"] == 365
    assert days["2024FY"] == 366  # 2024 leap year


def test_quarterly_periodicity_and_lengths() -> None:
    records = _parse(_fixture("balance-quarterly.json"), symbol="AAPL", statement="BQ")
    assert records.periodicity == "quarterly"
    row = records.rows[0]
    assert row["period_label"] == "2026Q2"
    assert row["period_end_date"] == dt.date(2026, 3, 31)
    days = {
        r["period_label"]: r["period_length_days"]
        for r in records.rows
        if r["metric"] == "Total Assets"
    }
    assert days["2026Q2"] == 90  # 12/31/2025 -> 3/31/2026
    assert days["2026Q1"] == 92  # 9/30/2025 -> 12/31/2025
    assert days["2025Q4"] is None  # no earlier parsed end


def test_cashflow_annual_fixture_is_exercised() -> None:
    """The manifest's statements.ca/cq fixture parses like every statement code.

    Guards the hygiene contract: a manifest-referenced fixture can never sit
    unexercised (statements.ca and statements.cq both point at this file).
    """
    records = _parse(_fixture("cashflow-annual.json"), symbol="AAPL", statement="CA")
    assert records.currency == "USD"
    metrics = {r["metric"] for r in records.rows}
    assert metrics == {
        "Net Operating Cash Flow",
        "Capital Expenditures",
        "Dividends Paid",
        "Free Cash Flow",
    }
    labels = [r["period_label"] for r in records.rows if r["metric"] == "Free Cash Flow"]
    assert labels == ["TTM", "2025FY", "2024FY"]
    ttm = [r for r in records.rows if r["period_label"] == "TTM"]
    for row in ttm:
        assert row["period_end_date"] is None
        assert row["period_length_days"] is None
    fy = {
        r["period_label"]: r["period_length_days"]
        for r in records.rows
        if r["metric"] == "Net Operating Cash Flow"
    }
    assert fy["2025FY"] == 365  # 9/30/2025 -> 9/30/2024
    # Verbatim value displays survive (the builder owns conversion).
    values = {
        r["period_label"]: r["value"] for r in records.rows if r["metric"] == "Capital Expenditures"
    }
    assert values["2024FY"] == "-9,700.00"


def test_values_pass_through_verbatim() -> None:
    """Commas, signs, blanks, and decimals survive exactly (builder converts)."""
    records = _parse(_fixture("income-annual.json"), symbol="AAPL", statement="IA")
    by_metric: dict[str, list[dict]] = {}
    for row in records.rows:
        by_metric.setdefault(row["metric"], []).append(row)
    r_and_d = {r["period_label"]: r["value"] for r in by_metric["Research and Development"]}
    assert r_and_d["TTM"] == ""  # blank stays blank; builder nulls it
    assert r_and_d["2025FY"] == "-31,370.00"  # signed + commas verbatim
    assert r_and_d["2024FY"] == "-29,915.00"


def test_metric_order_is_source_order_and_periods_stable() -> None:
    records = _parse(_fixture("income-annual.json"), symbol="AAPL", statement="IA")
    metrics = []
    labels = []
    for row in records.rows:
        if not metrics or metrics[-1] != row["metric"]:
            metrics.append(row["metric"])
        if row["metric"] == "Total Revenue" and not labels:
            labels = [r["period_label"] for r in records.rows if r["metric"] == "Total Revenue"]
    assert metrics[0] == "Total Revenue"
    assert labels == ["TTM", "2025FY", "2024FY", "2023FY"]


def test_fingerprint_is_deterministic_and_value_sensitive() -> None:
    payload = _fixture("income-annual.json")
    one = _parse(payload, symbol="AAPL", statement="IA")
    two = _parse(_fixture("income-annual.json"), symbol="AAPL", statement="IA")
    assert one.fingerprint == two.fingerprint
    changed = json.loads(json.dumps(payload))
    changed["data"]["Total Revenue"][0] = "401,500.01"
    different = _parse(changed, symbol="AAPL", statement="IA")
    assert different.fingerprint != one.fingerprint


# --- drift / invalid payloads ---------------------------------------------------


def test_error_payload_is_recognized_empty() -> None:
    records = _parse({"error": "no data"}, symbol="NOPE", statement="IA")
    assert records.rows == ()
    assert records.empty_recognized is True
    assert records.currency is None


def test_missing_period_arrays_are_drift() -> None:
    payload = {"currency": "USD", "data": {"Total Revenue": ["1.00", "2.00"]}}
    with pytest.raises(FinvizParseError, match="Period"):
        _parse(payload, symbol="AAPL", statement="IA")


def test_misaligned_metric_arrays_are_drift() -> None:
    payload = {
        "currency": "USD",
        "data": {
            "Period": ["2025FY", "2024FY"],
            "Period End Date": ["", ""],
            "Total Revenue": ["1.00"],
        },
    }
    with pytest.raises(FinvizParseError, match="Total Revenue"):
        _parse(payload, symbol="AAPL", statement="IA")


def test_non_string_values_are_drift() -> None:
    """Numeric/bool metric values would silently reformat; only strings parse."""
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "Total Revenue": [1234.5]},
    }
    with pytest.raises(FinvizParseError, match="Total Revenue"):
        _parse(payload, symbol="AAPL", statement="IA")


def test_unparseable_period_end_is_drift() -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": ["not a date"]},
    }
    with pytest.raises(FinvizParseError, match="Period End Date"):
        _parse(payload, symbol="AAPL", statement="IA")


def test_missing_currency_is_drift() -> None:
    payload = {"data": {"Period": ["2025FY"], "Period End Date": [""]}}
    with pytest.raises(FinvizParseError, match="currency"):
        _parse(payload, symbol="AAPL", statement="IA")


def _flag_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json object_pairs_hook that rejects repeated keys (JSON allows them; the
    last one would silently win — that is drift, not data)."""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise FinvizParseError(
                f"duplicate JSON key {key!r}", context={"endpoint": "statements"}
            )
        seen[key] = value
    return seen


def test_duplicate_metric_keys_are_drift() -> None:
    """A repeated metric key in the raw JSON text would be silent loss."""
    raw = (
        '{"currency": "USD", "data": {"Period": ["2025FY"], "Period End Date": [""],'
        ' "Total Revenue": ["1.00"], "Total Revenue": ["2.00"]}}'
    )
    with pytest.raises(FinvizParseError):
        _parse(json.loads(raw, object_pairs_hook=_flag_duplicates), symbol="AAPL", statement="IA")


def test_empty_data_object_is_drift_not_silent() -> None:
    payload = {"currency": "USD", "data": {}}
    with pytest.raises(FinvizParseError, match="no statement data"):
        _parse(payload, symbol="AAPL", statement="IA")
