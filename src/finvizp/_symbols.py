"""Canonical symbol normalization: trim, uppercase, class-share dash mapping."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from finvizp.results import SymbolResolutionRecord

__all__ = ["SymbolInputError", "normalize_symbols"]

# Finviz dash notation plus reviewed dot/slash class-share forms.
_ALLOWED = re.compile(r"^[A-Z0-9]+(?:[-./][A-Z0-9]+)*$")


class SymbolInputError(ValueError):
    """Raised for blank or punctuation-bearing symbol input."""


def _iter_requested(symbols: str | Iterable[str] | None) -> Iterator[str]:
    if symbols is None:
        return
    if isinstance(symbols, str):
        yield symbols
        return
    yield from symbols


def _resolve(
    symbols: str | Iterable[str] | None,
) -> tuple[list[str], list[SymbolResolutionRecord]]:
    """Validate and map every requested symbol; canonical list is deduped in order."""
    if symbols is None:
        msg = "symbols: at least one symbol is required"
        raise SymbolInputError(msg)

    canonical: list[str] = []
    seen: set[str] = set()
    records: list[SymbolResolutionRecord] = []

    for position, requested in enumerate(_iter_requested(symbols)):
        if not isinstance(requested, str):
            msg = f"symbols[{position}]: symbol must be a string, got {type(requested).__name__}"
            raise SymbolInputError(msg)
        symbol = requested.strip().upper()
        if not symbol:
            msg = f"symbols[{position}]: blank symbol input is not allowed"
            raise SymbolInputError(msg)
        if not _ALLOWED.fullmatch(symbol):
            msg = (
                f"symbols[{position}]: unsupported symbol notation {requested!r}; "
                "class shares use dot or slash (brk.b, BRK/B); "
                "unknown punctuation is rejected"
            )
            raise SymbolInputError(msg)
        mapped = symbol.replace(".", "-").replace("/", "-")
        records.append(
            SymbolResolutionRecord(position=position, requested=requested, canonical=mapped)
        )
        if mapped not in seen:
            seen.add(mapped)
            canonical.append(mapped)

    if not canonical:
        msg = "symbols: at least one symbol is required"
        raise SymbolInputError(msg)
    return canonical, records


def normalize_symbols(
    symbols: str | Iterable[str] | None,
    *,
    resolve: bool = False,
) -> list[str] | list[SymbolResolutionRecord]:
    """Normalize requested symbols to canonical Finviz dash notation.

    Trims and uppercases input, maps reviewed class-share notation
    (``brk.b``/``BRK/B``) to dashes, rejects blank/unknown input, dedupes by
    canonical symbol in first-occurrence order, and preserves every requested
    position/spelling. With ``resolve=True`` returns one immutable
    :class:`SymbolResolutionRecord` per input position.
    """
    canonical, records = _resolve(symbols)
    return records if resolve else canonical
