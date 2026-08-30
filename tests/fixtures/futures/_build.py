"""Build scrubbed futures fixtures (live evidence: 2026-08-30 probe).

Run with ``uv run python -m tests.fixtures.futures._build`` from the repo root.
Emits hand-shaped HTML documents mirroring the verified public ``/futures``
page (the ``/futures.ashx`` legacy route 301-redirects there):

- ``current-tiles.html``: one inline script carrying ``var groups = [...]``
  (category groups whose contracts join the tiles by ticker) and
  ``var tiles = {...}`` (one current tile per contract). The verified page
  has no ``<table>`` element at all — the legacy performance-table target is
  gone — and no chart/image artifact references. Every ``sparkline`` array
  and ``sparklineDateChanges`` object was empty in the probe and carries no
  provider timestamps, so fixtures keep them empty: no invented history.
- ``_drift-missing-tiles.html``: the tiles object is absent (drift).
- ``_drift-malformed-tiles.html``: the tiles value is not an object (drift).
- ``_empty-tiles.html``: an empty tiles object — the recognized empty state.

No live HTML is copied; every value is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def _tile(
    ticker: str,
    label: str,
    *,
    last: float,
    change: float,
    change_usd: float,
    prev_close: float,
    high: float,
    low: float,
) -> dict:
    """One provider-shaped futures tile (verified 2026-08-30 field set)."""
    return {
        "label": label,
        "ticker": ticker,
        "last": last,
        "change": change,
        "changeUsd": change_usd,
        "prevClose": prev_close,
        "high": high,
        "low": low,
        # Verified empty with no timestamps: payload decoration, not history.
        "sparkline": [],
        "sparklineDateChanges": {},
    }


def _contract(ticker: str, label: str, cot: str) -> dict:
    return {"label": label, "ticker": ticker, "cot": cot, "cotArray": [cot]}


# Two groups / three contracts cover every tile field, the group join, an
# unknown-tile-field case, and a contract with an empty COT display.
GROUPS = [
    {
        "ticker": "INDICES",
        "label": "Indices",
        "contracts": [
            _contract("ES", "S&P 500", "138741"),
            _contract("NQ", "Nasdaq 100", "209742"),
            _contract("VX", "VIX", ""),
        ],
    },
    {
        "ticker": "ENERGY",
        "label": "Energy",
        "contracts": [_contract("CL", "Crude Oil", "067651")],
    },
]

TILES = {
    "ES": _tile(
        "ES",
        "S&P 500",
        last=7724.75,
        change=-0.26,
        change_usd=-20.5,
        prev_close=7742.5,
        high=7782.5,
        low=7711.75,
    ),
    "NQ": _tile(
        "NQ",
        "Nasdaq 100",
        last=25180.25,
        change=-0.42,
        change_usd=-106.25,
        prev_close=25286.5,
        high=25310.0,
        low=25102.0,
    ),
    "VX": _tile(
        "VX",
        "VIX",
        last=16.04,
        change=2.16,
        change_usd=0.34,
        prev_close=15.7,
        high=16.35,
        low=15.62,
    ),
    "CL": _tile(
        "CL",
        "Crude Oil",
        last=64.37,
        change=-0.88,
        change_usd=-0.57,
        prev_close=64.94,
        high=65.22,
        low=63.98,
    ),
}
# Unknown additive field: must land in extra_fields with a drift warning.
TILES["ES"]["someFutureField"] = "future-value"

FOOTER_DELAY = "Stock quotes delayed by 1 minute. Futures and options delayed by 15 minutes."


def _page(tiles_json: str) -> str:
    """SPA shell whose one inline futures script carries groups + tiles."""
    groups_json = json.dumps(GROUPS, separators=(",", ":"))
    return (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        "<title>Futures Prices - Finviz</title></head>"
        '<body><div id="futures"></div>'
        f"<style>.sparkline {{ vertical-align: middle; }}</style>"
        '<script type="text/javascript">(function() { var groups = '
        f"{groups_json};"
        f" var tiles = {tiles_json};"
        " groups.forEach(function(group) { for (var i = 0; i < group.contracts.length;"
        " i++) { group.contracts[i] = tiles[group.contracts[i].ticker]; } });"
        " window.addEventListener('Finviz:FinvizInitFutures', () =>"
        " window.FinvizInitFutures(groups), {once: true}); })();</script>"
        f"<div>{FOOTER_DELAY}</div>"
        "</body></html>"
    )


def current_tiles_page() -> str:
    """Current futures: groups join + tiles object, one script."""
    return _page(json.dumps(TILES, separators=(",", ":")))


def missing_tiles_page() -> str:
    """Payload drift: the tiles variable is absent entirely."""
    groups_json = json.dumps(GROUPS, separators=(",", ":"))
    return (
        "<!DOCTYPE html><html><head><title>Futures Prices - Finviz</title></head>"
        '<body><div id="futures"></div>'
        '<script type="text/javascript">(function() { var groups = '
        f"{groups_json}; }})();</script>"
        "</body></html>"
    )


def malformed_tiles_page() -> str:
    """Payload drift: the tiles variable is not an object."""
    return _page('["broken"]')


def empty_tiles_page() -> str:
    """Recognized empty state: an empty tiles object."""
    return _page("{}")


def main() -> None:
    (HERE / "current-tiles.html").write_text(current_tiles_page(), "utf-8")
    (HERE / "_drift-missing-tiles.html").write_text(missing_tiles_page(), "utf-8")
    (HERE / "_drift-malformed-tiles.html").write_text(malformed_tiles_page(), "utf-8")
    (HERE / "_empty-tiles.html").write_text(empty_tiles_page(), "utf-8")
    print("futures fixtures written to", HERE)


if __name__ == "__main__":
    main()
