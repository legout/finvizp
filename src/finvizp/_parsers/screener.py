"""Pure screener-table parser: one page of public HTML -> immutable page record.

Direct lxml only (xpath, no cssselect). No network, client, or cache imports.
Structure is header-driven, verified against the 2026-08 public screener page:

- one ``table.screener_table`` whose ``thead`` cells name the columns (the
  fixed named views render their registered column labels; the custom view
  renders exactly the requested column labels);
- data rows carry the provider's absolute rank on the screen in the first
  cell, the ticker in the second cell (``data-boxover-ticker`` attribute,
  anchor text fallback), then one raw display per remaining column;
- a ``#<start> / <total> Total`` page marker names the first rank shown and
  the provider's screen size — pagination evidence, never a universe promise;
- ``table#js-screener-body-empty`` with ``0 Total`` is the positively
  recognized no-results state (no table element at all);
- anything else (missing header, row/column arity mismatch, duplicate or
  non-numeric ranks, missing page marker on a populated table) is structural
  drift and raises :class:`FinvizParseError`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from lxml import html as lxml_html

from finvizp.errors import FinvizParseError

__all__ = ["ScreenerPage", "ScreenerRow", "parse_screener_page"]

_CLASS = "contains(concat(' ', normalize-space(@class), ' '), ' {} ')"
_TABLE_XPATH = f".//table[{_CLASS.format('screener_table')}]"
_EMPTY_XPATH = ".//table[@id='js-screener-body-empty']"
_MARKER_XPATH = "//*[@id='screener-total']/text()"


@dataclass(frozen=True, slots=True)
class ScreenerRow:
    """One source-near screen row: typed identity, raw displays."""

    rank: int
    symbol: str
    raw: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScreenerPage:
    """One parsed screen page: columns, rows, pagination evidence, fingerprint."""

    columns: tuple[str, ...]
    rows: tuple[ScreenerRow, ...]
    page_start: int | None
    total_rows: int | None
    is_empty: bool
    fingerprint: str


def parse_screener_page(html: str) -> ScreenerPage:
    """Parse one screener page of public HTML into a :class:`ScreenerPage`."""
    document = lxml_html.fromstring(html)

    empty_nodes = document.xpath(_EMPTY_XPATH)
    if empty_nodes:
        return ScreenerPage(
            columns=(),
            rows=(),
            page_start=None,
            total_rows=0,
            is_empty=True,
            fingerprint=_fingerprint(document),
        )

    tables = document.xpath(_TABLE_XPATH)
    if len(tables) != 1:
        msg = f"screener page has no recognized results table (tables: {len(tables)})"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    table = tables[0]

    heads = [th.text_content().strip() for th in table.xpath(".//thead//th")]
    if not heads or any(not head for head in heads):
        msg = "screener table has no readable header row"
        raise FinvizParseError(msg, context={"endpoint": "screener"})

    page_start, total_rows = _parse_page_marker(document, require=bool(heads))
    arity = len(heads)

    rows: list[ScreenerRow] = []
    seen_ranks: set[int] = set()
    for tr in table.xpath("./tr"):
        cells = tr.xpath("./td")
        if not cells:
            continue
        if len(cells) != arity:
            msg = f"screener row has {len(cells)} cells for {arity} columns"
            raise FinvizParseError(msg, context={"endpoint": "screener", "column count": arity})
        rank_text = cells[0].text_content().strip()
        if not rank_text.isdigit():
            msg = "screener row rank is not a number"
            raise FinvizParseError(msg, context={"endpoint": "screener"})
        rank = int(rank_text)
        if rank in seen_ranks:
            msg = f"screener page repeats rank {rank}"
            raise FinvizParseError(msg, context={"endpoint": "screener"})
        seen_ranks.add(rank)
        rows.append(
            ScreenerRow(
                rank=rank,
                symbol=_row_symbol(cells[1]),
                raw=tuple(cell.text_content().strip() for cell in cells[2:]),
            )
        )

    return ScreenerPage(
        columns=tuple(heads),
        rows=tuple(rows),
        page_start=page_start,
        total_rows=total_rows,
        is_empty=False,
        fingerprint=_fingerprint(document),
    )


def _row_symbol(ticker_cell: Any) -> str:
    """Ticker from the ``data-boxover-ticker`` attribute, anchor text fallback."""
    ticker = ticker_cell.get("data-boxover-ticker")
    if ticker:
        return ticker.strip().upper()
    anchors = ticker_cell.xpath(".//a")
    if anchors:
        return anchors[0].text_content().strip().upper()
    return ticker_cell.text_content().strip().upper()


def _parse_page_marker(document: Any, *, require: bool) -> tuple[int | None, int | None]:
    """``#<start> / <total> Total`` page marker -> (start, total)."""
    texts = document.xpath(_MARKER_XPATH)
    for text in texts:
        match = re.match(r"#(\d+)\s*/\s*([\d,]+)\s*Total", text.strip())
        if match:
            return int(match.group(1)), int(match.group(2).replace(",", ""))
    if require:
        msg = "screener page marker (#N / M Total) not found"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    return None, None


def _fingerprint(document: Any) -> str:
    """SHA-256 over the ordered table-region skeleton (tags + classes), no values."""
    parts = [
        f"{element.tag}.{element.get('class') or ''}"
        for element in document.iter()
        if isinstance(element.tag, str)
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
