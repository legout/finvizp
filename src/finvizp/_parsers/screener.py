"""Pure screener-table parser: one page of public HTML -> immutable page record.

Direct lxml only (xpath, no cssselect). No network, client, or cache imports.

Two provider shapes are accepted:

1. **Legacy HTML table** (pre-2026-10): one ``table.screener_table`` whose
   ``thead`` cells name the columns; data rows carry absolute rank, ticker,
   then raw displays; ``#start / total Total`` page marker.
2. **Embedded JSON** (2026-10+ public redesign): a ``<script>`` JSON blob with
   ``data.rows`` (``text``/``numbers`` bags), ``data.totalCount`` /
   ``data.firstRow`` / ``data.pageSize``. No ``table.screener_table``.

``table#js-screener-body-empty`` with ``0 Total`` remains the positively
recognized no-results state for the legacy shape. JSON empty is
``data.totalCount == 0`` with ``data.rows == []``.

Anything else is structural drift and raises :class:`FinvizParseError`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lxml import html as lxml_html

from finvizp.errors import FinvizParseError

__all__ = ["ScreenerPage", "ScreenerRow", "parse_screener_page"]

_CLASS = "contains(concat(' ', normalize-space(@class), ' '), ' {} ')"
_TABLE_XPATH = f".//table[{_CLASS.format('screener_table')}]"
_EMPTY_XPATH = ".//table[@id='js-screener-body-empty']"
_MARKER_XPATH = "//*[@id='screener-total']/text()"

# JSON field key -> legacy header label expected by screener._table_from_page.
# Keys not listed are skipped (card chrome, www, type, …).
_JSON_FIELD_LABELS: dict[str, str] = {
    "row": "No.",
    "ticker": "Ticker",
    "company": "Company",
    "sector": "Sector",
    "industry": "Industry",
    "country": "Country",
    "marketCap": "Market Cap",
    "PE": "P/E",
    "forwardPE": "Forward P/E",
    "PEG": "PEG",
    "PS": "P/S",
    "PB": "P/B",
    "PC": "P/Cash",
    "PFCF": "P/FCF",
    "eps": "EPS",
    "epsYoY": "EPS this Y",
    "epsYoY1": "EPS next Y",
    "eps5Years": "EPS past 5Y",
    "estLTGrowth": "EPS next 5Y",
    "sales5Years": "Sales past 5Y",
    "epsQoQ": "EPS growth quarter",
    "salesQoQ": "Sales growth quarter",
    "dividendYield": "Dividend %",
    "payoutRatio": "Payout Ratio",
    "sharesOutstanding2": "Shares Outstanding",
    "sharesFloat": "Shares Float",
    "insiderOwn": "Insider Own",
    "insiderTrans": "Insider Trans",
    "instOwn": "Inst Own",
    "instTrans": "Inst Trans",
    "shortInterestShare": "Float Short",
    "shortInterestRatio": "Short Ratio",
    "shortInterest": "Short Interest",
    "roa": "ROA",
    "roe": "ROE",
    "roi": "ROI",
    "curRatio": "Curr R",
    "quickRatio": "Quick R",
    "ltdebtEq": "LTDebt/Eq",
    "debtEq": "Debt/Eq",
    "grossMargin": "Gross M",
    "operMargin": "Oper M",
    "netMargin": "Profit M",
    "profitMargin": "Profit M",
    "price": "Price",
    "change": "Change %",
    "volume": "Volume",
    "prevClose": "Previous Close",
    "earningsDate": "Earnings Date",
    "beta": "Beta",
    "averageTrueRange": "ATR",
    "atr": "ATR",
    "rsi": "RSI",
    "targetPrice": "Target Price",
    "employees": "Employees",
    "sales": "Sales",
    "income": "Income",
    "index": "Index",
    "optionable": "Optionable",
    "shortable": "Shortable",
    "perf1w": "Perf Week",
    "perf4w": "Perf Month",
    "perf13w": "Perf Quart",
    "perf26w": "Perf Half Y",
    "perf52w": "Perf Year",
    "perfYtd": "Perf YTD",
    "sma20": "SMA20",
    "sma50": "SMA50",
    "sma200": "SMA200",
    "high52w": "52W High",
    "low52w": "52W Low",
    "high50d": "50-Day High",
    "low50d": "50-Day Low",
    "relativeVolume": "Rel Volume",
    "averageVolume": "Avg Volume",
    "gap": "Gap",
    "recom": "Recom",
    "book": "Book value per share",
    "cashPerShare": "Cash per share",
    "dividend": "Dividend",
    "estQ1": "EPS estimate next quarter",
    "ipoDate": "IPO Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "changeopen": "Change from Open %",
    "volatility1w": "Volatility W",
    "volatility4w": "Volatility M",
    "floatoutstandingpct": "Float/Outstanding",
}

# Provider column code (from ``c=`` / overview selectedColumns) -> JSON key.
# Stable across pages so sparse bags cannot drift the Arrow schema mid-walk.
_CODE_TO_JSON_KEY: dict[str, str] = {
    "0": "row",
    "1": "ticker",
    "2": "company",
    "3": "sector",
    "4": "industry",
    "5": "country",
    "6": "marketCap",
    "7": "PE",
    "8": "forwardPE",
    "9": "PEG",
    "10": "PS",
    "11": "PB",
    "12": "PC",
    "13": "PFCF",
    "14": "dividendYield",
    "15": "payoutRatio",
    "16": "eps",
    "17": "epsYoY",
    "18": "epsYoY1",
    "19": "eps5Years",
    "20": "estLTGrowth",
    "21": "sales5Years",
    "22": "epsQoQ",
    "23": "salesQoQ",
    "24": "sharesOutstanding2",
    "25": "sharesFloat",
    "26": "insiderOwn",
    "27": "insiderTrans",
    "28": "instOwn",
    "29": "instTrans",
    "30": "shortInterestShare",
    "31": "shortInterestRatio",
    "32": "roa",
    "33": "roe",
    "34": "roi",
    "35": "curRatio",
    "36": "quickRatio",
    "37": "ltdebtEq",
    "38": "debtEq",
    "39": "grossMargin",
    "40": "operMargin",
    "41": "netMargin",
    "42": "perf1w",
    "43": "perf4w",
    "44": "perf13w",
    "45": "perf26w",
    "46": "perf52w",
    "47": "perfYtd",
    "48": "beta",
    "49": "averageTrueRange",
    "50": "volatility1w",
    "51": "volatility4w",
    "52": "sma20",
    "53": "sma50",
    "54": "sma200",
    "55": "high50d",
    "56": "low50d",
    "57": "high52w",
    "58": "low52w",
    "59": "rsi",
    "60": "changeopen",
    "61": "gap",
    "62": "recom",
    "63": "averageVolume",
    "64": "relativeVolume",
    "65": "price",
    "66": "change",
    "67": "volume",
    "68": "earningsDate",
    "69": "targetPrice",
    "70": "ipoDate",
    "73": "book",
    "74": "cashPerShare",
    "75": "dividend",
    "76": "employees",
    "77": "estQ1",
    "78": "income",
    "79": "index",
    "80": "optionable",
    "81": "prevClose",
    "82": "sales",
    "83": "shortable",
    "84": "shortInterest",
    "85": "floatoutstandingpct",
    "86": "open",
    "87": "high",
    "88": "low",
}

# Overview selectedColumns name -> JSON key (when URL has no ``c=`` list).
_SELECTED_TO_JSON_KEY: dict[str, str] = {
    "row": "row",
    "ticker": "ticker",
    "company": "company",
    "sector": "sector",
    "industry": "industry",
    "country": "country",
    "marketCap": "marketCap",
    "PE": "PE",
    "price": "price",
    "change": "change",
    "volume": "volume",
}

_SELECTED_SKIP = frozenset({"row"})
_URL_C_RE = re.compile(r"[?&]c=([^&]+)")


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

    json_page = _try_parse_json_payload(document)
    if json_page is not None:
        return json_page

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


def _try_parse_json_payload(document: Any) -> ScreenerPage | None:
    """Parse the 2026-10+ embedded screener JSON blob, or return None if absent."""
    payloads: list[dict[str, Any]] = []
    for script in document.xpath(".//script"):
        text = (script.text or "").strip()
        if not text.startswith("{"):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            blob = data["data"]
            if "rows" in blob and "totalCount" in blob:
                payloads.append(data)
    if not payloads:
        return None
    if len(payloads) != 1:
        msg = f"screener page has {len(payloads)} JSON data payloads"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    return _page_from_json(payloads[0])


def _page_from_json(payload: dict[str, Any]) -> ScreenerPage:
    """Map one embedded screener JSON object to :class:`ScreenerPage`."""
    data = payload["data"]
    raw_rows = data.get("rows") or []
    total = data.get("totalCount")
    first_row = data.get("firstRow")
    if not isinstance(total, int) or total < 0:
        msg = "screener JSON totalCount is missing or invalid"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    if not isinstance(first_row, int) or first_row < 0:
        msg = "screener JSON firstRow is missing or invalid"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    if total == 0 or not raw_rows:
        return ScreenerPage(
            columns=(),
            rows=(),
            page_start=None,
            total_rows=0,
            is_empty=True,
            fingerprint=_json_fingerprint(payload),
        )

    field_keys = _json_field_order(payload, raw_rows)
    labels = tuple(_JSON_FIELD_LABELS[key] for key in field_keys)
    # ScreenerPage.columns includes No. + Ticker + value columns (legacy shape).
    columns = ("No.", "Ticker", *labels)

    rows: list[ScreenerRow] = []
    seen_ranks: set[int] = set()
    for index, item in enumerate(raw_rows):
        if not isinstance(item, dict):
            msg = "screener JSON row is not an object"
            raise FinvizParseError(msg, context={"endpoint": "screener"})
        text = item.get("text") or {}
        numbers = item.get("numbers") or {}
        if not isinstance(text, dict) or not isinstance(numbers, dict):
            msg = "screener JSON row missing text/numbers bags"
            raise FinvizParseError(msg, context={"endpoint": "screener"})
        symbol = str(text.get("ticker") or "").strip().upper()
        if not symbol:
            msg = "screener JSON row has no ticker"
            raise FinvizParseError(msg, context={"endpoint": "screener"})
        order = numbers.get("order")
        rank = int(order) + 1 if isinstance(order, int) else first_row + index + 1
        if rank in seen_ranks:
            msg = f"screener page repeats rank {rank}"
            raise FinvizParseError(msg, context={"endpoint": "screener"})
        seen_ranks.add(rank)
        displays: list[str] = []
        for key in field_keys:
            value = text.get(key) if key in text else numbers.get(key)
            displays.append(_json_display(key, value))
        rows.append(ScreenerRow(rank=rank, symbol=symbol, raw=tuple(displays)))

    return ScreenerPage(
        columns=columns,
        rows=tuple(rows),
        page_start=first_row + 1,
        total_rows=total,
        is_empty=False,
        fingerprint=_json_fingerprint(payload),
    )


def _json_field_order(payload: dict[str, Any], raw_rows: list[Any]) -> tuple[str, ...]:
    """Stable value-field order from request ``c=`` codes or selectedColumns.

    Never derive the schema from whichever keys happen to be present on this
    page's sparse row bags — that drifts across pages of the same walk.
    """
    del raw_rows  # present only for call-site compatibility
    ordered: list[str] = []
    seen: set[str] = set()

    url = str(payload.get("url") or "")
    match = _URL_C_RE.search(url)
    if match:
        for code in match.group(1).split(","):
            code = code.strip()
            key = _CODE_TO_JSON_KEY.get(code)
            if key is None or key in {"row", "ticker"} or key in seen:
                continue
            if key not in _JSON_FIELD_LABELS:
                continue
            ordered.append(key)
            seen.add(key)
    else:
        selected = payload.get("selectedColumns") or []
        if isinstance(selected, list):
            for name in selected:
                if not isinstance(name, str) or name in _SELECTED_SKIP:
                    continue
                key = _SELECTED_TO_JSON_KEY.get(name, name)
                if key in {"ticker"} or key in seen or key not in _JSON_FIELD_LABELS:
                    continue
                ordered.append(key)
                seen.add(key)

    if not ordered:
        msg = "screener JSON has no recognized value columns"
        raise FinvizParseError(msg, context={"endpoint": "screener"})
    return tuple(ordered)


def _json_display(key: str, value: Any) -> str:
    """Format one JSON cell as a legacy-style display string."""
    if value is None:
        return "-"
    if key == "earningsDate":
        return _format_earnings_date(value)
    if key in {"company", "sector", "industry", "country", "index", "optionable", "shortable"}:
        text = str(value).strip()
        return text or "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool):
        if key == "volume":
            return f"{value:,}"
        return str(value)
    if isinstance(value, float):
        if key == "marketCap":
            return _format_compact_millions(value)
        if key in {
            "change",
            "dividendYield",
            "payoutRatio",
            "insiderOwn",
            "insiderTrans",
            "instOwn",
            "instTrans",
            "shortInterestShare",
            "roa",
            "roe",
            "roi",
            "grossMargin",
            "operMargin",
            "profitMargin",
            "epsYoY",
            "epsYoY1",
            "eps5Years",
            "estLTGrowth",
            "sales5Years",
            "epsQoQ",
            "salesQoQ",
            "floatoutstandingpct",
            "perfWeek",
            "perfMonth",
            "perfQuarter",
            "perfHalf",
            "perfYear",
            "perfYTD",
            "sma20",
            "sma50",
            "sma200",
            "high52w",
            "low52w",
            "gap",
        }:
            # JSON already stores percent points (3.06 == 3.06%).
            return f"{value:.4g}%"
        if key in {"sharesOutstanding2", "sharesFloat", "sales", "income", "avgVolume"}:
            return _format_compact_millions(value) if key in {"sales", "income"} else f"{value:g}"
        return f"{value:g}"
    return str(value).strip() or "-"


def _format_compact_millions(value: float) -> str:
    """Format a millions-denominated magnitude as Finviz compact display."""
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1_000_000:
        return f"{sign}{abs_v / 1_000_000:.2f}T"
    if abs_v >= 1_000:
        return f"{sign}{abs_v / 1_000:.2f}B"
    if abs_v >= 1:
        return f"{sign}{abs_v:.2f}M"
    if abs_v >= 0.001:
        return f"{sign}{abs_v * 1_000:.2f}K"
    return f"{sign}{abs_v:g}"


def _format_earnings_date(value: Any) -> str:
    """ISO timestamp/date -> ``Mon DD, YYYY [BMO|AMC]`` display for earnings projection."""
    text = str(value).strip()
    if not text:
        return "-"
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    session = ""
    if parsed.hour or parsed.minute or parsed.second:
        # Provider wall-clock on the earnings event: morning = BMO, else AMC.
        session = " BMO" if parsed.hour < 12 else " AMC"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}{session}"


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


def _json_fingerprint(payload: dict[str, Any]) -> str:
    """SHA-256 over JSON skeleton (keys + pagination), no cell values."""
    data = payload.get("data") or {}
    skeleton = {
        "selectedColumns": payload.get("selectedColumns"),
        "totalCount": data.get("totalCount"),
        "firstRow": data.get("firstRow"),
        "pageSize": data.get("pageSize"),
        "row_count": len(data.get("rows") or []),
        "text_keys": sorted(
            {
                key
                for row in (data.get("rows") or [])
                if isinstance(row, dict)
                for key in (row.get("text") or {})
            }
        ),
        "number_keys": sorted(
            {
                key
                for row in (data.get("rows") or [])
                if isinstance(row, dict)
                for key in (row.get("numbers") or {})
            }
        ),
    }
    blob = json.dumps(skeleton, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
