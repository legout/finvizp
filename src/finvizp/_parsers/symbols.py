"""Transport-free parsers for the stock sitemap manifest and JSON suggestions.

Pure lxml per the foundation parser contract (NFR-4); the XML boundary is
network/entity/DTD-safe.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from lxml.html import (
    etree,
)

from finvizp.errors import FetchWarning, FinvizParseError

__all__ = ["parse_sitemap", "parse_suggestions"]

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_LOC_TAG = _SITEMAP_NS + "loc"
# Standard sitemap-protocol optional children of <url>; never data here, but
# their presence is not structure drift.
_SITEMAP_OPTIONAL_TAGS = frozenset(
    _SITEMAP_NS + name for name in ("lastmod", "changefreq", "priority")
)
_STOCK_PATH = "/stock"
_CANONICAL_HOST = "finviz.com"
# Raw source-value gate: unpadded ASCII alnum/dash only, before any
# uppercasing. Rejects percent-decodable shapes, +/-/whitespace padding, and
# non-ASCII homoglyphs that str.upper() would fold into ASCII lookalikes.
_VALID_SOURCE_SYMBOL = re.compile(r"[A-Za-z0-9-]+\Z")
_RESERVED_SUGGESTION_FIELDS = frozenset(
    {"symbol", "extra_fields", "fetched_at", "market_cap_raw", "div_yield_raw"}
)
_XML_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    dtd_validation=False,
    load_dtd=False,
    recover=False,
    huge_tree=False,
)


def _canonical_symbol(loc: str) -> str | None:
    """Canonical symbol from one canonical sitemap ``<loc>`` URL, or ``None``.

    Only the canonical ``https://finviz.com/stock?t=...`` shape carries a
    symbol; the ``ty=oc`` variant resolves to the same symbol and dedupes —
    it is never optionability evidence. Anything else (foreign hosts, other
    schemes/ports/userinfo, near-match paths, fragments, unknown or duplicate
    query fields, other ``ty`` variants, malformed URL components, or a
    malformed (non-``[A-Za-z0-9-]+``) source symbol) is not a symbol.
    """
    try:
        parts = urlsplit(loc)
        port = parts.port  # may raise ValueError for malformed/oversized ports
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or parts.hostname != _CANONICAL_HOST
        or port is not None
        or parts.netloc.endswith(":")
        or parts.username is not None
        or parts.password is not None
        or parts.path != _STOCK_PATH
        or parts.fragment
    ):
        return None
    # Raw-query gate: the source value is validated BEFORE percent-decoding,
    # so percent-escaped tickers (%41APL, BRK%2DB, %C5%BF) never normalize.
    # The `t` token must already be an unpadded ASCII [A-Za-z0-9-]+ value;
    # any encoded form is an unexpected URL, not a symbol.
    raw_query = parts.query
    t_raw: str | None = None
    for field in raw_query.split("&") if raw_query else ():
        key, sep, value = field.partition("=")
        if key == "t":
            if t_raw is not None or not sep or not _VALID_SOURCE_SYMBOL.fullmatch(value):
                return None
            t_raw = value
    if t_raw is None:
        return None
    try:
        pairs = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    fields: dict[str, list[str]] = {}
    for key, value in pairs:
        fields.setdefault(key, []).append(value)
    if set(fields) - {"t", "ty"}:
        return None
    if len(fields.get("t", [])) != 1:
        return None
    ty_values = fields.get("ty", [])
    if ty_values not in ([], ["oc"]):
        return None
    return t_raw.upper()


def parse_sitemap(xml_text: str) -> tuple[list[str], list[FetchWarning]]:
    """Parse the stock sitemap manifest into unique canonical symbols.

    Deterministic first-manifest order, ``ty=oc`` duplicates removed, no
    optionability inference. Unexpected URL shapes are skipped with
    ``unexpected_url`` warnings; a recognized empty manifest is empty, not
    drift. XML comments and processing instructions are ignored; unknown
    element children (at any level) are structure drift. Malformed/unsafe
    XML or a non-urlset root raises :class:`FinvizParseError`.
    """
    try:
        root = etree.fromstring(xml_text.encode("utf-8"), _XML_PARSER)
    except (etree.XMLSyntaxError, ValueError) as exc:
        msg = f"malformed or unsafe sitemap XML: {exc}"
        raise FinvizParseError(msg) from None
    if root.getroottree().docinfo.doctype:
        # DTDs/entity definitions are never part of a sitemap and are refused
        # at the upstream boundary rather than resolved.
        msg = "sitemap XML must not declare a DOCTYPE"
        raise FinvizParseError(msg)
    if root.tag != _SITEMAP_NS + "urlset":
        msg = f"unexpected sitemap root {root.tag!r}"
        raise FinvizParseError(msg)

    symbols: list[str] = []
    seen: set[str] = set()
    warnings: list[FetchWarning] = []

    def _elements(node: Any) -> list[Any]:
        # Comments and processing instructions are never sitemap content; only
        # real element children are validated.
        return [child for child in node if isinstance(child.tag, str)]

    for url in _elements(root):
        if url.tag != _SITEMAP_NS + "url":
            msg = f"unexpected sitemap child {url.tag!r}"
            raise FinvizParseError(msg)
        children = _elements(url)
        locs = [child for child in children if child.tag == _LOC_TAG]
        unknown = [child for child in children if child.tag not in _SITEMAP_OPTIONAL_TAGS]
        if len(locs) != 1 or len(unknown) != 1:
            # Exactly one namespaced loc plus only the protocol's standard
            # optional children (lastmod/changefreq/priority, never data):
            # unknown nested elements (widget, foreign-namespace tags, ...)
            # are structure drift, never ordinary data.
            msg = "sitemap URL entry must contain exactly one loc"
            raise FinvizParseError(msg)
        # The accepted leaf tags (loc and the optional children) carry only
        # text: any element descendant of them is structure drift too, never
        # ordinary data. Comments/PIs stay ignored (filtered by _elements).
        if any(_elements(child) for child in children):
            msg = f"unexpected sitemap element nested inside {locs[0].tag!r} siblings"
            raise FinvizParseError(msg)
        # Comments/PIs are ignored, not content: the loc value is the
        # concatenation of the direct text nodes (loc.text would stop at the
        # first comment/PI child and truncate or erase the URL).
        text = "".join(locs[0].xpath("text()")).strip()
        symbol = _canonical_symbol(text)
        if symbol is None:
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
    verbatim and maps ``ticker`` to the canonical ``symbol`` field. A ticker
    must be an unpadded ASCII ``[A-Za-z0-9-]+`` source value, and company and
    exchange must be present strings — anything else is provider drift, never
    null-coerced. Any other source field passes through untouched so the
    Arrow builder can preserve it in ``extra_fields`` with an
    ``unknown_field`` warning. Recognized empty (``[]``) is empty, not drift;
    any other shape deviation (including JSON ``null``) raises
    :class:`FinvizParseError`.
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
        if not isinstance(ticker, str) or not _VALID_SOURCE_SYMBOL.fullmatch(ticker):
            msg = f"suggestion {position} has a malformed ticker {ticker!r}"
            raise FinvizParseError(msg)
        company = record.get("company")
        exchange = record.get("exchange")
        # Missing or non-string company/exchange is provider drift: never
        # silently converted to null.
        if not isinstance(company, str) or not isinstance(exchange, str):
            msg = f"suggestion {position} is missing company or exchange"
            raise FinvizParseError(msg)
        row: dict[str, Any] = {
            "symbol": ticker.upper(),
            "company": company,
            "exchange": exchange,
        }
        # Additive provider fields pass through verbatim: the Arrow builder
        # preserves them in ``extra_fields`` and warns ``unknown_field``.
        for key, value in record.items():
            if key in ("ticker", "company", "exchange"):
                continue
            source_key = str(key)
            if source_key in _RESERVED_SUGGESTION_FIELDS:
                source_key = f"provider_{source_key}"
            while source_key in row:
                source_key = f"provider_{source_key}"
            row[source_key] = value
        rows.append(row)
    return rows
