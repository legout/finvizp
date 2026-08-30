"""Pure groups-page parser: one page of public HTML -> immutable records.

Direct lxml only (xpath, no cssselect). No network, client, or cache imports.
Structure is header-driven, verified against the 2026-08-30 public groups page:

- exactly one ``table.groups_table`` whose ``thead`` cells name the columns;
  the first two headers are always ``No.`` and ``Name`` (live-verified across
  overview/valuation/performance/custom views);
- data rows carry the group index in the first cell, the group name in the
  second cell (anchor text), then one raw display per remaining column;
- a populated table with zero data rows is the recognized empty state (the
  provider renders the same table shell);
- anything else (no/multiple groups_table, missing header or Name column,
  row/column arity mismatch, duplicate indices) is structural drift and raises
  :class:`FinvizParseError`.

The spectrum (``v=310``) page carries one ``img.groups_spectrum-image`` whose
``src`` names the aggregate spectrum image (``grp_image?spectrum_<dim>.png``);
:func:`parse_spectrum_page` returns an :class:`Artifact` descriptor without
touching the image bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lxml import html as lxml_html

from finvizp.errors import FinvizParseError
from finvizp.models import Artifact

__all__ = ["GroupPage", "GroupRow", "parse_groups_page", "parse_spectrum_page"]

_CLASS = "contains(concat(' ', normalize-space(@class), ' '), ' {} ')"
_TABLE_XPATH = f".//table[{_CLASS.format('groups_table')}]"
_SPECTRUM_XPATH = ".//img[contains(@class, 'groups_spectrum-image')]/@src"


@dataclass(frozen=True, slots=True)
class GroupRow:
    """One source-near group row: typed identity, raw displays."""

    index: int
    name: str
    raw: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GroupPage:
    """One parsed groups page: columns, rows, fingerprint."""

    columns: tuple[str, ...]
    rows: tuple[GroupRow, ...]
    is_empty: bool
    fingerprint: str


def parse_groups_page(html: str) -> GroupPage:
    """Parse one groups page of public HTML into a :class:`GroupPage`."""
    document = lxml_html.fromstring(html)

    tables = document.xpath(_TABLE_XPATH)
    if len(tables) != 1:
        msg = f"groups page has no single groups_table (tables: {len(tables)})"
        raise FinvizParseError(msg, context={"endpoint": "groups"})
    table = tables[0]

    heads = [th.text_content().strip() for th in table.xpath(".//thead//th")]
    if not heads or any(not head for head in heads):
        msg = "groups table has no readable header row"
        raise FinvizParseError(msg, context={"endpoint": "groups"})
    if len(heads) < 2 or heads[0] != "No." or heads[1] != "Name":
        msg = "groups table header must start with 'No.', 'Name'"
        raise FinvizParseError(msg, context={"endpoint": "groups", "columns": heads})
    arity = len(heads)

    rows: list[GroupRow] = []
    seen: set[int] = set()
    for tr in table.xpath("./tr"):
        cells = tr.xpath("./td")
        if not cells:
            continue
        if len(cells) != arity:
            msg = f"groups row has {len(cells)} cells for {arity} columns"
            raise FinvizParseError(msg, context={"endpoint": "groups", "column count": arity})
        index_text = cells[0].text_content().strip()
        if not index_text.isdigit():
            msg = "groups row index is not a number"
            raise FinvizParseError(msg, context={"endpoint": "groups"})
        index = int(index_text)
        if index in seen:
            msg = f"groups page repeats index {index}"
            raise FinvizParseError(msg, context={"endpoint": "groups"})
        seen.add(index)
        rows.append(
            GroupRow(
                index=index,
                name=cells[1].text_content().strip(),
                raw=tuple(cell.text_content().strip() for cell in cells[2:]),
            )
        )

    return GroupPage(
        columns=tuple(heads),
        rows=tuple(rows),
        is_empty=not rows,
        fingerprint=_fingerprint(document),
    )


def parse_spectrum_page(
    html: str,
    *,
    dimension: str = "sector",
    fetched_at: datetime | None = None,
) -> Artifact:
    """Read the spectrum image descriptor from one spectrum page — no bytes.

    ``fetched_at`` stamps the descriptor when provided by the caller (the
    collector passes the response time); the descriptor never carries or
    fetches image content.
    """
    document = lxml_html.fromstring(html)
    srcs = document.xpath(_SPECTRUM_XPATH)
    if len(srcs) != 1:
        msg = f"groups spectrum page has no single spectrum image (found: {len(srcs)})"
        raise FinvizParseError(msg, context={"endpoint": "groups"})
    src: str = srcs[0].strip()
    if src.startswith("//"):
        src = f"https:{src}"
    media_type = "image/png" if ".png" in src.lower() else "image"
    return Artifact(
        source_url=src,
        kind="image",
        media_type=media_type,
        fetched_at=fetched_at or datetime.now().replace(microsecond=0),
        group=dimension,
        chart_type="spectrum",
    )


def _fingerprint(document: Any) -> str:
    """SHA-256 over the ordered table-region skeleton (tags + classes), no values."""
    parts = [
        f"{element.tag}.{element.get('class') or ''}"
        for element in document.iter()
        if isinstance(element.tag, str)
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
