"""Parser tests for the sitemap symbol manifest and JSON suggestions (Card 0.1-E).

RED-first: every test below fails until ``finvizp._parsers.symbols`` exists.
Fixtures are minimal scrubbed structures; no live XML/JSON is copied.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finvizp._parsers import symbols as symbols_parser
from finvizp.errors import FinvizParseError

FIXTURES = Path(__file__).parent / "fixtures" / "symbols"


def _sitemap() -> str:
    return (FIXTURES / "stock-sitemap.xml").read_text("utf-8")


def _suggestions() -> str:
    return (FIXTURES / "suggestions.json").read_text("utf-8")


# --- sitemap manifest parsing -------------------------------------------------


def test_parse_yields_unique_canonical_symbols_in_manifest_order() -> None:
    rows, _ = symbols_parser.parse_sitemap(_sitemap())
    assert rows == ["AAPL", "BRK-B", "AAC-U", "REZI-WI", "NA"]


def test_ty_oc_duplicates_are_removed_not_optionability() -> None:
    rows, _ = symbols_parser.parse_sitemap(_sitemap())
    # BRK-B appears canonically first and again as ty=oc; exactly one row.
    assert rows.count("BRK-B") == 1
    assert rows.count("AAC-U") == 1


def test_dash_forms_are_preserved_verbatim() -> None:
    rows, _ = symbols_parser.parse_sitemap(_sitemap())
    assert "BRK-B" in rows
    assert "AAC-U" in rows
    assert "REZI-WI" in rows
    # No dot/slash class notation may be reintroduced from the manifest.
    assert not any("." in s or "/" in s for s in rows)


def test_unexpected_url_shapes_warn_and_are_skipped() -> None:
    rows, warnings = symbols_parser.parse_sitemap(_sitemap())
    # screener URL, bare /stock, empty t, punctuation t, foreign host,
    # near-match path: all skipped with warnings, never returned as symbols.
    assert "AAPL" in rows
    assert "EVIL" not in rows
    assert "BRK" not in rows
    assert len(rows) == 5
    assert len(warnings) == 6
    assert all(w.code == "unexpected_url" for w in warnings)


def test_non_canonical_hosts_and_paths_are_rejected() -> None:
    # Only the canonical https://finviz.com/stock?t=... shape carries a symbol.
    for loc in (
        "https://other.example/stock?t=EVIL",
        "http://finviz.com/stock?t=HTTP",
        "https://finviz.com/stocks?t=BRK",
        "https://finviz.com/other/stock?t=PATH",
        "https://finviz.com/stock?ty=oc",
    ):
        rows, warnings = symbols_parser.parse_sitemap(
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{loc}</loc></url></urlset>'
        )
        assert rows == []
        assert [w.code for w in warnings] == ["unexpected_url"]


def test_non_canonical_query_and_fragment_variants_are_rejected() -> None:
    # Canonical shape is exactly https://finviz.com/stock?t=SYMBOL with an
    # optional ty=oc; fragments, unknown/duplicate query fields, and other ty
    # variants are not canonical.
    for loc in (
        "https://finviz.com/stock?t=AAPL#frag",
        "https://finviz.com/stock?t=AAPL&amp;unexpected=1",
        "https://finviz.com/stock?t=AAPL&amp;ty=cb",
        "https://finviz.com/stock?t=AAPL&amp;t=AAPL",
        "https://finviz.com/stock?ty=oc",
        "https://finviz.com/stock",
    ):
        rows, warnings = symbols_parser.parse_sitemap(
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{loc}</loc></url></urlset>'
        )
        assert rows == []
        assert [w.code for w in warnings] == ["unexpected_url"], loc


@pytest.mark.parametrize(
    "xml",
    [
        "<urlset></urlset>",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
    ],
)
def test_recognized_empty_manifest_is_empty_not_drift(xml: str) -> None:
    rows, warnings = symbols_parser.parse_sitemap(xml)
    assert rows == []
    assert warnings == []


def test_malformed_and_out_of_range_ports_are_skipped_not_raised() -> None:
    # Broken URL components must be unexpected-URL warnings, never raw ValueError.
    for loc in (
        "https://finviz.com:invalid/stock?t=AAPL",
        "https://finviz.com:99999/stock?t=AAPL",
        "https://[::1/stock?t=AAPL",
    ):
        rows, warnings = symbols_parser.parse_sitemap(
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{loc}</loc></url></urlset>'
        )
        assert rows == []
        assert [w.code for w in warnings] == ["unexpected_url"], loc


def test_malformed_xml_is_parse_drift() -> None:
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_sitemap("<urlset><url></urlset>")


def test_lxml_rejects_entities_and_dtd() -> None:
    # Upstream boundary: entity expansion / DTD constructs are refused, not resolved.
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_sitemap(
            '<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY x "EVIL">]>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://finviz.com/stock?t=&x;</loc></url></urlset>"
        )


def test_non_urlset_root_is_parse_drift() -> None:
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_sitemap("<rss><channel/></rss>")


# --- suggestions JSON parsing ---------------------------------------------------


def test_parse_suggestions_preserves_provider_ranking() -> None:
    rows = symbols_parser.parse_suggestions(_suggestions())
    assert [(r["symbol"], r["company"], r["exchange"]) for r in rows] == [
        ("AAPL", "Apple Inc.", "NASDAQ"),
        ("AAP", "Advance Auto Parts", "NYSE"),
    ]


def test_parse_suggestions_empty_list_is_recognized_empty() -> None:
    assert symbols_parser.parse_suggestions("[]") == []


def test_parse_suggestions_missing_ticker_is_parse_drift() -> None:
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_suggestions('[{"company": "Apple", "exchange": "NASDAQ"}]')


def test_parse_suggestions_json_null_is_parse_drift_not_empty() -> None:
    # Only ``[]`` is the recognized empty shape; ``null`` is provider drift.
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_suggestions("null")


def test_parse_suggestions_non_list_is_parse_drift() -> None:
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_suggestions('{"ticker": "AAPL"}')


def test_parse_suggestions_malformed_json_is_parse_drift() -> None:
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_suggestions("[{")
