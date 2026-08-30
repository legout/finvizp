"""Pure forex/crypto markets parsers and shared projections.

Parsing is direct lxml + json only (no network, client, or cache imports).
Structure is verified against the 2026-08-30 public pages (see
``tests/fixtures/markets/representation-evidence.md``):

- the performance pages carry exactly one ``table.groups_table`` whose
  ``thead`` names the columns (forex: ``No.``/``Pair``/``Price`` plus the
  perf columns; crypto adds ``Ticker`` and ``Name``). A populated table with
  zero data rows is the recognized empty state; anything else — no or
  multiple groups_table, unexpected headers, row/column arity mismatch — is
  structural drift and raises :class:`FinvizParseError`;
- the ``/forex``/``/crypto`` pages embed one first-party JSON tile payload in
  an inline script hooking ``Finviz:FinvizInitForex``/``FinvizInitCrypto``.
  Tiles are carried honestly: the sparkline array is preserved verbatim (the
  provider sends no per-point timestamps, so no history is ever inferred),
  percent ``change`` values become decimal fractions, and unknown tile
  fields land in ``extra_fields``;
- the charts galleries embed ``img.charts-gal`` elements whose srcset names
  the provider's cross-origin ``charts2-node.finviz.com`` image host;
  :func:`chart_descriptor` resolves one symbol's URL verbatim from the page
  into an :class:`~finvizp.models.Artifact` — never constructed, never
  fetched here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pyarrow as pa
from lxml import html as lxml_html

from finvizp.errors import FinvizParseError, FinvizQueryError
from finvizp.models import Artifact
from finvizp.results import AccessTier

__all__ = [
    "MarketPerformancePage",
    "MarketPerformanceRow",
    "TileBundle",
    "TileRow",
    "chart_descriptor",
    "parse_market_performance",
    "parse_market_tiles",
    "performance_table",
]

_CLASS = "contains(concat(' ', normalize-space(@class), ' '), ' {} ')"
_TABLE_XPATH = f".//table[{_CLASS.format('groups_table')}]"

_TILE_EVENT_RE = re.compile(r"window\.addEventListener\('Finviz:(\w+)', \(\) => window\.\w+\(")

# Verified perf header labels -> semantic field names, in page order.
PERF_FIELD_BY_LABEL: dict[str, str] = {
    "Perf 5Min": "perf_5min",
    "Perf Hour": "perf_hour",
    "Perf Day": "perf_day",
    "Perf Week": "perf_week",
    "Perf Month": "perf_month",
    "Perf Quart": "perf_quart",
    "Perf Half": "perf_half",
    "Perf YTD": "perf_ytd",
    "Perf Year": "perf_year",
}
_IDENTITY_FIELD_BY_LABEL = {"Pair": "pair", "Ticker": "ticker"}


def family_of(tile_event: str) -> str:
    """Map the tile hook name to its family label for drift context."""
    return "crypto" if "Crypto" in tile_event else "forex"


@dataclass(frozen=True, slots=True)
class MarketPerformanceRow:
    """One source-near performance row: typed identity, raw displays."""

    index: int
    identity: str
    name: str | None
    raw: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketPerformancePage:
    """One parsed performance page: columns, rows, recognized emptiness."""

    columns: tuple[str, ...]
    rows: tuple[MarketPerformanceRow, ...]
    is_empty: bool


def parse_market_performance(html: str, *, family: str) -> MarketPerformancePage:
    """Parse one forex/crypto performance page into typed rows."""
    document = lxml_html.fromstring(html)

    tables = document.xpath(_TABLE_XPATH)
    if len(tables) != 1:
        msg = f"{family} performance page has no single groups_table (tables: {len(tables)})"
        raise FinvizParseError(msg, context={"endpoint": family})
    table = tables[0]

    heads = [th.text_content().strip() for th in table.xpath(".//thead//th")]
    if not heads or any(not head for head in heads):
        msg = f"{family} performance table has no readable header row"
        raise FinvizParseError(msg, context={"endpoint": family})
    if heads[0] != "No." or heads[1] not in _IDENTITY_FIELD_BY_LABEL:
        msg = f"{family} performance table header must start with 'No.', 'Pair'/'Ticker'"
        raise FinvizParseError(msg, context={"endpoint": family, "columns": heads})

    has_name = "Ticker" in heads and "Name" in heads
    rows: list[MarketPerformanceRow] = []
    for tr in table.xpath("./tr"):
        cells = tr.xpath("./td")
        if not cells:
            continue
        if len(cells) != len(heads):
            msg = f"{family} performance row has {len(cells)} cells for {len(heads)} columns"
            raise FinvizParseError(msg, context={"endpoint": family})
        index_text = cells[0].text_content().strip()
        if not index_text.isdigit():
            msg = f"{family} performance row index is not a number"
            raise FinvizParseError(msg, context={"endpoint": family})
        identity = cells[1].text_content().strip()
        if not identity:
            msg = f"{family} performance row has an empty identity cell"
            raise FinvizParseError(msg, context={"endpoint": family})
        name: str | None = None
        value_cells = cells[2:]
        if has_name:
            name = cells[2].text_content().strip()
            value_cells = cells[3:]
        rows.append(
            MarketPerformanceRow(
                index=int(index_text),
                identity=identity,
                name=name,
                raw=tuple(cell.text_content().strip() for cell in value_cells),
            )
        )

    return MarketPerformancePage(columns=tuple(heads), rows=tuple(rows), is_empty=not rows)


def performance_table(page: MarketPerformancePage, fetched_at: datetime, *, family: str) -> pa.Table:
    """Project one parsed performance page into a deterministic wide table.

    Percent displays become decimal fractions; PIPS-view displays (plain
    numbers) pass through as pips; every converted column keeps its ``_raw``
    display companion. Unknown provider headers are drift, never silently
    dropped data.
    """
    typed: list[tuple[str, str]] = []  # (label, field name), page order
    for label in page.columns[2:]:
        if label == "Name":
            continue
        field = "price" if label == "Price" else PERF_FIELD_BY_LABEL.get(label)
        if field is None:
            msg = f"{family} performance page has unexpected column {label!r}"
            raise FinvizParseError(msg, context={"endpoint": family, "columns": page.columns})
        typed.append((label, field))

    identity = _IDENTITY_FIELD_BY_LABEL[page.columns[1]]
    has_name = "Name" in page.columns
    fields = [
        pa.field("rank", pa.int64(), nullable=False),
        pa.field(identity, pa.string(), nullable=False),
    ]
    if has_name:
        fields.append(pa.field("name", pa.string()))
    for _, name in typed:
        fields.append(pa.field(name, pa.float64()))
    for _, name in typed:
        fields.append(pa.field(f"{name}_raw", pa.string()))
    fields.append(pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False))
    fields.append(pa.field("extra_fields", pa.map_(pa.string(), pa.string()), nullable=True))
    schema = pa.schema(fields)

    ranks: list[int] = []
    identities: list[str] = []
    names: list[str | None] = []
    values: dict[str, list[Any]] = {name: [] for _, name in typed}
    raws: dict[str, list[str]] = {f"{name}_raw": [] for _, name in typed}
    for row in page.rows:
        ranks.append(row.index)
        identities.append(row.identity)
        names.append(row.name)
        displays = iter(row.raw)
        for label, name in typed:
            display = next(displays, None)
            if display is None:
                msg = f"{family} performance row {row.index} has no display for {label!r}"
                raise FinvizParseError(msg, context={"endpoint": family})
            raws[f"{name}_raw"].append(display)
            values[name].append(None if display in {"", "-"} else _convert(label, display, family))

    arrays = [
        pa.array(ranks, type=schema.field("rank").type),
        pa.array(identities, type=schema.field(identity).type),
    ]
    if has_name:
        arrays.append(pa.array(names, type=schema.field("name").type))
    for _, name in typed:
        arrays.append(pa.array(values[name], type=schema.field(name).type))
    for _, name in typed:
        arrays.append(pa.array(raws[f"{name}_raw"], type=schema.field(f"{name}_raw").type))
    arrays.append(pa.array([fetched_at] * len(ranks), type=schema.field("fetched_at").type))
    arrays.append(pa.array([[] for _ in ranks], type=schema.field("extra_fields").type))
    return pa.Table.from_arrays(arrays, schema=schema)


def _convert(label: str, display: str, family: str) -> float:
    """Convert one display: percent fractions for %, plain floats otherwise."""
    try:
        if display.endswith("%"):
            return float(display[:-1].replace(",", "")) / 100.0
        return float(display.replace(",", ""))
    except ValueError as exc:
        msg = f"cannot convert {label!r} display {display!r} on the {family} performance page"
        raise FinvizParseError(msg, context={"endpoint": family}) from exc


@dataclass(frozen=True, slots=True)
class TileRow:
    """One market tile: typed identity, price facts, verbatim sparkline.

    ``change`` is the provider's percent value as a decimal fraction;
    ``change_usd`` is the absolute USD delta. ``sparkline`` preserves the
    provider payload verbatim — it carries no timestamps, so
    ``sparkline_timestamps`` and ``sparkline_interval_seconds`` are always
    ``None`` and no history is ever inferred. ``extra_fields`` holds unknown
    tile fields for drift review.
    """

    ticker: str
    label: str
    last: float
    change: float
    change_usd: float
    prev_close: float
    high: float
    low: float
    sparkline: list[float]
    extra_fields: dict[str, Any]
    sparkline_timestamps: None = None
    sparkline_interval_seconds: None = None


@dataclass(frozen=True, slots=True)
class TileBundle:
    """One markets page's embedded tile payload, parsed."""

    rows: tuple[TileRow, ...]
    fetched_at: datetime
    access_tier: AccessTier


def _tile_payload(html: str, *, tile_event: str) -> dict[str, dict[str, Any]]:
    """Extract the embedded tile JSON object from one markets page."""
    document = lxml_html.fromstring(html)
    family = family_of(tile_event)
    for script in document.xpath(".//script"):
        text = script.text or ""
        if tile_event not in text:
            continue
        match = _TILE_EVENT_RE.search(text)
        if match is None:
            continue
        # The regex consumes the hook name and its opening paren, so the
        # payload object starts at the first '{' from the match's end.
        start = text.index("{", match.end())
        depth = 0
        for end in range(start, len(text)):
            char = text[end]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        payload = json.loads(text[start : end + 1])
                    except json.JSONDecodeError as exc:
                        msg = f"malformed embedded {tile_event} tile JSON"
                        raise FinvizParseError(msg, context={"endpoint": family}) from exc
                    if not isinstance(payload, dict) or not all(
                        isinstance(value, dict) for value in payload.values()
                    ):
                        msg = f"embedded {tile_event} tile payload is not an object map"
                        raise FinvizParseError(msg, context={"endpoint": family})
                    return payload
        break
    msg = f"page carries no {tile_event} tile payload"
    raise FinvizParseError(msg, context={"endpoint": family})


def parse_market_tiles(html: str, *, tile_event: str) -> tuple[TileRow, ...]:
    """Parse one markets page's embedded tiles into typed rows."""
    payload = _tile_payload(html, tile_event=tile_event)
    family = family_of(tile_event)
    rows: list[TileRow] = []
    for ticker, tile in payload.items():
        known = {
            "label": tile.get("label"),
            "ticker": tile.get("ticker", ticker),
            "last": tile.get("last"),
            "change": tile.get("change"),
            "changeUsd": tile.get("changeUsd"),
            "prevClose": tile.get("prevClose"),
            "high": tile.get("high"),
            "low": tile.get("low"),
            "sparkline": tile.get("sparkline"),
        }
        missing = [key for key, value in known.items() if value is None]
        if missing:
            msg = f"tile {ticker!r} is missing required fields: {', '.join(missing)}"
            raise FinvizParseError(msg, context={"endpoint": family})
        extra = {
            key: value
            for key, value in tile.items()
            if key not in known and isinstance(value, (str, int, float, bool))
        }
        sparkline = known["sparkline"]
        if not isinstance(sparkline, list) or not all(
            isinstance(point, (int, float)) and not isinstance(point, bool) for point in sparkline
        ):
            msg = f"tile {ticker!r} sparkline is not a numeric array"
            raise FinvizParseError(msg, context={"endpoint": family})
        try:
            rows.append(
                TileRow(
                    ticker=str(known["ticker"]),
                    label=str(known["label"]),
                    last=float(known["last"]),
                    change=float(known["change"]) / 100.0,
                    change_usd=float(known["changeUsd"]),
                    prev_close=float(known["prevClose"]),
                    high=float(known["high"]),
                    low=float(known["low"]),
                    sparkline=list(float(point) for point in sparkline),
                    extra_fields=extra,
                )
            )
        except (TypeError, ValueError) as exc:
            msg = f"tile {ticker!r} carries a non-numeric value"
            raise FinvizParseError(msg, context={"endpoint": family}) from exc
    return tuple(rows)


def chart_descriptor(
    html: str,
    *,
    symbol: str,
    timeframe: str,
    fetched_at: datetime,
    family: str,
) -> Artifact:
    """Resolve one symbol's chart image descriptor from a gallery page.

    The URL is the page's own embedded ``charts2-node.finviz.com`` srcset
    entry, taken verbatim: a cross-origin image host the client never
    fetches. Requesting a symbol the gallery does not carry is a typed
    query error, not silent absence.
    """
    document = lxml_html.fromstring(html)
    urls: dict[str, str] = {}
    for image in document.xpath(".//img[contains(@class, 'charts-gal')]"):
        srcset = image.get("srcset") or ""
        candidate = srcset.split(",")[0].split()[0] if srcset else (image.get("src") or "")
        match = re.search(r"[?&]t=@([a-z0-9]+)", candidate, re.I)
        if match is None:
            msg = f"{family} chart image carries no @symbol parameter"
            raise FinvizParseError(msg, context={"endpoint": family})
        urls[match.group(1).upper()] = candidate
    source_url = urls.get(symbol.upper())
    if source_url is None:
        msg = f"no {family} chart image for {symbol!r} on the charts page"
        raise FinvizQueryError(msg)
    media_type = "image/png" if ".png" in source_url.lower() else "image"
    return Artifact(
        source_url=source_url,
        kind="chart",
        media_type=media_type,
        fetched_at=fetched_at,
        symbol=symbol.upper(),
        timeframe=timeframe,
    )
