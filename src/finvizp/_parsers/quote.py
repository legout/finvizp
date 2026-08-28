"""Complete stock-page parser: one coherent page -> immutable QuoteBundle.

Direct lxml only (xpath, no cssselect dependency). No network, client, or
cache imports. Regions are located by structural/header markers verified
against the 2026-08 stock page, never by global table indices:

- six ``snapshot-table2`` tables, each cell a label/value ``td`` pair;
- ratings rows under the ``js-table-ratings`` header table;
- news rows inside ``#news-table``;
- insider rows under a table whose header contains ``SEC Form 4``;
- peers and ETF holders as ``data-boxover-ticker`` spans introduced by a
  ``Peers`` / ``Held by`` link;
- signal links as screener signal anchors;
- chart images as artifact descriptors;
- identity/classification from the ``quote-header`` region.

The parser emits source-near rows for the registry-driven Arrow builder
(``finvizp.arrow``), which owns unit conversion, ``_raw``/``_status``
companions, and additive ``extra_fields`` drift. The parser therefore:

- maps verified provider labels to ``quote_snapshot`` registry fields and
  passes every unmapped label through as its own row key (builder routes it
  to ``extra_fields`` with an ``unknown_field`` warning);
- enforces the verified six-region snapshot contract: fewer regions, or a
  region with malformed label/value cell pairs, raises ``FinvizParseError``;
- normalizes provider temporal displays into builder-compatible shapes:
  full datetimes become ISO ``YYYY-MM-DD HH:MM`` US-Eastern strings, news
  time-only displays keep ``HH:MM`` so the builder anchors them to the
  response date with an ``anchored`` status;
- never sets ``_raw``/``_status``/``fetched_at``/``extra_fields`` itself.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Callable
from typing import Any

from lxml import html as lxml_html

from finvizp import arrow as fa
from finvizp.errors import FetchWarning, FinvizParseError
from finvizp.models import Artifact, QuoteBundle
from finvizp.results import AccessTier, ResultStatus

__all__ = ["parse_quote_page"]

WarningCallback = Callable[[FetchWarning], Any]

_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

# Verified snapshot-label -> quote_snapshot field(s). Only registry fields
# appear here; every other label is additive and flows to extra_fields. A
# tuple value splits a paired display ("0.88 (0.28%)") into two fields.
_SNAPSHOT_LABELS: dict[str, str | tuple[str, str]] = {
    "Index": "index_membership",
    "Market Cap": "market_cap",
    "P/E": "pe_ratio",
    "EPS (ttm)": "eps_ttm",
    "Dividend TTM": "dividend_yield",
    "Dividend Ex-Date": "ex_dividend_date",
    "Earnings": "earnings_date",
    "Employees": "employees",
    "IPO Date": "ipo_date",
    "SMA20": "sma20_percent",
    "SMA50": "sma50_percent",
    "SMA200": "sma200_percent",
    "Shs Outstand": "shares_outstanding",
    "Shs Float": "float_shares",
    "Beta": "beta",
    "Avg Volume": "average_volume",
    "Volume": "volume",
    "Price": "price",
    "Change": ("change", "change_percent"),
}

_OPTIONAL_REGIONS = (
    "description",
    "ratings",
    "news",
    "insider",
    "peers",
    "etf_holders",
    "signals",
)

_PAREN_PERCENT = re.compile(r"\(([-+]?[\d.]+%)\)")
_US_DATE = re.compile(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})")
_CLASS = "contains(concat(' ', normalize-space(@class), ' '), ' {} ')"


def parse_quote_page(
    html: str,
    *,
    fetched_at: dt.datetime,
    response_date: dt.date | None = None,
    strict_schema: bool = False,
    on_warning: WarningCallback | None = None,
) -> QuoteBundle:
    """Parse one coherent stock page into a :class:`QuoteBundle`."""
    warnings: list[FetchWarning] = []

    def warn(code: str, message: str) -> None:
        warning = FetchWarning(code=code, message=message)
        warnings.append(warning)
        if on_warning is not None:
            on_warning(warning)

    document = lxml_html.fromstring(html)

    symbol, company, sector, industry, country, exchange = _parse_header(document)
    if symbol is None:
        raise FinvizParseError(
            "stock page has no quote header ticker", context={"endpoint": "quote"}
        )

    fingerprint = _fingerprint(document)
    per_table = _merge_snapshot_tables(document, warn)
    if not per_table:
        raise FinvizParseError(
            "no snapshot-table2 regions found on stock page",
            context={"endpoint": "quote"},
        )

    # Source-near row: registry-mapped fields plus every unknown label as its
    # own key. The Arrow builder routes unknown keys into extra_fields with a
    # drift warning and fills provenance/raw/status companions.
    row: dict[str, Any] = {
        "symbol": symbol,
        "company": company,
        "sector": sector,
        "industry": industry,
        "country": country,
        "exchange": exchange,
    }
    for table in per_table:
        for label, value in table:
            target = _SNAPSHOT_LABELS.get(label)
            if target is None:
                row.setdefault(label, value)
                continue
            targets = target if isinstance(target, tuple) else (target,)
            prepared = _prepare_values(label, value)
            if len(prepared) != len(targets):
                warn("conversion_failed", f"unexpected display {value!r} for {label!r}")
                row.setdefault(label, value)
                continue
            for name, prepared_value in zip(targets, prepared, strict=True):
                if row.get(name) is not None:
                    warn("duplicate_label", f"duplicate snapshot label {label!r}")
                    continue
                row[name] = prepared_value

    def build(dataset: str, rows: list[dict[str, Any]]) -> Any:
        return fa.build_table(
            dataset,
            rows,
            fetched_at=fetched_at,
            response_date=response_date,
            strict_schema=strict_schema,
            on_warning=on_warning,
        )

    def presence(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        # ``None`` means the region structure itself is absent (missing
        # optional region); ``[]`` means the structure is present but empty —
        # the no-results state is positively recognized, so the registered
        # empty Arrow table is preserved.
        return [] if rows is None else rows

    snapshot = build("quote_snapshot", [row])
    description_text = _parse_description(document)
    ratings_rows = _parse_ratings(document, symbol, warn)
    news_rows = _parse_news(document, symbol, response_date)
    insider_rows = _parse_insider(document, symbol, warn)
    peers_entries = _parse_ticker_links(document, "Peers")
    etf_entries = _parse_ticker_links(document, "Held by")
    signals_rows = _parse_signals(document, symbol)

    def rows_or_empty(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        # ``None`` means the region structure itself is absent (missing
        # optional region); ``[]`` means the structure is present but empty —
        # the no-results state is positively recognized, so the registered
        # empty Arrow table is preserved.
        return [] if rows is None else rows

    description = build(
        "quote_description",
        rows_or_empty(
            [{"symbol": symbol, "description": description_text}]
            if description_text is not None
            else None
        ),
    )
    ratings = build("quote_ratings", rows_or_empty(ratings_rows))
    news = build("quote_news", rows_or_empty(news_rows))
    insider = build("quote_insider", rows_or_empty(insider_rows))
    # market_cap needs its raw display; the builder mirrors the base input, so
    # pass the display itself and let compact conversion derive the number.
    peers = build(
        "quote_peers",
        rows_or_empty(
            None
            if peers_entries is None
            else [
                {
                    "symbol": symbol,
                    "peer": peer,
                    "rank": position + 1,
                    "market_cap": raw,
                }
                for position, (peer, _value, raw) in enumerate(peers_entries)
            ],
        ),
    )
    etf_holders = build(
        "quote_etf_holders",
        rows_or_empty(
            None
            if etf_entries is None
            else [
                {
                    "symbol": symbol,
                    "etf": etf,
                    "rank": position + 1,
                    # AUM-style values are fund sizes, not holding weights: keep
                    # them out of weight_percent; the raw display lands in
                    # extra_fields via the unknown 'data_boxover_value' key.
                    "weight_percent": None,
                }
                for position, (etf, _size, _raw) in enumerate(etf_entries)
            ],
        ),
    )
    signals = build("quote_signals", rows_or_empty(signals_rows))
    artifacts = _parse_charts(document, symbol, fetched_at)

    # Presence, not row count, decides a missing region: a present-but-empty
    # relation is a positively recognized no-results state (its Arrow table
    # exists with the registered schema), so only structure absence is drift.
    present = {
        "description": description_text is not None,
        "ratings": ratings_rows is not None,
        "news": news_rows is not None,
        "insider": insider_rows is not None,
        "peers": peers_entries is not None,
        "etf_holders": etf_entries is not None,
        "signals": signals_rows is not None,
    }
    missing = [name for name in _OPTIONAL_REGIONS if not present[name]]
    if strict_schema and missing:
        raise FinvizParseError(
            f"missing optional region(s) {', '.join(missing)} (strict_schema=True)",
            context={"endpoint": "quote"},
        )
    for name in missing:
        warn("missing_region", f"optional stock page region {name!r} not found")
    if not missing:
        status = ResultStatus.COMPLETE
    elif len(missing) == len(_OPTIONAL_REGIONS):
        status = ResultStatus.EMPTY
    else:
        status = ResultStatus.PARTIAL

    return QuoteBundle(
        symbol=symbol,
        fetched_at=fetched_at,
        snapshot=snapshot,
        # Present-but-empty regions keep their registered empty Arrow table
        # on the bundle; only structure absence yields None.
        description=description if present["description"] else None,
        ratings=ratings if present["ratings"] else None,
        news=news if present["news"] else None,
        insider=insider if present["insider"] else None,
        peers=peers if present["peers"] else None,
        etf_holders=etf_holders if present["etf_holders"] else None,
        signals=signals if present["signals"] else None,
        artifacts=artifacts,
        snapshot_tables=_per_table_projections(per_table),
        status=status,
        access_tier=AccessTier.PUBLIC,
        fingerprint=fingerprint,
    )


def _prepare_values(label: str, value: str) -> tuple[str, ...]:
    """Normalize one provider display into builder-ready source-near strings."""
    if label in {"Dividend Ex-Date", "IPO Date"}:
        if match := _US_DATE.match(value):
            month_name, day, year = match.groups()
            return (f"{year}-{_MONTHS[month_name]:02d}-{int(day):02d}",)
        return (value,)
    if label in {"Dividend TTM", "Change"}:
        if match := _PAREN_PERCENT.search(value):
            if label == "Dividend TTM":
                return (match.group(1),)
            return (value[: match.start()].strip(), match.group(1))
        return (value,) if label == "Dividend TTM" else (value, "")
    return (value,)


def _merge_snapshot_tables(
    document: Any,
    warn: Callable[[str, str], None],
) -> list[list[tuple[str, str]]]:
    """Merge the verified six ``snapshot-table2`` regions, per-table grouped.

    Fewer regions (a required region vanished) or malformed pair structure
    (odd snapshot cell count inside a region) is required-structure drift
    and raises ``FinvizParseError`` — never a silent COMPLETE parse.
    """
    per_table: list[list[tuple[str, str]]] = []
    seen: set[str] = set()
    tables = document.xpath(f".//table[{_CLASS.format('snapshot-table2')}]")
    if len(tables) != 6:
        raise FinvizParseError(
            f"expected 6 snapshot-table2 regions on stock page, found {len(tables)}",
            context={"endpoint": "quote"},
        )
    for table in tables:
        cells = table.xpath(f".//td[{_CLASS.format('snapshot-td2')}]")
        if len(cells) % 2:
            raise FinvizParseError(
                "snapshot region has malformed label/value pair structure "
                f"({len(cells)} snapshot cells)",
                context={"endpoint": "quote"},
            )
        pairs: list[tuple[str, str]] = []
        for position in range(0, len(cells) - 1, 2):
            label = cells[position].text_content().strip()
            value = cells[position + 1].text_content().strip()
            if not label:
                continue
            if label in seen:
                warn("duplicate_label", f"duplicate snapshot label {label!r}")
                continue
            seen.add(label)
            pairs.append((label, value))
        if pairs:
            per_table.append(pairs)
    return per_table


def _per_table_projections(
    per_table: list[list[tuple[str, str]]],
) -> dict[str, Any]:
    """One immutable label/value table per physical snapshot region."""
    import pyarrow as pa

    schema = pa.schema([pa.field("label", pa.string()), pa.field("value", pa.string())])
    return {
        f"table_{index}": pa.table(
            {
                "label": [label for label, _ in pairs],
                "value": [value for _, value in pairs],
            },
            schema=schema,
        )
        for index, pairs in enumerate(per_table, start=1)
    }


def _parse_header(document: Any) -> tuple[str | None, ...]:
    headers = document.xpath(f".//div[{_CLASS.format('quote-header')}]")
    if not headers:
        return None, None, None, None, None, None
    header = headers[0]
    tickers = header.xpath(".//*[@data-ticker]")
    symbol = tickers[0].get("data-ticker") if tickers else None
    companies = header.xpath(f".//h2[{_CLASS.format('quote-header_ticker-wrapper_company')}]")
    company = companies[0].text_content().strip() if companies else None
    # Category anchors embed screener filter codes (sec_/ind_/geo_/exch_); map
    # by code, not position, so partial category lists still classify.
    codes: dict[str, str] = {}
    for anchor in header.xpath(f".//a[{_CLASS.format('quote-header_category')}]"):
        if match := re.search(r"f=([a-z]+)_([a-z0-9]+)", anchor.get("href") or ""):
            kind, _value = match.groups()
            codes.setdefault(kind, anchor.text_content().strip())
    exchange = codes.get("exch")
    if exchange == "NASD":
        exchange = "NASDAQ"
    return (
        symbol,
        company or None,
        codes.get("sec"),
        codes.get("ind"),
        codes.get("geo"),
        exchange,
    )


def _parse_description(document: Any) -> str | None:
    nodes = document.xpath(f".//div[{_CLASS.format('quote_profile-bio')}]")
    if not nodes:
        return None
    return nodes[0].text_content().strip() or None


def _parse_ratings(
    document: Any,
    symbol: str,
    warn: Callable[[str, str], None],
) -> list[dict[str, Any]] | None:
    tables = document.xpath(f".//table[{_CLASS.format('js-table-ratings')}]")
    if not tables:
        return None
    rows: list[dict[str, Any]] = []
    for tr in tables[0].xpath(".//tr"):
        cells = tr.xpath("./td")
        if len(cells) < 5:
            continue
        date_text = cells[0].text_content().strip()
        target_text = cells[4].text_content().strip().lstrip("$")
        rows.append(
            {
                "symbol": symbol,
                # "Aug-17-26" -> builder-compatible exact Eastern display.
                "published_at": _normalize_ratings_date(date_text, warn),
                "status": cells[1].text_content().strip() or None,
                "rating": cells[3].text_content().strip() or None,
                "analyst": cells[2].text_content().strip() or None,
                "price_target": target_text or None,
            }
        )
    return rows


def _normalize_ratings_date(text: str, warn: Callable[[str, str], None]) -> str | None:
    if match := re.match(r"([A-Z][a-z]{2})-(\d{1,2})-(\d{2})$", text):
        month_name, day, year = match.groups()
        return f"20{int(year):02d}-{_MONTHS[month_name]:02d}-{int(day):02d} 00:00"
    warn("conversion_failed", f"cannot parse ratings date {text!r}")
    return None


def _parse_news(
    document: Any,
    symbol: str,
    response_date: dt.date | None,
) -> list[dict[str, Any]] | None:
    tables = document.xpath(".//table[@id='news-table']")
    if not tables:
        return None
    rows: list[dict[str, Any]] = []
    for tr in tables[0].xpath(".//tr"):
        cells = tr.xpath("./td")
        if len(cells) < 2:
            continue
        anchors = cells[1].xpath(f".//a[{_CLASS.format('tab-link-news')}]")
        if not anchors:
            continue
        when = cells[0].text_content().strip()
        publishers = cells[1].xpath(f".//div[{_CLASS.format('news-link-right')}]/span")
        rows.append(
            {
                "symbol": symbol,
                # Relative/time-only displays are normalized into
                # builder-anchorable (or exact) shapes; the builder anchors
                # them to the response date and statuses the parse.
                "published_at": _normalize_news_time(when, response_date),
                "title": anchors[0].text_content().strip(),
                "url": anchors[0].get("href") or "",
                "publisher": publishers[0].text_content().strip().strip("()")
                if publishers
                else None,
            }
        )
    return rows


def _normalize_news_time(text: str, response_date: dt.date | None) -> str | None:
    """Provider news displays -> builder-compatible timestamp displays.

    ``Today 05:25AM`` / ``09:00AM`` keep a time-only ``HH:MM`` display so the
    builder anchors them to the response date (status ``anchored``).
    ``Aug-27-26 04:15PM`` and ``Yesterday 11:30PM`` become exact ISO Eastern
    datetimes. ``Yesterday`` needs ``response_date``; without one the raw
    display passes through and the builder reports the conversion as failed.
    """
    if match := re.match(r"([A-Z][a-z]{2})-(\d{1,2})-(\d{2}) (\d{1,2}):(\d{2})(AM|PM)$", text):
        month_name, day, year, hour, minute, meridiem = match.groups()
        hour = int(hour) % 12 + (12 if meridiem == "PM" else 0)
        return f"20{year}-{_MONTHS[month_name]:02d}-{int(day):02d} {hour:02d}:{minute}"
    day_offset = 0
    lowered = text.lower()
    if lowered.startswith("yesterday"):
        day_offset = -1
        text = text[len("yesterday") :].strip()
    elif lowered.startswith("today"):
        text = text[len("today") :].strip()
    if match := re.match(r"(\d{1,2}):(\d{2})(AM|PM)$", text.strip()):
        hour, minute, meridiem = match.groups()
        hour = int(hour) % 12 + (12 if meridiem == "PM" else 0)
        if day_offset == 0:
            # Time-only: the builder anchors to the response date itself.
            return f"{hour:02d}:{minute}"
        if response_date is None:
            return text.strip()
        anchor = response_date + dt.timedelta(days=day_offset)
        return f"{anchor.isoformat()} {hour:02d}:{minute}"
    return text or None


def _parse_insider(
    document: Any,
    symbol: str,
    warn: Callable[[str, str], None],
) -> list[dict[str, Any]] | None:
    target = None
    for table in document.xpath(f".//table[{_CLASS.format('body-table')}]"):
        heads = [th.text_content().strip() for th in table.xpath(".//th")]
        if "SEC Form 4" in heads:
            target = table
            break
    if target is None:
        return None
    heads = [th.text_content().strip() for th in target.xpath(".//th")]
    column = {name: position for position, name in enumerate(heads)}

    def cell(cells: list[Any], name: str) -> str | None:
        position = column.get(name)
        if position is None or position >= len(cells):
            return None
        return cells[position].text_content().strip() or None

    rows: list[dict[str, Any]] = []
    for tr in target.xpath(".//tr"):
        cells = tr.xpath("./td")
        if len(cells) < len(heads):
            continue
        owner_cell = cells[column["Insider Trading"]] if "Insider Trading" in column else None
        sec_url = None
        sec_position = column.get("SEC Form 4")
        if sec_position is not None and sec_position < len(cells):
            links = cells[sec_position].xpath(".//a")
            if links:
                sec_url = links[0].get("href")
        date_text = cell(cells, "Date") or ""
        transaction_date = None
        if match := re.match(r"([A-Z][a-z]{2}) (\d{1,2}) '(\d{2})$", date_text):
            month_name, day, year = match.groups()
            transaction_date = dt.date(2000 + int(year), _MONTHS[month_name], int(day))
        elif date_text:
            warn("conversion_failed", f"cannot parse insider date {date_text!r}")
        rows.append(
            {
                "symbol": symbol,
                "owner": owner_cell.text_content().strip() if owner_cell is not None else None,
                "relationship": cell(cells, "Relationship"),
                "transaction_date": (transaction_date.isoformat() if transaction_date else None),
                "transaction_type": cell(cells, "Transaction"),
                "cost": cell(cells, "Cost"),
                "shares": cell(cells, "#Shares"),
                "value": cell(cells, "Value ($)"),
                "shares_total": cell(cells, "#Shares Total"),
                "sec_form_url": sec_url,
            }
        )
    return rows


def _parse_ticker_links(
    document: Any,
    link_text: str,
) -> list[tuple[str, str | None, str | None]] | None:
    """Peers / ETF-holder spans introduced by a link labelled ``link_text``."""
    for anchor in document.xpath(f".//a[{_CLASS.format('tab-link')}]"):
        if anchor.text_content().strip() != link_text:
            continue
        container = anchor.getparent()
        if container is None:
            continue
        entries: list[tuple[str, str | None, str | None]] = []
        for span in container.xpath(".//span[@data-boxover-ticker]"):
            ticker = span.get("data-boxover-ticker") or ""
            if not ticker:
                continue
            value_text = span.get("data-boxover-value")
            numeric = None if not value_text or value_text.startswith("AUM") else value_text
            entries.append((ticker, numeric, value_text))
        return entries
    return None


def _parse_signals(document: Any, symbol: str) -> list[dict[str, Any]] | None:
    # The correlation-links container is the region structure; it stays on the
    # page even when a ticker currently has no screener signal links.
    if not document.xpath(f".//td[{_CLASS.format('js-quote-correlation-links-container')}]"):
        return None
    return [
        {
            "symbol": symbol,
            "signal": anchor.text_content().strip(),
            "url": anchor.get("href") or "",
        }
        for anchor in document.xpath(f".//a[{_CLASS.format('sig-tab-link')}]")
        if "screener" in (anchor.get("href") or "")
    ]


def _parse_charts(document: Any, symbol: str, fetched_at: dt.datetime) -> tuple[Artifact, ...]:
    artifacts: list[Artifact] = []
    for image in document.xpath(".//img[contains(@src, 'chart.ashx')]"):
        src = image.get("src") or ""
        if not src:
            continue
        timeframe = "d"
        if match := re.search(r"[?&]p=([a-z]+)", src):
            timeframe = match.group(1)
        artifacts.append(
            Artifact(
                source_url=src,
                kind="chart",
                media_type="image/png",
                fetched_at=fetched_at,
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    return tuple(artifacts)


def _fingerprint(document: Any) -> str:
    """SHA-256 over the ordered region skeleton (tags + classes), no values."""
    parts = [
        f"{element.tag}.{element.get('class') or ''}"
        for element in document.iter()
        if isinstance(element.tag, str)
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
