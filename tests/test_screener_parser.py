"""Tests for the pure screener HTML parser (Card 0.2-B, TDD step 1).

Every test fails until ``finvizp._parsers.screener`` exists. The parser is
transport-free: plain HTML in, immutable page records out. Header-driven
columns, provider absolute ranks, page evidence, a positively recognized
no-results state, and structural drift — never positional guessing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finvizp._parsers.screener import ScreenerPage, parse_screener_page
from finvizp.errors import FinvizParseError

FIXTURES = Path(__file__).parent / "fixtures" / "screener"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


# --- fixed named view: overview -------------------------------------------------


def test_overview_page_parses_header_driven_columns() -> None:
    page = parse_screener_page(_page("overview-page-1.html"))
    assert isinstance(page, ScreenerPage)
    assert not page.is_empty
    assert page.columns == (
        "No.",
        "Ticker",
        "Company",
        "Sector",
        "Industry",
        "Country",
        "Market Cap",
        "P/E",
        "Price",
        "Change %",
        "Volume",
    )


def test_rows_carry_provider_absolute_rank_symbol_and_raw_displays() -> None:
    page = parse_screener_page(_page("overview-page-1.html"))
    assert len(page.rows) == 20
    first, last = page.rows[0], page.rows[-1]
    assert first.rank == 1
    assert first.symbol == "S01X"
    assert first.raw == (
        "Sample Co 1",
        "Technology",
        "Software",
        "USA",
        "1.20B",
        "12.34",
        "10.10",
        "1.23%",
        "123,456",
    )
    assert last.rank == 20
    assert last.symbol == "S20X"


def test_page_evidence_reports_start_rank_and_total() -> None:
    page = parse_screener_page(_page("overview-page-1.html"))
    assert page.page_start == 1
    assert page.total_rows == 600


def test_final_page_evidence_reports_late_start() -> None:
    page = parse_screener_page(_page("overview-final-page.html"))
    assert page.page_start == 581
    assert page.total_rows == 588
    assert [row.rank for row in page.rows] == list(range(581, 589))
    assert len(page.rows) < 20  # sub-page-size final evidence


def test_structural_fingerprint_is_stable_and_content_free() -> None:
    p1 = parse_screener_page(_page("overview-page-1.html"))
    again = parse_screener_page(_page("overview-page-1.html"))
    assert p1.fingerprint == again.fingerprint
    assert p1.fingerprint != parse_screener_page(_page("overview-final-page.html")).fingerprint


# --- custom view ----------------------------------------------------------------


def test_custom_view_headers_follow_requested_columns() -> None:
    page = parse_screener_page(_page("custom-columns.html"))
    assert page.columns == ("No.", "Ticker", "Price")
    assert len(page.rows) == 20
    assert page.rows[0].symbol == "S01X"
    assert page.rows[0].raw == ("10.10",)


# --- recognized no-results state --------------------------------------------------


def test_no_results_page_is_positively_recognized_empty() -> None:
    page = parse_screener_page(_page("no-results.html"))
    assert page.is_empty
    assert page.rows == ()
    assert page.columns == ()


# --- structural drift --------------------------------------------------------------


def test_malformed_row_arity_is_drift() -> None:
    with pytest.raises(FinvizParseError, match="column count"):
        parse_screener_page(_page("_drift-malformed-row.html"))


def test_missing_header_is_drift() -> None:
    with pytest.raises(FinvizParseError, match="header"):
        parse_screener_page(_page("_drift-header.html"))


def test_plain_garbage_is_drift() -> None:
    with pytest.raises(FinvizParseError):
        parse_screener_page("<html><body><p>nothing here</p></body></html>")


def test_missing_page_marker_on_populated_table_is_drift() -> None:
    html = _page("overview-page-1.html").replace("#1 / 600 Total", "elsewhere")
    with pytest.raises(FinvizParseError, match="page marker"):
        parse_screener_page(html)


def test_duplicate_rank_is_drift() -> None:
    html = _page("overview-page-1.html").replace(
        '<td height="10" align="right"><a href="stock?t=S02X&amp;ty=c&amp;p=d">2</a></td>',
        '<td height="10" align="right"><a href="stock?t=S01X&amp;ty=c&amp;p=d">1</a></td>',
    )
    with pytest.raises(FinvizParseError, match="rank"):
        parse_screener_page(html)
