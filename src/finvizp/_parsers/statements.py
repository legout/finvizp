"""Pure public statement-JSON parser: one payload -> source-near records.

No network, client, cache, or pyarrow imports (plan card 0.1-F step 2). The
verified 2026-08 ``/api/statement`` JSON shape is:

``{"currency": "USD", "data": {"Period": [...], "Period End Date": [...],
"Period Length": [...], "<Metric>": [...], ...}}`` — one value array per metric,
all aligned with the Period array. ``{"error": "no data"}`` is the provider's
recognized no-results state. Values stay verbatim (commas, signs, blanks); the
registry-driven Arrow builder (``finvizp.arrow``) owns typed conversion,
``value_raw`` companions, and null handling.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from finvizp.errors import FinvizParseError

__all__ = ["StatementRecords", "parse_statement_json"]

# Period Length maps to day counts via the parsed period ends; the display text
# itself is never trusted for the typed value (provider spellings vary).
_PERIOD_KEYS = ("Period", "Period End Date")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


@dataclass(frozen=True, slots=True)
class StatementRecords:
    """Source-near parse output: long metric/period rows plus fingerprint.

    ``rows`` are builder-ready mappings (verbatim string values); ``rows == []``
    with ``empty_recognized=True`` is the provider's positive no-data state,
    while a missing/malformed payload raises ``FinvizParseError`` instead.
    """

    symbol: str
    statement: str
    periodicity: str
    currency: str | None
    rows: tuple[dict[str, Any], ...]
    fingerprint: str
    empty_recognized: bool


def _fail(message: str) -> FinvizParseError:
    return FinvizParseError(message, context={"endpoint": "statements"})


def _parse_period_end(text: str) -> dt.date | None:
    """US provider display ``M/D/YYYY`` -> date; blank stays None."""
    if not text.strip():
        return None
    if match := _US_DATE.match(text.strip()):
        month, day, year = (int(part) for part in match.groups())
        try:
            return dt.date(year, month, day)
        except ValueError as exc:
            raise _fail(f"invalid Period End Date {text!r}: {exc}") from exc
    raise _fail(f"unrecognized Period End Date display {text!r}")


def parse_statement_json(
    payload: Any,
    *,
    symbol: str,
    statement: str,
    fetched_at: dt.datetime,
) -> StatementRecords:
    """Parse one classified statement JSON payload into long records.

    ``statement`` is the reviewed provider code (``IA``/``IQ``/``BA``/``BQ``/
    ``CA``/``CQ``); periodicity is derived from it (``*Q`` quarterly).
    """
    if not isinstance(payload, dict):
        raise _fail(f"statement payload must be a JSON object, got {type(payload).__name__}")
    if "error" in payload:
        # Only the exact verified no-results envelope is a recognized EMPTY
        # state; every other error payload is unverified provider surface and
        # must fail typed instead of masquerading as an empty result.
        if len(payload) == 1 and payload["error"] == "no data":
            return StatementRecords(
                symbol=symbol,
                statement=statement,
                periodicity="quarterly" if statement.endswith("Q") else "annual",
                currency=None,
                rows=(),
                fingerprint=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
                empty_recognized=True,
            )
        # Fixed message: the provider's error value is untrusted raw content
        # and must never surface through public error carriers (spec 0.1).
        raise _fail("unrecognized statement error envelope")
    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        raise _fail("statement payload has no statement data object")
    currency = payload.get("currency")
    if not isinstance(currency, str) or not currency:
        raise _fail("statement payload has no currency")
    for key in _PERIOD_KEYS:
        periods = data.get(key)
        if not isinstance(periods, list) or not periods:
            raise _fail(f"statement data has no {key!r} array")
    length_row = data.get("Period Length")
    if length_row is not None and not isinstance(length_row, list):
        raise _fail("statement data 'Period Length' must be an array when present")

    labels = [str(label) for label in data["Period"]]
    # Structural alignment: every period-shaped array must have exactly one
    # entry per Period position. A short array would raise an untyped
    # IndexError below; a surplus one would be silently dropped. Both are
    # typed parse drift, never data loss.
    for key in (*_PERIOD_KEYS, "Period Length"):
        row = data.get(key)
        if row is None:
            continue  # Period Length is optional
        if len(row) != len(labels):
            raise _fail(
                f"statement data {key!r} array is misaligned with Period "
                f"({len(row)} entries for {len(labels)} periods)"
            )
    ends = [_parse_period_end(str(end)) for end in data["Period End Date"]]
    # Day lengths come from consecutive parsed ends (earliest has no neighbor),
    # never from the display text. Blank ends yield None lengths.
    lengths: dict[int, int | None] = {}
    for position in range(len(ends) - 1):
        earlier, later = ends[position + 1], ends[position]
        lengths[position] = (
            (later - earlier).days if earlier is not None and later is not None else None
        )

    reserved = {"Period", "Period End Date", "Period Length"}
    rows: list[dict[str, Any]] = []
    metrics: list[tuple[str, list[Any]]] = []
    seen_metrics: set[str] = set()
    for key, values in data.items():
        if key in reserved:
            continue
        if not isinstance(values, list) or len(values) != len(labels):
            count = len(values) if isinstance(values, list) else "not a list"
            raise _fail(f"metric array {key!r} is misaligned with Period ({count} values)")
        if key in seen_metrics:
            raise _fail(f"duplicate metric {key!r}")
        seen_metrics.add(key)
        metrics.append((key, values))
    if not metrics:
        raise _fail("statement data has no statement data rows")

    for metric, values in metrics:
        for position, label in enumerate(labels):
            value = values[position]
            if value is not None and not isinstance(value, str):
                raise _fail(f"metric {metric!r} has a non-string value at position {position}")
            rows.append(
                {
                    "symbol": symbol,
                    "period_label": label,
                    "period_end_date": ends[position],
                    "period_length_days": lengths.get(position),
                    "metric": metric,
                    "value": value if value is not None else None,
                    "currency": currency,
                }
            )

    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return StatementRecords(
        symbol=symbol,
        statement=statement,
        periodicity="quarterly" if statement.endswith("Q") else "annual",
        currency=currency,
        rows=tuple(rows),
        fingerprint=fingerprint,
        empty_recognized=False,
    )
