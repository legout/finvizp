"""Build scrubbed structural screener-page fixtures (live evidence: 2026-08-30 probes).

Run with ``uv run python -m tests.fixtures.screener._build`` from the repo root.
Emits hand-shaped HTML documents mirroring the verified public screener structure:

- one ``table.styled-table-new.screener_table`` whose ``thead`` labels are the
  header-driven column contract and whose ``./tr`` children are data rows;
- data rows carry the absolute rank in td[0], the ticker cell in td[1]
  (``data-boxover-ticker`` attribute + anchor text), then one raw display per
  remaining column;
- a ``div#screener-total`` ``#<start> / <total> Total`` page marker;
- ``table#js-screener-body-empty`` + ``0 Total`` as the positively recognized
  no-results state (no ``screener_table`` present).

No live HTML is copied; every value is synthetic.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent

# overview view: registry column labels for the fixed view.
OVERVIEW_COLUMNS = [
    "No.",
    "Ticker",
    "Company",
    "Sector",
    "Industry",
    "Country",
    "Market Cap",
    "P/E",
    "Price",
    "Change %",
    "Volume",
]

# 20 synthetic rows: (rank, ticker, raw displays for the remaining columns).
PAGE_ROWS: list[tuple[int, str, list[str]]] = [
    (
        rank,
        ticker,
        [
            f"Sample Co {rank}",
            "Technology",
            "Software",
            "USA",
            "1.20B",
            "12.34",
            "10.10",
            "1.23%",
            "123,456",
        ],
    )
    for rank, ticker in zip(
        range(1, 21),
        [f"S{rank:02d}X" for rank in range(1, 21)],
        strict=True,
    )
]

FINAL_ROWS: list[tuple[int, str, list[str]]] = [
    (
        rank,
        ticker,
        [
            f"Last Co {rank}",
            "Energy",
            "Oil & Gas",
            "USA",
            "980.11M",
            "7.77",
            "5.55",
            "-0.88%",
            "9,876",
        ],
    )
    for rank, ticker in zip(
        range(581, 589),
        [f"Z{rank:03d}L" for rank in range(581, 589)],
        strict=True,
    )
]


def _head(total_rows: int, page_start: int) -> str:
    return (
        "<!DOCTYPE html><html><head><title>Stock Screener</title></head>"
        '<body><div class="screener-view-container">'
        f'<div id="screener-total" class="count-text whitespace-nowrap">'
        f"#{page_start} / {total_rows} Total</div>"
    )


def _table(columns: list[str], rows: list[tuple[int, str, list[str]]]) -> str:
    heads = "".join(f"<th>{label}</th>" for label in columns)
    body = []
    for rank, ticker, values in rows:
        cells = [
            f'<td height="10" align="right"><a href="stock?t={ticker}'
            f'&amp;ty=c&amp;p=d">{rank}</a></td>',
            f'<td height="10" align="left" data-boxover-ticker="{ticker}" '
            f'data-boxover-company="{values[0]}">'
            f'<a href="stock?t={ticker}" class="tab-link">{ticker}</a></td>',
        ]
        cells.extend(f'<td height="10">{value}</td>' for value in values)
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<table class="styled-table-new is-rounded is-tabular-nums w-full screener_table">'
        f'<thead><tr align="center">{heads}</tr></thead>{"".join(body)}</table>'
    )


def overview_page(*, rows: list[tuple[int, str, list[str]]], total: int, start: int) -> str:
    """One populated overview page; ``start`` is the first rank shown."""
    return _head(total, start) + _table(OVERVIEW_COLUMNS, rows) + "</div></body></html>"


def custom_page() -> str:
    """Custom view: header labels follow the requested column codes (No., Ticker, Price)."""
    rows = [
        (rank, ticker, [f"{10 + rank / 10:.2f}"])
        for rank, ticker in zip(
            range(1, 21), [f"S{rank:02d}X" for rank in range(1, 21)], strict=True
        )
    ]
    return _head(20, 1) + _table(["No.", "Ticker", "Price"], rows) + "</div></body></html>"


def no_results_page() -> str:
    """Positively recognized no-results state: no screener_table, empty-state body."""
    return (
        "<!DOCTYPE html><html><head><title>Stock Screener</title></head><body>"
        '<table id="js-screener-body-empty" width="100%"><tr>'
        '<td width="140" align="left" class="count-text">0 Total</td>'
        '<td align="center" class="fullview-links">'
        '<a href="/elite" class="tab-link">Create Alert</a></td>'
        "</tr></table></body></html>"
    )


def malformed_page() -> str:
    """Drift: a screener_table whose rows do not match the header arity."""
    return (
        _head(5, 1)
        + _table(OVERVIEW_COLUMNS, [(1, "S01X", ["Sample Co 1", "Technology"])])
        + "</div></body></html>"
    )


def header_drift_page() -> str:
    """Drift: header labels missing entirely (empty thead)."""
    return (
        _head(1, 1) + '<table class="screener_table"><thead><tr></tr></thead>'
        "<tr><td>1</td><td>S01X</td></tr></table></div></body></html>"
    )


def no_ticker_page() -> str:
    """Live 2026-08-30 drift: custom view without a Ticker column.

    The provider renders exactly the requested columns: ``c=0,68`` (rank +
    Earnings) yields two-cell rows whose second cell is the earnings display
    itself (``Aug 26/a``) — no ticker cell anywhere. The parser's positional
    ticker assumption then consumes the display as the symbol (``AUG 26/A``)
    and the header contract runs one display short.
    """
    heads = "<th>No.</th><th>Earnings</th>"
    anchor = '<a href="stock?t=S{0:02d}X&amp;ty=c&amp;p=d">{1}</a>'
    body = "".join(
        "<tr>"
        f'<td height="10" align="right">{anchor.format(rank, rank)}</td>'
        f'<td height="10" align="right">{anchor.format(rank, display)}</td>'
        "</tr>"
        for rank, display in enumerate(TICKERLESS_ROWS, start=1)
    )
    return (
        _head(len(TICKERLESS_ROWS), 1)
        + '<table class="styled-table-new is-rounded is-tabular-nums w-full screener_table">'
        f'<thead><tr align="center">{heads}</tr></thead>{body}</table></div></body></html>'
    )


def earnings_grid_page() -> str:
    """Live-shaped ``c=0,1,68`` grid: No. + Ticker + ``Earnings`` headers.

    The provider labels custom-column code 68 ``Earnings`` (not the registry
    name ``Earnings Date``) and renders compact displays ``Aug 26/a``.
    """
    heads = "<th>No.</th><th>Ticker</th><th>Earnings</th>"
    tickers = ("S01X", "S02X", "S03X", "S04X", "S05X")
    link = '<a href="stock?t={0}&amp;ty=c&amp;p=d">{1}</a>'
    body = "".join(
        "<tr>"
        f'<td height="10" align="right">{link.format(ticker, rank)}</td>'
        f'<td height="10" align="left" data-boxover-ticker="{ticker}">'
        f'<a href="stock?t={ticker}" class="tab-link">{ticker}</a></td>'
        f'<td height="10" align="right">{link.format(ticker, display)}</td>'
        "</tr>"
        for rank, ticker, (display,) in zip(
            range(1, 6), tickers, ([d] for _, d in TICKERLESS_ROWS), strict=True
        )
    )
    return (
        _head(len(TICKERLESS_ROWS), 1)
        + '<table class="styled-table-new is-rounded is-tabular-nums w-full screener_table">'
        f'<thead><tr align="center">{heads}</tr></thead>{body}</table></div></body></html>'
    )


# Compact grid displays: ``/a`` = after market close (AMC), ``/b`` = before
# market open (BMO) — verified per-ticker against quote pages 2026-08-30.
TICKERLESS_ROWS: list[tuple[int, str]] = [
    (rank, display)
    for rank, display in enumerate(
        ("Aug 26/a", "Aug 27/a", "Aug 27/a", "Aug 24/b", "Aug 26/b"), start=1
    )
]


def write_fixtures() -> dict[str, Path]:
    out: dict[str, Path] = {}

    def emit(name: str, html: str) -> None:
        path = HERE / name
        path.write_text(html, "utf-8")
        out[name] = path

    emit("overview-page-1.html", overview_page(rows=PAGE_ROWS, total=600, start=1))
    emit("overview-final-page.html", overview_page(rows=FINAL_ROWS, total=588, start=581))
    emit("custom-columns.html", custom_page())
    emit("no-results.html", no_results_page())
    emit("no-ticker-columns.html", no_ticker_page())
    emit("earnings-grid.html", earnings_grid_page())
    emit("_drift-malformed-row.html", malformed_page())
    emit("_drift-header.html", header_drift_page())
    return out


def overview_for(start: int, *, total: int = 600, per_page: int = 20) -> str:
    """Provider-shaped page for an arbitrary start rank (test transport helper)."""
    count = min(per_page, total - start + 1)
    rows = [
        (
            rank,
            f"S{rank:03d}X",
            [
                f"Sample Co {rank}",
                "Technology",
                "Software",
                "USA",
                "1.20B",
                "12.34",
                "10.10",
                "1.23%",
                f"{rank * 1000:,}",
            ],
        )
        for rank in range(start, start + count)
    ]
    return overview_page(rows=rows, total=total, start=start)


if __name__ == "__main__":
    for path in write_fixtures().values():
        print(path)
