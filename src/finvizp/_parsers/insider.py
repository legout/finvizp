"""Transport-free parsers for the insider feed families.

Per the foundation parser contract (NFR-4): pure lxml/stdlib, header-driven,
independent of transport and environment.

- :func:`parse_insider_table` reads the global ``insider-table`` by its header
  contract (Ticker/Owner/Relationship/Date/Transaction/Cost/#Shares/
  Value ($)/#Shares Total/SEC Form 4) into source-near rows with exactly the
  registered ``quote_insider`` field names — the same contract the ticker
  insider projection produces, so one builder turn normalizes both. The typed
  ``transaction_date`` is the provider's own date; the ``SEC Form 4`` cell
  yields the authoritative filing link. The provider's filing-time display
  keeps no typed column.
- :func:`parse_fund_page` / :func:`parse_manager_page` extract the embedded
  first-party JSON (``initialFundDetailsData`` / ``initialManagerDetailsData``
  plus report dates) verbatim. The live pages carry no HTML tables at all:
  the whole contract is the quarterly 13F/N-PORT portfolio disclosure payload.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lxml import html as lxml_html

from finvizp.errors import FinvizParseError

__all__ = ["parse_fund_page", "parse_insider_table", "parse_manager_page"]

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
# Provider event date display: ``Aug 27 '26`` (quote-page insider tables use
# the same two-digit apostrophe year).
_DATE_DISPLAY = re.compile(r"^(?P<month>[A-Za-z]{3}) (?P<day>\d{1,2}) '(?P<year>\d{2})$")

# Required header contract of the global insider table. Parsing maps by header
# text (never position); a table missing any of these is structure drift.
_REQUIRED_HEADS = (
    "Ticker",
    "Owner",
    "Relationship",
    "Date",
    "Transaction",
    "Cost",
    "#Shares",
    "Value ($)",
    "#Shares Total",
    "SEC Form 4",
)


def _transaction_date(raw: str, row_key: str) -> str | None:
    """``Aug 27 '26`` -> ISO date string; an unparseable display is typed drift."""
    if not raw:
        return None
    match = _DATE_DISPLAY.match(raw)
    if match is None:
        msg = f"cannot parse insider date display for {row_key}"
        raise FinvizParseError(msg, context={"endpoint": "insider"})
    return f"20{match['year']}-{_MONTHS[match['month'].lower()]:02d}-{int(match['day']):02d}"


def parse_insider_table(html: str) -> list[dict[str, Any]]:
    """Parse the global insider table into source-near ``quote_insider`` rows.

    Rows keep provider order. Numeric displays are handed over as text for the
    registry-driven builder to convert (its raw companions retain the exact
    displays). A page without the required table/columns raises typed parse
    drift.
    """
    document = lxml_html.fromstring(html)
    target = None
    for table in document.xpath(".//table[@id='insider-table']"):
        heads = [th.text_content().strip() for th in table.xpath(".//th")]
        if all(head in heads for head in _REQUIRED_HEADS):
            target = (table, heads)
            break
    if target is None:
        msg = "insider page carries no insider trading table with the required headers"
        raise FinvizParseError(msg, context={"endpoint": "insider"})
    table, heads = target
    column = {name: position for position, name in enumerate(heads)}

    def cell(cells: list[Any], name: str) -> str | None:
        position = column.get(name)
        if position is None or position >= len(cells):
            return None
        return cells[position].text_content().strip() or None

    rows: list[dict[str, Any]] = []
    for position, tr in enumerate(table.xpath(".//tr[./td]"), start=1):
        cells = tr.xpath("./td")
        ticker_anchor = (
            cells[column["Ticker"]].xpath(".//a") if column["Ticker"] < len(cells) else []
        )
        ticker = (ticker_anchor[0].text_content().strip() if ticker_anchor else "") or cell(
            cells, "Ticker"
        )
        sec_position = column["SEC Form 4"]
        sec_url = None
        if sec_position < len(cells):
            links = cells[sec_position].xpath(".//a[@href]")
            if links:
                sec_url = links[0].get("href") or None
        rows.append(
            {
                "symbol": ticker or "",
                "owner": cell(cells, "Owner"),
                "relationship": cell(cells, "Relationship"),
                "transaction_date": _transaction_date(cell(cells, "Date") or "", f"row {position}"),
                "transaction_type": cell(cells, "Transaction"),
                "cost": cell(cells, "Cost"),
                "shares": cell(cells, "#Shares"),
                "value": cell(cells, "Value ($)"),
                "shares_total": cell(cells, "#Shares Total"),
                "sec_form_url": sec_url,
            }
        )
    return rows


_EMBEDDED_ROOTS = {
    "initialFundDetailsData": "initialFundReportDates",
    "initialManagerDetailsData": "initialManagerReportDates",
}


def _embedded_payload(html: str, root_key: str, kind: str) -> dict[str, Any]:
    """Extract one embedded ``<script>{...}</script>`` payload by its root key."""
    for script in lxml_html.fromstring(html).xpath("//script"):
        text = (script.text or "").strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get(root_key), dict):
            dates = payload.get(_EMBEDDED_ROOTS[root_key], [])
            return {
                "details": payload[root_key],
                "report_dates": [str(date) for date in dates] if isinstance(dates, list) else [],
            }
    msg = f"{kind} page carries no embedded {root_key} JSON payload"
    raise FinvizParseError(msg, context={"endpoint": "insider"})


def parse_fund_page(html: str) -> dict[str, Any]:
    """Extract one N-PORT fund page's embedded portfolio JSON verbatim."""
    return _embedded_payload(html, "initialFundDetailsData", "fund")


def parse_manager_page(html: str) -> dict[str, Any]:
    """Extract one 13F manager page's embedded portfolio JSON verbatim."""
    return _embedded_payload(html, "initialManagerDetailsData", "manager")
