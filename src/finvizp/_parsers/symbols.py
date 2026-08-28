"""Transport-free parsers for the stock sitemap manifest and JSON suggestions."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from xml.etree import ElementTree

from finvizp.errors import FetchWarning, FinvizParseError

__all__ = ["parse_sitemap", "parse_suggestions"]

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_STOCK_PATH = "/stock"
_CANONICAL_HOST = "finviz.com"
_SYMBOL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")


def _symbol_from_loc(loc: str) -> str | None:
    """Canonical symbol from one canonical sitemap ``<loc>`` URL, or ``None``.

    Only the canonical ``https://finviz.com/stock?t=...`` shape carries a
    symbol; the ``ty=oc`` variant resolves to the same symbol and dedupes —
    it is never optionability evidence. Foreign hosts, near-match paths, and
    an absent or ambiguous ``t`` are not symbols.
    """
    parts = urlsplit(loc)
    if (
        parts.scheme != "https"
        or parts.hostname != _CANONICAL_HOST
        or parts.port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.path != _STOCK_PATH
    ):
        return None
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    t_values = [value for key, value in pairs if key == "t"]
    if len(t_values) != 1:
        return None
    symbol = t_values[0].strip().upper()
    if not symbol or any(ch not in _SYMBOL_CHARS for ch in symbol):
        return None
    return symbol


def parse_sitemap(xml_text: str) -> tuple[list[str], list[FetchWarning]]:
    """Parse the stock sitemap manifest into unique canonical symbols.

    Deterministic first-manifest order, ``ty=oc`` duplicates removed, no
    optionability inference. Unexpected URL shapes are skipped with
    ``unexpected_url`` warnings; a recognized empty manifest is empty, not
    drift. Malformed XML or a non-urlset root raises :class:`FinvizParseError`.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        msg = f"malformed sitemap XML: {exc}"
        raise FinvizParseError(msg) from None
    if root.tag not in (_SITEMAP_NS + "urlset", "urlset"):
        msg = f"unexpected sitemap root {root.tag!r}"
        raise FinvizParseError(msg)

    symbols: list[str] = []
    seen: set[str] = set()
    warnings: list[FetchWarning] = []
    for loc in (elem.text or "" for elem in root.iter(_SITEMAP_NS + "loc")):
        text = loc.strip()
        symbol = _symbol_from_loc(text)
        if symbol is None:
            if text:
                warnings.append(
                    FetchWarning(code="unexpected_url", message="unexpected sitemap URL shape")
                )
            continue
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols, warnings


def parse_suggestions(payload: Any) -> list[dict[str, Any]]:
    """Parse one bounded ranked ``/api/suggestions`` JSON body.

    Accepts the already-decoded JSON value (the classified client envelope
    hands parsed JSON to parsers) or the raw text. Preserves provider ranking
    verbatim and maps ``ticker`` to the canonical ``symbol`` field.
    Recognized empty (``[]``) is empty, not drift; any other shape deviation
    (including JSON ``null``) raises :class:`FinvizParseError`.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            msg = f"malformed suggestions JSON: {exc}"
            raise FinvizParseError(msg) from None
    if not isinstance(payload, list):
        msg = f"suggestions payload must be a JSON array, got {type(payload).__name__}"
        raise FinvizParseError(msg)
    rows: list[dict[str, Any]] = []
    for position, record in enumerate(payload):
        if not isinstance(record, dict):
            msg = f"suggestion {position} must be a JSON object, got {type(record).__name__}"
            raise FinvizParseError(msg)
        ticker = record.get("ticker")
        if not isinstance(ticker, str) or not ticker.strip():
            msg = f"suggestion {position} is missing a ticker"
            raise FinvizParseError(msg)
        company = record.get("company")
        exchange = record.get("exchange")
        rows.append(
            {
                "symbol": ticker.strip().upper(),
                "company": company if isinstance(company, str) else None,
                "exchange": exchange if isinstance(exchange, str) else None,
            }
        )
    return rows
