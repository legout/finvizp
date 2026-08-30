"""Build scrubbed calendar fixtures (live evidence: 2026-08-30 probes).

Run with ``uv run python -m tests.fixtures.calendar._build`` from the repo root.
Emits hand-shaped HTML documents mirroring the verified public calendar pages:

- ``current-embedded.html``: the ``/calendar.ashx`` SPA shell whose one
  ``<script id="route-init-data" type="application/json">`` payload carries the
  complete current calendar (``data.entries``) — the verified representation;
- ``detail.html``: the ``/calendar/economic/detail/<release>`` page whose
  ``route-init-data`` payload carries release metadata plus the release's
  ``data.table`` history rows;
- ``_drift-malformed-entries.html`` / ``_drift-missing-entries.html``: payload
  drift states for typed-error coverage.

No live HTML is copied; every value is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def _entry(
    calendar_id: int,
    ticker: str,
    event: str,
    category: str,
    date: str,
    *,
    reference: str | None,
    reference_date: str | None,
    actual: str | None,
    previous: str | None,
    forecast: str | None,
    importance: int,
) -> dict:
    """One provider-shaped calendar entry (verified 2026-08-30 field set)."""
    return {
        "calendarId": calendar_id,
        "ticker": ticker,
        "event": event,
        "category": category,
        "date": date,
        "reference": reference,
        "referenceDate": reference_date,
        "actual": actual,
        "previous": previous,
        "forecast": forecast,
        "teforecast": None,
        "importance": importance,
        "isHigherPositive": 1,
        "hasNoDetail": False,
        "alert": None,
        "allDay": False,
        "nonEmptinessScore": 2,
    }


# Two-day window with one released row (actual present), one upcoming row, a
# date-only-all-day row, and one compact- ticker release without detail.
ENTRIES = [
    _entry(
        399001,
        "USACPI",
        "CPI MoM",
        "Consumer Price Index",
        "2026-08-12T08:30:00",
        reference="Jul",
        reference_date="2026-07-31",
        actual="0.2%",
        previous="0.3%",
        forecast="0.2%",
        importance=3,
    ),
    _entry(
        399002,
        "UNITEDSTAJOBLES",
        "Initial Jobless Claims",
        "Initial Jobless Claims",
        "2026-08-13T08:30:00",
        reference="08/08",
        reference_date="2026-08-08",
        actual=None,
        previous="211K",
        forecast="205K",
        importance=2,
    ),
    _entry(
        399003,
        "FDTR",
        "FOMC Rate Decision",
        "Interest Rate",
        "2026-08-14T18:00:00",
        reference=None,
        reference_date=None,
        actual=None,
        previous=None,
        forecast=None,
        importance=3,
    ),
]

DETAIL_TABLE = [
    _entry(
        399101,
        "USACPI",
        "CPI MoM",
        "Consumer Price Index",
        "2026-09-11T08:30:00",
        reference="Aug",
        reference_date="2026-08-31",
        actual=None,
        previous="0.2%",
        forecast="0.2%",
        importance=3,
    ),
    _entry(
        399102,
        "USACPI",
        "CPI MoM",
        "Consumer Price Index",
        "2026-08-12T08:30:00",
        reference="Jul",
        reference_date="2026-07-31",
        actual="0.2%",
        previous="0.3%",
        forecast="0.2%",
        importance=3,
    ),
    _entry(
        399103,
        "USACPI",
        "CPI MoM",
        "Consumer Price Index",
        "2026-07-14T08:30:00",
        reference="Jun",
        reference_date="2026-06-30",
        actual="0.3%",
        previous="0.1%",
        forecast="0.3%",
        importance=3,
    ),
]


def _page(payload: dict, title: str) -> str:
    """SPA shell with one embedded route-init-data JSON payload."""
    body = json.dumps(payload, separators=(",", ":"))
    return (
        "<!DOCTYPE html><html><head>"
        f"<title>{title} - Finviz</title></head>"
        '<body><div id="root"></div>'
        '<script id="route-init-data" type="application/json">'
        f"{body}</script>"
        "</body></html>"
    )


def current_embedded_page() -> str:
    """Current calendar: complete entries array in the embedded JSON."""
    return _page(
        {"data": {"initialDateFrom": "2026-08-31", "entries": ENTRIES}, "version": 3},
        "Economic Calendar",
    )


def detail_page() -> str:
    """Release detail: category metadata plus the release's history table."""
    return _page(
        {
            "data": {
                "ticker": "USACPI",
                "category": "Consumer Price Index",
                "description": None,
                "table": DETAIL_TABLE,
                "frequency": 1,
                "chartUnit": "points",
                "chartSource": "Sample Source",
            },
            "version": 3,
        },
        "Economic Calendar - CPI MoM",
    )


def malformed_entries_page() -> str:
    """Payload drift: entries is not a list."""
    return _page({"data": {"entries": {"broken": True}}, "version": 3}, "Economic Calendar")


def missing_entries_page() -> str:
    """Payload drift: the entries key is absent entirely."""
    return _page({"data": {"initialDateFrom": "2026-08-31"}, "version": 3}, "Economic Calendar")


def not_found_page() -> str:
    """Unknown release slug: the provider's 404 shell has no route-init-data."""
    return (
        "<!DOCTYPE html><html><head><title>Finviz</title></head>"
        '<body><img src="/gfx/error-pages/error_404_light.svg" alt="Error 404 illustration" />'
        "</body></html>"
    )


def main() -> None:
    (HERE / "current-embedded.html").write_text(current_embedded_page(), "utf-8")
    (HERE / "detail.html").write_text(detail_page(), "utf-8")
    (HERE / "_drift-malformed-entries.html").write_text(malformed_entries_page(), "utf-8")
    (HERE / "_drift-missing-entries.html").write_text(missing_entries_page(), "utf-8")
    (HERE / "not-found.html").write_text(not_found_page(), "utf-8")
    print("calendar fixtures written to", HERE)


if __name__ == "__main__":
    main()
