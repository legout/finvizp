"""Tests for the pure map parsers (Card 0.3-B, TDD step 1).

RED-first: every test fails until ``finvizp._parsers.maps`` exists. Transport-
free: plain text in, immutable records out. Two parsers mirror the two verified
first-party representations (representation-evidence.md):

- :func:`parse_map_page` reads the public ``/map.ashx`` HTML: the embedded
  ``initialPerf`` JSON payload (typed performance facts) and the page's own
  ``data-chunk-id="map_base_sec"`` preload link (the hierarchy asset URL the
  page itself requests — never constructed locally);
- :func:`parse_hierarchy_asset` reads the preloaded static data asset: a
  webpack module whose export is a sector -> industry -> symbol object literal
  with ``name``/``description``/``value`` leaves.

Malformed or incomplete embedded data is typed drift
(:class:`FinvizParseError`); a payload without any perf nodes or hierarchy
leaves is the positively recognized empty state, not drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finvizp._parsers.maps import (
    HierarchyNode,
    MapPage,
    parse_hierarchy_asset,
    parse_map_page,
)
from finvizp.errors import FinvizParseError

FIXTURES = Path(__file__).parent / "fixtures" / "maps"

PAGE = (FIXTURES / "sp500-embedded.html").read_text("utf-8")
ASSET = (FIXTURES / "sp500-hierarchy.js").read_text("utf-8")

CHUNK_URL = f"/assets/dist/{'mapbase.v1.0f1xTURE.js'}"


# --- map page: embedded initialPerf + hierarchy preload URL ---------------------


def test_map_page_yields_typed_perf_payload_and_preload_url() -> None:
    page = parse_map_page(PAGE)
    assert isinstance(page, MapPage)
    assert page.perf == {
        "AAA": 0.51,
        "BBB": -0.29,
        "CCC": 1.22,
        "DDD": 0.0,
        "EEE": 2.51,
        "FFF": -1.05,
        "GGG": 0.31,
        "HHH": 0.75,
        "AAA-P": 0.44,
    }
    assert page.subtype == "d1"
    assert page.version == 15
    assert page.payload_hash == "0000FIXTURE0000HASH"
    assert page.hierarchy_url == CHUNK_URL


def test_map_page_no_data_script_is_recognized_empty_not_drift() -> None:
    page = parse_map_page(
        '<html><head><title>S&amp;P 500 Map</title></head><body><div id="root"></div></body></html>'
    )
    assert page.perf == {}
    assert page.subtype is None
    assert page.hierarchy_url is None


def test_map_page_missing_preload_with_perf_is_incomplete_drift() -> None:
    # Perf without the page's own hierarchy preload: the two-request contract
    # cannot be satisfied — incomplete embedded data, never a guessed URL.
    body = PAGE.replace(
        f'<link rel="preload" as="script" href="{CHUNK_URL}" data-chunk-id="map_base_sec">',
        "",
    )
    with pytest.raises(FinvizParseError, match="hierarchy"):
        parse_map_page(body)


def test_map_page_malformed_perf_json_is_drift() -> None:
    body = PAGE.replace('"AAA":0.51,', '"AAA":0.51,,')
    with pytest.raises(FinvizParseError, match="initialPerf"):
        parse_map_page(body)


def test_map_page_perf_nodes_not_object_is_drift() -> None:
    # raw_decode stops at the first complete value, so swapping the nodes
    # object for a string keeps the surrounding payload valid JSON while
    # making ``nodes`` itself a non-object.
    body = PAGE.replace('"nodes":{', '"nodes":"oops",', 1)
    with pytest.raises(FinvizParseError, match="nodes"):
        parse_map_page(body)


# --- hierarchy asset: sector -> industry -> symbol leaves -----------------------


def test_hierarchy_asset_parses_three_levels() -> None:
    root = parse_hierarchy_asset(ASSET)
    assert isinstance(root, HierarchyNode)
    assert root.name == "Root"
    sectors = root.children
    assert [sector.name for sector in sectors] == ["Technology", "Healthcare"]
    software = sectors[0].children[0]
    assert software.name == "Software"
    assert [(leaf.name, leaf.description, leaf.value) for leaf in software.children] == [
        ("AAA", "Alpha App Corp", 12000),
        ("BBB", "Beta Bits Inc", 8000),
        ("CCC", "Gamma Cloud Co", 6000),
    ]
    assert software.parent is sectors[0]
    assert sectors[0].parent is root


def test_hierarchy_leaf_without_value_is_incomplete_drift() -> None:
    body = ASSET.replace(",value:12000", "")
    with pytest.raises(FinvizParseError, match="value"):
        parse_hierarchy_asset(body)


def test_hierarchy_leaf_without_ticker_is_drift() -> None:
    body = ASSET.replace('{name:"AAA",', "{", 1)
    with pytest.raises(FinvizParseError, match="name"):
        parse_hierarchy_asset(body)


def test_hierarchy_asset_without_export_is_drift() -> None:
    with pytest.raises(FinvizParseError, match="export"):
        parse_hierarchy_asset("(self.webpackChunk=[]).push([[8119],{}]);")


def test_hierarchy_empty_children_is_recognized_empty_root() -> None:
    root = parse_hierarchy_asset('e.exports={name:"Root"}')
    assert root.name == "Root"
    assert root.children == ()


def test_hierarchy_non_string_ticker_is_drift() -> None:
    body = ASSET.replace('{name:"AAA",', "{name:12,", 1)
    with pytest.raises(FinvizParseError, match="name"):
        parse_hierarchy_asset(body)
