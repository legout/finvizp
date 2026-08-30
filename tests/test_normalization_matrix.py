"""Cross-family normalization matrix (Card 0.5-B, step 3).

One compact truth table: every display class (compact values, fractions,
counts, dates, time-only anchors, exact datetimes, DST fold ambiguity,
sentinels, extra fields) is asserted against multiple registered datasets
spanning different parser families, so a family parser cannot quietly drift
away from the shared normalization contract. Meridiem news displays are
normalized by the news parser (see ``test_news.py``) before the builder sees
them; the builder contract covers the shapes parsers emit.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo

import pyarrow as pa
import pytest

from finvizp import arrow as fa
from finvizp.errors import FetchWarning

NOW = dt.datetime(2026, 8, 27, 14, 30, tzinfo=dt.UTC)
DATE = dt.date(2026, 8, 27)
EASTERN = zoneinfo.ZoneInfo("America/New_York")


def _table(dataset: str, rows: list[dict[str, object]]) -> pa.Table:
    return fa.build_table(dataset, rows, fetched_at=NOW, response_date=DATE)


# (dataset, field, display, expected) — same display class, different families.
_COMPACT_CASES = [
    ("symbol_search", "market_cap", "2.5B", 2.5e9),
    ("quote_snapshot", "market_cap", "2.5B", 2.5e9),
    ("quote_insider", "value", "1.2M", 1.2e6),
    ("quote_etf_holders", "aum", "890.5B", 890.5e9),
]
_FRACTION_CASES = [
    ("symbol_search", "div_yield", "3.25%", 0.0325),
    ("quote_snapshot", "change_percent", "-1.5%", -0.015),
    ("futures_tiles", "change_percent", "0.75%", 0.0075),
]
_COUNT_CASES = [
    ("quote_peers", "rank", "3", 3),
    ("quote_snapshot", "volume", "12,500", 12500),
]
_DATE_CASES = [
    ("quote_snapshot", "ex_dividend_date", "2026-08-15", dt.date(2026, 8, 15)),
    ("economic_calendar", "release_date", "2026-09-01", dt.date(2026, 9, 1)),
    ("earnings_screen", "earnings_date", "2026-09-08", dt.date(2026, 9, 8)),
]
_SENTINEL_SPOT_CASES = [
    ("symbol_search", "company"),
    ("quote_snapshot", "sector"),
    ("quote_description", "description"),
    ("statements", "currency"),
]


def _row(dataset: str, **extra: object) -> dict[str, object]:
    """A minimal contract-valid row for the datasets this module exercises."""
    rows: dict[str, dict[str, object]] = {
        "symbol_search": {},
        "quote_snapshot": {},
        "quote_insider": {},
        "quote_etf_holders": {"etf": "SPY"},
        "quote_peers": {"peer": "MSFT"},
        "quote_news": {"title": "t", "url": "https://example.com/a"},
        "quote_description": {},
        "futures_tiles": {},
        "economic_calendar": {"event": "e"},
        "earnings_screen": {"rank": "3"},
        "statements": {
            "statement_kind": "k",
            "periodicity": "q",
            "period_label": "Q1",
            "metric": "m",
        },
    }
    return {"symbol": "AAPL", **rows[dataset], **extra}


@pytest.mark.parametrize(("dataset", "field", "display", "expected"), _COMPACT_CASES)
def test_compact_suffix_matches_across_families(
    dataset: str, field: str, display: str, expected: float
) -> None:
    table = _table(dataset, [_row(dataset, **{field: display})])
    assert table.column(field)[0].as_py() == pytest.approx(expected)
    assert table.column(f"{field}_raw")[0].as_py() == display


@pytest.mark.parametrize(("dataset", "field", "display", "expected"), _FRACTION_CASES)
def test_percent_fraction_matches_across_families(
    dataset: str, field: str, display: str, expected: float
) -> None:
    table = _table(dataset, [_row(dataset, **{field: display})])
    assert table.column(field)[0].as_py() == pytest.approx(expected)


@pytest.mark.parametrize(("dataset", "field", "display", "expected"), _COUNT_CASES)
def test_counts_match_across_families(
    dataset: str, field: str, display: str, expected: int
) -> None:
    table = _table(dataset, [_row(dataset, **{field: display})])
    assert table.column(field)[0].as_py() == expected
    assert pa.types.is_int64(table.schema.field(field).type)


@pytest.mark.parametrize(("dataset", "field", "display", "expected"), _DATE_CASES)
def test_iso_dates_match_across_families(
    dataset: str, field: str, display: str, expected: dt.date
) -> None:
    table = _table(dataset, [_row(dataset, **{field: display})])
    assert table.column(field)[0].as_py() == expected
    assert pa.types.is_date32(table.schema.field(field).type)


@pytest.mark.parametrize(("dataset", "field"), (("quote_news", "published_at"),))
def test_time_only_displays_share_one_eastern_anchor_rule(dataset: str, field: str) -> None:
    """``HH:MM`` (news parser emits these for Today/time-only displays)."""
    table = _table(dataset, [_row(dataset, **{field: "13:30"})])
    value = table.column(field)[0].as_py()
    assert value == dt.datetime(2026, 8, 27, 17, 30, tzinfo=dt.UTC)
    assert table.column(f"{field}_status")[0].as_py() == "anchored"
    assert table.column(f"{field}_raw")[0].as_py() == "13:30"


@pytest.mark.parametrize(
    ("dataset", "field"),
    (("quote_news", "published_at"), ("economic_calendar", "release_timestamp")),
)
def test_time_only_anchor_shares_one_eastern_rule(dataset: str, field: str) -> None:
    """24-hour time-only displays anchor to the response date in New York."""
    for display, instant in (("09:30", 13), ("13:30", 17)):
        table = _table(dataset, [_row(dataset, **{field: display})])
        value = table.column(field)[0].as_py()
        assert value == dt.datetime(2026, 8, 27, instant, 30, tzinfo=dt.UTC), (dataset, display)
        assert table.column(f"{field}_status")[0].as_py() == "anchored", (dataset, display)


@pytest.mark.parametrize(
    ("dataset", "field"),
    (("quote_news", "published_at"), ("economic_calendar", "release_timestamp")),
)
def test_exact_datetimes_share_one_eastern_rule(dataset: str, field: str) -> None:
    """Full datetimes parse as exact US Eastern wall-clock -> UTC."""
    table = _table(dataset, [_row(dataset, **{field: "2026-08-27 09:30"})])
    value = table.column(field)[0].as_py()
    assert value == dt.datetime(2026, 8, 27, 13, 30, tzinfo=dt.UTC), dataset
    assert table.column(f"{field}_status")[0].as_py() == "exact", dataset


@pytest.mark.parametrize(
    ("dataset", "field"),
    (("quote_news", "published_at"), ("economic_calendar", "release_timestamp")),
)
def test_dst_fold_time_has_no_invented_instant(dataset: str, field: str) -> None:
    """A folded local time (2026-11-01 01:30 ET) keeps raw, null, 'ambiguous'."""
    table = _table(dataset, [_row(dataset, **{field: "2026-11-01 01:30"})])
    assert table.column(field)[0].as_py() is None, dataset
    assert table.column(f"{field}_raw")[0].as_py() == "2026-11-01 01:30"
    assert table.column(f"{field}_status")[0].as_py() == "ambiguous", dataset


@pytest.mark.parametrize(
    "sentinel", ["", "-", "--", "---", "\u2014", "\u2013", "n/a", "N/A", "NA", "None", "null"]
)
@pytest.mark.parametrize(("dataset", "field"), _SENTINEL_SPOT_CASES)
def test_null_sentinels_match_across_families(dataset: str, field: str, sentinel: str) -> None:
    """Every nullable text field in every family treats sentinels identically."""
    table = _table(dataset, [_row(dataset, **{field: sentinel})])
    assert table.column(field)[0].as_py() is None, (dataset, field, sentinel)


def test_relative_time_kept_verbatim_with_status() -> None:
    """Provider ages the builder cannot anchor pass through verbatim, typed null.

    The news family resolves ``46 min`` against ``fetched_at`` in the parser
    layer (``test_news.py``); at the builder contract level an unconverted
    relative display is never silently dropped or invented into an instant.
    """
    table = _table("quote_news", [_row("quote_news", published_at="46 min")])
    assert table.column("published_at")[0].as_py() is None
    assert table.column("published_at_raw")[0].as_py() == "46 min"
    assert table.column("published_at_status")[0].as_py() is None


def test_extra_fields_and_drift_warning_are_family_independent() -> None:
    records: list[FetchWarning] = []
    for dataset, row_extra in (
        ("symbol_search", {"brand_new_ratio": "3.5%"}),
        ("futures_tiles", {"mystery_tile": "7"}),
    ):
        table = fa.build_table(
            dataset,
            [_row(dataset, **row_extra)],
            fetched_at=NOW,
            response_date=DATE,
            on_warning=records.append,
        )
        extras = table.column("extra_fields")[0].as_py()
        assert dict(extras) == row_extra, dataset
    assert {w.code for w in records} == {"unknown_field"}


def test_comma_thousands_and_signs_normalized_everywhere() -> None:
    for dataset, field, raw, expected in (
        ("symbol_search", "market_cap", "1,234,567", 1234567.0),
        ("quote_snapshot", "price", "-2,315.04", -2315.04),
    ):
        table = _table(dataset, [_row(dataset, **{field: raw})])
        assert table.column(field)[0].as_py() == pytest.approx(expected), raw
        assert table.column(f"{field}_raw")[0].as_py() == raw
