"""Pure news-page parsers: public global/publisher HTML -> source-near records.

Direct lxml only (xpath, no cssselect). No network, client, or cache imports.
Structure is verified against the 2026-08-30 bounded public probes:

- Global page: ``div#news`` > ``table.news_time-table``; its heading row
  carries ``span.news-calendar_heading`` category names (``News``, ``Blogs``)
  and its second row nests exactly those category tables
  (``styled-table-new ... table-fixed``) in provider order.
- One item row (both surfaces): ``td.news_date-cell`` (the temporal display)
  then ``td.news_link-cell`` with one title anchor. The global anchor is
  ``a.nn-tab-link`` (external href); the publisher anchor is ``a.tab-link``
  (relative ``/news/<id>/<slug>`` href). Publisher pages also carry
  decorative related-ticker badge anchors inside
  ``div.news-badges-container`` (ignored, never fetched).
- Publisher identity: global rows name their publisher through the row's SVG
  icon fragment (``#bloomberg-light`` -> ``bloomberg``); publisher-page rows
  take the caller-supplied slug.

Temporal displays are classified here (``anchored`` clock time,
``relative`` age, ``date_only`` calendar display); instant math against the
fetch time lives in the endpoint module, which owns ``fetched_at``.

Structural drift (no single #news region, News/Blogs headings not first,
category/table count mismatch, a row without its cell pair or title anchor,
unclassifiable temporal display) raises :class:`FinvizParseError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from lxml import html as lxml_html

from finvizp.errors import FinvizParseError

__all__ = ["NewsItem", "parse_global_page", "parse_publisher_page"]

_CLASS = "contains(concat(' ', normalize-space(@class), ' '), ' {} ')"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

_CLOCK = re.compile(r"\d{1,2}:\d{2}[AP]M$", re.I)
_RELATIVE = re.compile(r"\d+\s+(?:min|hrs?|hours?|days?)$", re.I)
_DATE_ONLY = re.compile(r"([A-Za-z]{3})-(\d{1,2})$")
_ICON_FRAGMENT = re.compile(r"#([a-z0-9-]+)-light$", re.I)

# Temporal parse verdicts; the endpoint module resolves typed instants.
ANCHORED = "anchored"
RELATIVE = "relative"
DATE_ONLY = "date_only"


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One source-near news row: identity, exact provider display, verdict."""

    title: str
    url: str
    publisher: str | None
    when_raw: str
    when_status: str


def _classify_when(raw: str) -> str:
    if _CLOCK.match(raw):
        return ANCHORED
    if _RELATIVE.match(raw):
        return RELATIVE
    if (
        match := _DATE_ONLY.match(raw)
    ) and match[1].lower() in _MONTHS and 1 <= int(match[2]) <= 31:
        return DATE_ONLY
    msg = f"cannot parse news temporal display {raw!r}"
    raise FinvizParseError(msg, context={"endpoint": "news"})


def _cell_of(tr: Any, marker: str) -> Any:
    return next(
        (cell for cell in tr.xpath("./td") if marker in (cell.get("class") or "").split()),
        None,
    )


def _row_publisher(tr: Any, default: str | None) -> str | None:
    for use in tr.xpath(".//use"):
        if match := _ICON_FRAGMENT.search(use.get("href") or ""):
            return match[1]
    return default


def _rows_from_table(table: Any, default_publisher: str | None) -> list[NewsItem]:
    items: list[NewsItem] = []
    for tr in table.xpath(".//tr"):
        when_cell = _cell_of(tr, "news_date-cell")
        link_cell = _cell_of(tr, "news_link-cell")
        if when_cell is None or link_cell is None:
            has_anchor = bool(
                tr.xpath(f".//a[{_CLASS.format('tab-link')}]")
                or tr.xpath(f".//a[{_CLASS.format('nn-tab-link')}]")
            )
            if tr.xpath("./td") and has_anchor:
                msg = "news row has no temporal/link cell pair"
                raise FinvizParseError(msg, context={"endpoint": "news"})
            continue  # heading/spacing rows: no data cells, or no item anchors
        raw = when_cell.text_content().strip()
        anchors = link_cell.xpath(f".//a[{_CLASS.format('nn-tab-link')}]") or link_cell.xpath(
            f".//a[{_CLASS.format('tab-link')}]"
        )
        if not anchors:
            msg = "news row has no title anchor"
            raise FinvizParseError(msg, context={"endpoint": "news"})
        title = anchors[0].text_content().strip()
        if not title:
            msg = "news row anchor carries no title text"
            raise FinvizParseError(msg, context={"endpoint": "news"})
        items.append(
            NewsItem(
                title=title,
                url=anchors[0].get("href") or "",
                publisher=_row_publisher(tr, default_publisher),
                when_raw=raw,
                when_status=_classify_when(raw),
            )
        )
    return items


def parse_publisher_page(html: str, *, slug: str) -> list[NewsItem]:
    """Parse one explicit publisher page into ordered :class:`NewsItem` rows.

    Publisher pages carry exactly one ``table-fixed`` item table; related
    ticker badges are decorative and never request anything.
    """
    document = lxml_html.fromstring(html)
    tables = document.xpath(f".//table[{_CLASS.format('table-fixed')}]")
    if len(tables) != 1:
        msg = f"publisher page has no single news table (tables: {len(tables)})"
        raise FinvizParseError(msg, context={"endpoint": "news"})
    return _rows_from_table(tables[0], default_publisher=slug)


def parse_global_page(html: str) -> dict[str, list[NewsItem]]:
    """Parse one global page into category-ordered item lists.

    Returns categories keyed as the page names them (provider order); the
    canonical Time-view contract is News first, Blogs second. Recognized
    empty is a page whose category tables exist with zero item rows.
    """
    document = lxml_html.fromstring(html)
    regions = document.xpath(".//div[@id='news']")
    if len(regions) != 1:
        msg = f"global news page has no single #news region (found: {len(regions)})"
        raise FinvizParseError(msg, context={"endpoint": "news"})
    categories = [
        heading.text_content().strip().lower()
        for heading in regions[0].xpath(f".//span[{_CLASS.format('news-calendar_heading')}]")
    ]
    if categories[:2] != ["news", "blogs"]:
        msg = f"global news page categories are not News/Blogs (found: {categories})"
        raise FinvizParseError(msg, context={"endpoint": "news"})
    tables = regions[0].xpath(
        f".//table[{_CLASS.format('styled-table-new')}][{_CLASS.format('table-fixed')}]"
    )
    if len(tables) < 2:
        msg = f"global news page carries {len(tables)} category tables for {len(categories)}"
        raise FinvizParseError(msg, context={"endpoint": "news"})
    return {
        category: _rows_from_table(table, default_publisher=None)
        for category, table in zip(categories[:2], tables[:2], strict=True)
    }
