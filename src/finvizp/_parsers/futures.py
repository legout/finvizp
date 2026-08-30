"""Pure current-futures parser: embedded tile script -> source-near data.

Transport-free per the foundation parser contract (direct ``re`` + ``json``,
no lxml needed — the verified representation is the page's one inline
futures script, and drift detection needs no DOM). No network, client, or
cache imports.

Representation evidence (2026-08-30 bounded live probe of ``/futures``, the
301 target of ``/futures.ashx``): the page renders one inline script
carrying ``var groups = [...]`` (category groups whose ``contracts`` join
the tiles by ticker) and ``var tiles = {...}`` (one current tile per
contract). Verified tile field set: ``label``, ``ticker``, ``last``,
``change``, ``changeUsd``, ``prevClose``, ``high``, ``low``, ``sparkline``,
``sparklineDateChanges``. The page has no ``<table>`` element at all — the
legacy ``finvizfinance`` performance-table target returns nothing — so there
is no fallback parser and the empty-table assumption is not preserved. No
chart/image artifact references exist on the page.

Temporal honesty: every ``sparkline`` array was empty and every
``sparklineDateChanges`` empty in the probe, and the payload carries no
provider timestamps. The sparkline payload is handed over verbatim as text
evidence — never typed, dated, or relabeled as provider history.
``fetched_at`` is provenance, never an as-of claim; the page's own footer
delay statement ("Futures and options delayed by 15 minutes") is preserved
as the only delay fact.

Unknown tile fields land in ``extra_fields`` with ``unknown_field`` drift
warnings; a missing/non-object ``var tiles`` payload is structural drift
(:class:`FinvizParseError`); an empty tiles object is the recognized empty
state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from finvizp.errors import FetchWarning, FinvizParseError

__all__ = [
    "FuturesPage",
    "parse_futures_page",
]

_GROUPS = re.compile(r"var groups = (\[.*?\]);", re.S)
_TILES = re.compile(r"var tiles = (\{.*?\});", re.S)
_DELAY = re.compile(r"Futures and options delayed by (\d+) minute")
# camelCase -> snake_case tile field spellings (verified 2026-08-30 set).
_TILE_FIELD_ALIASES = {
    "label": "label",
    "ticker": "ticker",
    "last": "last",
    "change": "change",
    "changeUsd": "change_usd",
    "prevClose": "prev_close",
    "high": "high",
    "low": "low",
    "sparkline": "sparkline",
    "sparklineDateChanges": "sparkline_date_changes",
}
WarningCallback = Callable[[FetchWarning], Any]


@dataclass(frozen=True, slots=True)
class FuturesPage:
    """One parsed futures page: current tiles plus the group join metadata.

    ``tiles`` maps ticker -> the tile's source-near fields (snake_case,
    provider values verbatim; ``sparkline`` serialized back to compact JSON
    text); ``raw_tiles`` keeps the provider's own camelCase spellings for raw
    provenance; ``extra_fields`` collects unknown tile fields per ticker with
    drift warnings; ``groups``/``category_of`` map ticker -> category label
    exactly as the page's own join describes it.
    """

    tiles: dict[str, dict[str, Any]]
    raw_tiles: dict[str, dict[str, Any]]
    groups: dict[str, str]
    category_of: dict[str, str]
    delay_minutes: float | None
    extra_fields: dict[str, dict[str, str]] = field(default_factory=dict)
    fingerprint: str = ""


def parse_futures_page(
    html: str, *, fetched_at: Any, on_warning: WarningCallback | None = None
) -> FuturesPage:
    """Parse one current-futures page into source-near tile mappings."""
    tiles_match = _TILES.search(html)
    if tiles_match is None:
        msg = "futures page has no tiles payload"
        raise FinvizParseError(msg, context={"endpoint": "futures"})
    try:
        raw_tiles = json.loads(tiles_match.group(1))
    except (json.JSONDecodeError, ValueError) as exc:
        msg = f"futures tiles payload is not valid JSON: {exc}"
        raise FinvizParseError(msg, context={"endpoint": "futures"}) from exc
    if not isinstance(raw_tiles, dict):
        msg = f"futures tiles payload must be an object, got {type(raw_tiles).__name__}"
        raise FinvizParseError(msg, context={"endpoint": "futures"})

    groups_payload = _groups_of(html)
    warnings: list[FetchWarning] = []

    def warn(message: str, *, symbol: str | None = None) -> None:
        warning = FetchWarning(
            code="unknown_field", message=message, symbol=symbol, endpoint="futures"
        )
        warnings.append(warning)
        if on_warning is not None:
            on_warning(warning)

    tiles: dict[str, dict[str, Any]] = {}
    raw_kept: dict[str, dict[str, Any]] = {}
    extra: dict[str, dict[str, str]] = {}
    for ticker, payload in raw_tiles.items():
        if not isinstance(payload, dict):
            warn(f"futures tile {ticker!r} is {type(payload).__name__}, skipped", symbol=ticker)
            continue
        known: dict[str, Any] = {}
        unknown: dict[str, Any] = {}
        for key, value in payload.items():
            if key in _TILE_FIELD_ALIASES:
                known[_TILE_FIELD_ALIASES[key]] = value
            else:
                unknown[key] = value
        for key in sorted(unknown):
            warn(f"unknown field {key!r} on futures tile {ticker!r}", symbol=ticker)
            extra.setdefault(str(ticker), {})[key] = (
                "" if unknown[key] is None else str(unknown[key])
            )
        # Sparkline payload evidence only: verbatim compact JSON text, never
        # interpreted, never dated — the provider sends no timestamps.
        known["sparkline"] = json.dumps(known.get("sparkline", []), separators=(",", ":"))
        known["sparkline_date_changes"] = known.get("sparkline_date_changes", {})
        tiles[str(ticker)] = known
        raw_kept[str(ticker)] = dict(payload)

    groups: dict[str, str] = {}
    for group_label, contract_tickers in groups_payload:
        for contract_ticker in contract_tickers:
            groups.setdefault(contract_ticker, group_label)

    delay_match = _DELAY.search(html)
    return FuturesPage(
        tiles=tiles,
        raw_tiles=raw_kept,
        groups=groups,
        category_of=dict(groups),
        delay_minutes=float(delay_match.group(1)) if delay_match else None,
        extra_fields=extra,
        fingerprint=_fingerprint(raw_tiles, groups_payload),
    )


def _groups_of(html: str) -> list[tuple[str, list[str]]]:
    """Category label + contract tickers, exactly as the page's join states."""
    match = _GROUPS.search(html)
    if match is None:
        return []
    try:
        payload = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    groups: list[tuple[str, list[str]]] = []
    for group in payload:
        if not isinstance(group, dict):
            continue
        label = group.get("label")
        contracts = group.get("contracts")
        if not isinstance(label, str) or not isinstance(contracts, list):
            continue
        tickers: list[str] = [
            contract["ticker"]
            for contract in contracts
            if isinstance(contract, dict) and isinstance(contract.get("ticker"), str)
        ]
        groups.append((label, tickers))
    return groups


def _fingerprint(tiles: dict[str, Any], groups: list[tuple[str, list[str]]]) -> str:
    """SHA-256 over the payload's ordered key skeleton, no values."""
    import hashlib

    parts: list[str] = []
    for ticker, payload in tiles.items():
        parts.append(f"t:{ticker}")
        if isinstance(payload, dict):
            parts.extend(f"o:{key}" for key in payload)
    for label, tickers in groups:
        parts.append(f"g:{label}")
        parts.extend(f"c:{ticker}" for ticker in tickers)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
