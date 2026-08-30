"""Pure economic-calendar parser: embedded route-init JSON -> source-near rows.

Direct ``json`` + ``re`` only (no lxml needed — the verified representation is
the embedded payload, and drift detection needs no DOM). No network, client,
or cache imports.

Representation evidence (2026-08-30 bounded live probes of
``/calendar.ashx`` and ``/calendar/economic/detail/<RELEASE>``): both pages
are SPA shells carrying one ``<script id="route-init-data"
type="application/json">`` payload. The current-calendar payload has
``data.entries`` (complete event rows); the detail payload has ``data.table``
(release history rows) plus ``category``. No legacy ``table.calendar`` HTML
remains, and the payload carries no country field — sessions/country are never
invented.

Entry field contract (verified): ``calendarId``, ``ticker``, ``event``,
``category``, ``date`` (ISO local ``YYYY-MM-DDTHH:MM:SS`` or date-only),
``reference``, ``referenceDate``, ``actual``/``previous``/``forecast``
(display strings or null), ``importance`` (integer rank), ``allDay``.

Missing/unknown fields land in ``extra_fields`` with drift warnings through
the shared Arrow builder; structurally broken payloads (entries not a list,
payload absent) raise :class:`FinvizParseError`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from finvizp.errors import FinvizParseError

__all__ = [
    "CalendarRecords",
    "ReleaseDetailRecords",
    "parse_calendar_page",
    "parse_release_detail_page",
]

_ROUTE_INIT = re.compile(
    r'<script id="route-init-data" type="application/json">\s*(.*?)\s*</script>', re.S
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WarningCallback = Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class CalendarRecords:
    """Parsed current calendar: source-near row mappings in payload order.

    ``displays`` carries the provider's verbatim temporal/value displays that
    the rows hand over in normalized shape (ISO day for ``release_date``,
    ``%``-stripped numbers): field -> one entry per row (``None`` where the
    provider had no display), consumed by the endpoint module to restore the
    exact ``*_raw`` companions.
    """

    rows: tuple[dict[str, Any], ...]
    displays: dict[str, list[str | None]]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ReleaseDetailRecords:
    """Parsed release detail: history rows plus the release's category label."""

    rows: tuple[dict[str, Any], ...]
    displays: dict[str, list[str | None]]
    category: str | None
    fingerprint: str


def parse_calendar_page(
    html: str, *, fetched_at: Any, on_warning: WarningCallback | None = None
) -> CalendarRecords:
    """Parse one current-calendar page into source-near row mappings."""
    payload = _route_init_payload(html)
    data = _data_of(payload)
    entries = data.get("entries")
    if not isinstance(entries, list):
        msg = "calendar payload has no entries list"
        raise FinvizParseError(msg, context={"endpoint": "calendar"})
    rows: list[dict[str, Any]] = []
    date_displays: list[str | None] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rows.append(_source_near_row(entry))
        date = entry.get("date")
        date_displays.append(date if isinstance(date, str) else None)
    return CalendarRecords(
        rows=tuple(rows),
        displays={"release_date": date_displays},
        fingerprint=_fingerprint(payload),
    )


def parse_release_detail_page(
    html: str, *, fetched_at: Any, on_warning: WarningCallback | None = None
) -> ReleaseDetailRecords:
    """Parse one release-detail page into history rows plus category metadata."""
    payload = _route_init_payload(html)
    data = _data_of(payload)
    table = data.get("table")
    if not isinstance(table, list):
        msg = "release detail payload has no table list"
        raise FinvizParseError(msg, context={"endpoint": "calendar_detail"})
    rows: list[dict[str, Any]] = []
    date_displays: list[str | None] = []
    for entry in table:
        if not isinstance(entry, dict):
            continue
        rows.append(_source_near_row(entry))
        date = entry.get("date")
        date_displays.append(date if isinstance(date, str) else None)
    category = data.get("category")
    return ReleaseDetailRecords(
        rows=tuple(rows),
        displays={"release_date": date_displays},
        category=category if isinstance(category, str) else None,
        fingerprint=_fingerprint(payload),
    )


def _route_init_payload(html: str) -> dict[str, Any]:
    match = _ROUTE_INIT.search(html)
    if match is None:
        msg = "calendar page has no route-init-data payload"
        raise FinvizParseError(msg, context={"endpoint": "calendar"})
    try:
        payload = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError) as exc:
        msg = f"calendar route-init-data payload is not valid JSON: {exc}"
        raise FinvizParseError(msg, context={"endpoint": "calendar"}) from exc
    if not isinstance(payload, dict):
        msg = "calendar route-init-data payload must be an object"
        raise FinvizParseError(msg, context={"endpoint": "calendar"})
    return payload


def _data_of(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        msg = "calendar payload has no data object"
        raise FinvizParseError(msg, context={"endpoint": "calendar"})
    return data


def _source_near_row(entry: dict[str, Any]) -> dict[str, Any]:
    """One provider entry -> the Arrow builder's source-near row mapping.

    Temporal normalization: the ``date`` display ``YYYY-MM-DDTHH:MM:SS`` is
    split into ``release_date`` (ISO day, builder converts to date32),
    ``release_time`` (``HH:MM`` text), and ``release_timestamp`` (the full
    display; the builder converts exact US Eastern -> UTC). Date-only or
    ``allDay`` ``date`` values yield ``release_date`` only; time and timestamp
    stay null (never invented). Value displays (actual/forecast/previous) pass
    through verbatim; the builder's compact unit normalizes them and
    ``raw_overrides`` restores the exact provider ``date`` display in the
    ``release_date_raw`` companion.
    """
    date_display = entry.get("date")
    row: dict[str, Any] = {
        "symbol": str(entry.get("ticker") or ""),
        "event": str(entry.get("event") or ""),
        "category": entry.get("category"),
        "importance": entry.get("importance"),
        "reference_period": entry.get("reference"),
        "reference_date": entry.get("referenceDate"),
        "actual": entry.get("actual"),
        "forecast": entry.get("forecast"),
        "previous": entry.get("previous"),
    }
    if isinstance(date_display, str) and date_display:
        day = date_display[:10]
        row["release_date"] = day if _ISO_DATE.match(day) else date_display
        time_part = (
            date_display[11:16] if len(date_display) >= 16 and date_display[10] == "T" else None
        )
        row["release_time"] = time_part
        row["release_timestamp"] = date_display if time_part else None
    else:
        row["release_date"] = None
        row["release_time"] = None
        row["release_timestamp"] = None
    return row


def _fingerprint(payload: dict[str, Any]) -> str:
    """SHA-256 over the payload's ordered key skeleton, no values."""
    import hashlib

    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in value:
                parts.append(f"o:{key}")
                walk(value[key])
        elif isinstance(value, list):
            parts.append(f"a:{len(value)}")
            for item in value[:1]:
                walk(item)

    walk(payload)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
