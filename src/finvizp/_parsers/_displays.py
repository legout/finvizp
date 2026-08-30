"""Unit helpers for screener display conversion.

Compact suffixes (T/B/M/K) and comma-thousands are provider displays; the
conversions mirror ``finvizp.arrow`` semantics (exact integer arithmetic for
counts, one correctly-rounded float for compact/percent).
"""

from __future__ import annotations

import re

_SUFFIX_EXPONENT = {"T": 12, "B": 9, "M": 6, "K": 3}
_COMPACT = re.compile(r"^[+-]?\d+(?:\.\d+)?[TBMK]$", re.IGNORECASE)


def parse_compact(text: str) -> float:
    """``1.20B`` -> 1.2e9; plain numbers pass through."""
    cleaned = text.replace(",", "")
    if not _COMPACT.match(cleaned):
        return float(cleaned)
    mantissa = cleaned[:-1]
    exponent = _SUFFIX_EXPONENT[cleaned[-1].upper()]
    return float(f"{mantissa}e{exponent}")


def parse_int(text: str) -> int:
    """``123,456`` -> 123456."""
    return int(text.replace(",", ""))


def parse_percent(text: str) -> float:
    """``-2.44%`` -> -0.0244 (decimal fraction)."""
    return float(text.rstrip("%").replace(",", "")) / 100.0
