"""Parser tests for the sitemap symbol manifest and JSON suggestions (Card 0.1-E).

RED-first: every test below fails until ``finvizp._parsers.symbols`` exists.
Fixtures are minimal scrubbed structures; no live XML/JSON is copied.
"""

from __future__ import annotations

import json
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
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
    ],
)
def test_recognized_empty_manifest_is_empty_not_drift(xml: str) -> None:
    rows, warnings = symbols_parser.parse_sitemap(xml)
    assert rows == []
    assert warnings == []


def test_non_namespaced_urlset_with_payload_is_parse_drift() -> None:
    # Structure drift carrying data: without the sitemap namespace the manifest
    # is not a recognized shape, and must never silently degrade to EMPTY.
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_sitemap(
            "<urlset><url><loc>https://finviz.com/stock?t=AAPL</loc></url></urlset>"
        )


@pytest.mark.parametrize(
    "xml",
    [
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><loc>https://finviz.com/stock?t=AAPL</loc></urlset>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><unexpected/></urlset>',
    ],
)
def test_namespaced_sitemap_structure_drift_is_not_data_or_empty(xml: str) -> None:
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_sitemap(xml)


def test_malformed_and_out_of_range_ports_are_skipped_not_raised() -> None:
    # Broken URL components must be unexpected-URL warnings, never raw ValueError.
    for loc in (
        "https://finviz.com:invalid/stock?t=AAPL",
        "https://finviz.com:99999/stock?t=AAPL",
        "https://finviz.com:/stock?t=AAPL",
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


def test_xml_comments_and_processing_instructions_are_ignored() -> None:
    # Comments and PIs are never sitemap content: between entries and inside
    # a url they must be ignored, not raise structure drift.
    rows, warnings = symbols_parser.parse_sitemap(
        '<?xml version="1.0"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<!-- manifest comment -->"
        "<?xml-stylesheet type='text/xsl' href='sitemap.xsl'?>"
        "<url><!-- selected --><loc>https://finviz.com/stock?t=AAPL</loc></url>"
        "</urlset>"
    )
    assert rows == ["AAPL"]
    assert warnings == []


def test_standard_optional_url_children_are_accepted() -> None:
    # The sitemap protocol allows optional <lastmod>/<changefreq>/<priority>
    # inside <url>; they are never data here but must not be structure drift.
    rows, warnings = symbols_parser.parse_sitemap(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://finviz.com/stock?t=AAPL</loc>"
        "<lastmod>2026-08-28</lastmod>"
        "<changefreq>daily</changefreq>"
        "<priority>0.5</priority></url></urlset>"
    )
    assert rows == ["AAPL"]
    assert warnings == []


def test_unknown_nested_element_inside_url_is_structure_drift() -> None:
    # Unknown element children must never be accepted as ordinary data; only
    # the standard optional sitemap children are tolerated alongside loc.
    with pytest.raises(FinvizParseError):
        symbols_parser.parse_sitemap(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://finviz.com/stock?t=AAPL</loc>"
            "<lastmod>2026-08-28</lastmod><widget>x</widget></url></urlset>"
        )


def test_unknown_element_descendants_of_accepted_leaf_tags_are_drift() -> None:
    # loc/lastmod/changefreq/priority carry only text: unknown element
    # descendants at any depth below them are structure drift, while
    # comments/PIs inside them remain ignored content.
    for xml in (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://finviz.com/stock?t=AAPL<widget/></loc></url></urlset>",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://finviz.com/stock?t=AAPL</loc>"
        "<lastmod>2026-08-28<widget/></lastmod></url></urlset>",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://finviz.com/stock?t=AAPL</loc>"
        "<priority>0.5<sub xmlns='urn:x'/></priority></url></urlset>",
    ):
        with pytest.raises(FinvizParseError):
            symbols_parser.parse_sitemap(xml)


def test_parse_suggestions_preserves_provider_ranking() -> None:
    rows = symbols_parser.parse_suggestions(_suggestions())
    assert [(r["symbol"], r["company"], r["exchange"]) for r in rows] == [
        ("AAPL", "Apple Inc.", "NASDAQ"),
        ("AAP", "Advance Auto Parts", "NYSE"),
    ]


def test_parse_suggestions_preserves_additive_fields() -> None:
    # Provider drift must survive: non-schema fields are kept for the Arrow
    # builder, which lands them in ``extra_fields`` with ``unknown_field``.
    rows = symbols_parser.parse_suggestions(
        '[{"ticker": "AAPL", "company": "Apple Inc.", "exchange": "NASDAQ", "provider_added": "v"}]'
    )
    assert rows == [
        {
            "symbol": "AAPL",
            "company": "Apple Inc.",
            "exchange": "NASDAQ",
            "provider_added": "v",
        }
    ]


def test_parse_suggestions_preserves_ticker_key_on_provider_symbol_collision() -> None:
    rows = symbols_parser.parse_suggestions(
        '[{"ticker": "AAPL", "company": "Apple Inc.", "exchange": "NASDAQ", "symbol": "EVIL"}]'
    )
    assert rows == [
        {
            "symbol": "AAPL",
            "company": "Apple Inc.",
            "exchange": "NASDAQ",
            "provider_symbol": "EVIL",
        }
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


# --- source-symbol validation ---------------------------------------------------


@pytest.mark.parametrize(
    "loc",
    [
        "https://finviz.com/stock?t=%C5%BF",
        "https://finviz.com/stock?t=AAPL+",
        "https://finviz.com/stock?t=%41APL",  # decodes to AAPL; must not normalize
        "https://finviz.com/stock?t=BRK%2DB",  # encoded dash; must not decode
    ],
)
def test_percent_encoded_and_padded_source_values_are_unexpected_urls(loc: str) -> None:
    # Validation happens on the RAW query string BEFORE percent-decoding:
    # %C5%BF must not decode/collapse to "S", %41APL must not decode to
    # "AAPL", %2D must not decode to a dash, and "AAPL+" must not trim to
    # "AAPL" — all shapes are unexpected URLs, skipped with a warning.
    rows, warnings = symbols_parser.parse_sitemap(
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{loc}</loc></url></urlset>'
    )
    assert rows == []
    assert [w.code for w in warnings] == ["unexpected_url"]


@pytest.mark.parametrize(
    "loc", ["https://finviz.com/stock?t= brk-b ", "https://finviz.com/stock?t=+AAPL+"]
)
def test_whitespace_padded_source_values_are_unexpected_urls(loc: str) -> None:
    # Whitespace padding is not a valid unpadded source value either.
    rows, warnings = symbols_parser.parse_sitemap(
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{loc}</loc></url></urlset>'
    )
    assert rows == []
    assert [w.code for w in warnings] == ["unexpected_url"]


@pytest.mark.parametrize(
    "loc", ["https://finviz.com/stock?t=BRK-B", "https://finviz.com/stock?t=na"]
)
def test_valid_dash_forms_and_lowercase_na_are_preserved(loc: str) -> None:
    # The unpadded [A-Za-z0-9-]+ gate keeps every valid form; casing still
    # normalizes afterwards (na -> NA), dashes survive verbatim.
    rows, warnings = symbols_parser.parse_sitemap(
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{loc}</loc></url></urlset>'
    )
    assert len(rows) == 1
    assert warnings == []


def test_suggestion_malformed_ticker_raises_parse_drift() -> None:
    # A malformed provider ticker ("BAD!", "%") must be typed drift, never a
    # normalized row in a COMPLETE result.
    for ticker in ("BAD!", "%"):
        record = json.dumps({"ticker": ticker, "company": "c", "exchange": "e"})
        with pytest.raises(FinvizParseError):
            symbols_parser.parse_suggestions(record)


def test_suggestion_missing_or_malformed_company_exchange_is_parse_drift() -> None:
    # Missing keys and malformed types are provider drift: they must never be
    # silently converted to null in a returned row.
    for record in (
        {"ticker": "AAPL", "exchange": "NASDAQ"},  # company missing
        {"ticker": "AAPL", "company": "Apple", "exchange": None},  # exchange null
        {"ticker": "AAPL", "company": 42, "exchange": "NASDAQ"},  # company non-string
        {"ticker": "AAPL", "company": {"n": "Apple"}, "exchange": "NASDAQ"},  # dict company
    ):
        with pytest.raises(FinvizParseError):
            symbols_parser.parse_suggestions([record])
