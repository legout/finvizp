"""Build scrubbed structural groups-page fixtures (live evidence: 2026-08-30 probes).

Run with ``uv run python -m tests.fixtures.groups._build`` from the repo root.
Emits hand-shaped HTML documents mirroring the verified public groups surface:

- one ``table.groups_table`` whose ``thead`` labels are ``No. / Name /`` the
  view's aggregate columns and whose ``tr`` children are data rows (the group
  index rides td[0], the group-name anchor td[1], one display per remaining
  header);
- ``img.groups_spectrum-image`` carrying ``grp_image?spectrum_<dim>.png`` on
  the spectrum (v=310) page;
- ``_drift-*`` variants: missing header, row/column arity mismatch, duplicate
  tables, and a spectrum page without its image.

No live HTML is copied; every value is synthetic.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent

# Overview (v=110) header labels, live-verified 2026-08-30.
OVERVIEW_COLUMNS = [
    "No.",
    "Name",
    "Stocks",
    "Market Cap",
    "Dividend",
    "P/E",
    "Fwd P/E",
    "PEG",
    "LTDebt/Eq",
    "Debt/Eq",
    "Float Short",
    "Recom",
    "Change %",
    "Volume",
]

# Synthetic sector rows: (index, name, displays for the remaining columns).
SECTOR_ROWS: list[tuple[int, str, list[str]]] = [
    (
        index,
        name,
        [
            stocks,
            market_cap,
            dividend,
            pe,
            fwd_pe,
            peg,
            lt_debt_eq,
            debt_eq,
            float_short,
            recom,
            change,
            volume,
        ],
    )
    for index, name, stocks, market_cap, dividend, pe, fwd_pe, peg, lt_debt_eq, debt_eq, (
        float_short
    ), recom, change, volume in [
        (
            2,
            "Communication Services",
            "258",
            "12551.11B",
            "0.61%",
            "18.00",
            "19.71",
            "1.00",
            "0.59",
            "0.65",
            "3.15%",
            "1.40",
            "1.44%",
            "495.43M",
        ),
        (
            3,
            "Consumer Cyclical",
            "533",
            "9266.18B",
            "0.79%",
            "25.00",
            "20.74",
            "1.25",
            "0.92",
            "1.14",
            "5.28%",
            "1.76",
            "1.36%",
            "899.39M",
        ),
        (
            4,
            "Consumer Defensive",
            "243",
            "4388.24B",
            "2.43%",
            "24.87",
            "19.96",
            "2.72",
            "0.93",
            "1.09",
            "2.87%",
            "1.98",
            "0.57%",
            "344.08M",
        ),
        (
            5,
            "Energy",
            "154",
            "5031.24B",
            "3.02%",
            "13.09",
            "12.40",
            "0.70",
            "0.67",
            "0.77",
            "2.29%",
            "2.10",
            "-1.20%",
            "311.63M",
        ),
        (
            6,
            "Financial",
            "689",
            "13044.53B",
            "1.79%",
            "14.22",
            "13.72",
            "1.30",
            "0.85",
            "1.05",
            "2.63%",
            "1.83",
            "0.65%",
            "677.66M",
        ),
        (
            7,
            "Healthcare",
            "1320",
            "8496.94B",
            "1.24%",
            "22.34",
            "18.89",
            "1.60",
            "0.48",
            "0.54",
            "2.94%",
            "1.79",
            "0.32%",
            "402.55M",
        ),
        (
            8,
            "Industrials",
            "754",
            "6592.90B",
            "1.17%",
            "25.06",
            "22.57",
            "1.85",
            "0.63",
            "0.71",
            "2.62%",
            "1.91",
            "0.44%",
            "310.92M",
        ),
        (
            9,
            "Technology",
            "1024",
            "21400.15B",
            "0.68%",
            "33.71",
            "27.71",
            "1.90",
            "0.33",
            "0.38",
            "3.11%",
            "1.74",
            "1.03%",
            "721.20M",
        ),
        (
            10,
            "Utilities",
            "168",
            "2477.97B",
            "3.10%",
            "17.86",
            "17.86",
            "2.30",
            "1.62",
            "1.71",
            "2.75%",
            "1.99",
            "-0.15%",
            "88.16M",
        ),
        (
            11,
            "Real Estate",
            "194",
            "1979.28B",
            "3.72%",
            "38.79",
            "34.11",
            "2.65",
            "1.20",
            "1.34",
            "2.05%",
            "1.66",
            "0.21%",
            "61.35M",
        ),
        (
            12,
            "Basic Materials",
            "277",
            "2418.03B",
            "1.66%",
            "19.36",
            "18.42",
            "1.45",
            "0.70",
            "0.84",
            "2.48%",
            "2.02",
            "0.55%",
            "129.75M",
        ),
    ]
]

# Custom (v=152) default-ish column selection, live-verified headers.
CUSTOM_COLUMNS = ["No.", "Name", "Market Cap", "P/E", "Stocks"]
CUSTOM_ROWS: list[tuple[int, str, list[str]]] = [
    (2, "Communication Services", ["12551.11B", "18.00", "258"]),
    (9, "Technology", ["21400.15B", "33.71", "1024"]),
]


def _page(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><title>Stock Groups</title></head>"
        '<body><div class="m-0 is-groups min-w-[1009px]">'
        f"{body}</div></body></html>"
    )


def _table(columns: list[str], rows: list[tuple[int, str, list[str]]]) -> str:
    heads = "".join(f"<th>{label}</th>" for label in columns)
    body = []
    for index, name, values in rows:
        cells = [
            f'<td class="groups_index-cell groups_sticky-cell">{index}</td>',
            '<td class="groups_sticky-cell"><a href="screener?f=sec_x&amp;v=111" '
            f'class="tab-link">{name}</a></td>',
        ]
        cells.extend(f'<td height="10">{value}</td>' for value in values)
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<table class="styled-table-new is-medium is-rounded is-tabular-nums w-full groups_table">'
        f'<thead><tr align="center">{heads}</tr></thead>{"".join(body)}</table>'
    )


def overview_page() -> str:
    """One populated overview page (v=110 shape, live-verified headers)."""
    return _page(_table(OVERVIEW_COLUMNS, SECTOR_ROWS))


def custom_page() -> str:
    """One populated custom page (v=152 shape): headers follow the requested codes."""
    return _page(_table(CUSTOM_COLUMNS, CUSTOM_ROWS))


def spectrum_page() -> str:
    """Spectrum (v=310) page: the spectrum image descriptor, no groups_table."""
    return _page(
        '<img class="groups_spectrum-image" width="950" height="298" '
        'src="grp_image?spectrum_sector.png&amp;rev=123">'
    )


def empty_page() -> str:
    """Recognized empty: a groups_table whose header exists but has zero data rows."""
    return _page(_table(OVERVIEW_COLUMNS, []))


def no_name_page() -> str:
    """Drift: header without the Name column."""
    columns = ["No.", "Stocks", "Market Cap"]
    return _page(
        _table(
            columns,
            [(2, "Communication Services", ["258", "12551.11B"])],
        )
    )


def malformed_row_page() -> str:
    """Drift: a data row with fewer cells than the header arity."""
    heads = "".join(f"<th>{label}</th>" for label in OVERVIEW_COLUMNS)
    short_row = (
        '<tr><td class="groups_index-cell">2</td>'
        '<td><a href="screener?f=sec_x&amp;v=111">Communication Services</a></td>'
        "<td>258</td><td>12551.11B</td></tr>"
    )
    return _page(
        '<table class="styled-table-new is-medium is-rounded is-tabular-nums w-full groups_table">'
        f'<thead><tr align="center">{heads}</tr></thead>{short_row}</table>'
    )


def duplicate_table_page() -> str:
    """Drift: two groups_table elements on one page."""
    return _page(_table(OVERVIEW_COLUMNS, SECTOR_ROWS) + _table(OVERVIEW_COLUMNS, []))


def header_drift_page() -> str:
    """Drift: header labels missing entirely (empty thead)."""
    return _page(
        '<table class="groups_table"><thead><tr></tr></thead><tr><td>2</td><td>X</td></tr></table>'
    )


def spectrum_missing_image_page() -> str:
    """Drift: spectrum view page without the spectrum image."""
    return _page("<div class='groups-content'>no image here</div>")


FILES = {
    "overview.html": overview_page,
    "valuation.html": custom_page,  # same structural shape, different v
    "spectrum.html": spectrum_page,
    "empty.html": empty_page,
    "_drift-no-name.html": no_name_page,
    "_drift-malformed-row.html": malformed_row_page,
    "_drift-duplicate-table.html": duplicate_table_page,
    "_drift-header.html": header_drift_page,
    "_drift-spectrum-missing-image.html": spectrum_missing_image_page,
}


def main() -> int:
    HERE.mkdir(parents=True, exist_ok=True)
    for name, build in FILES.items():
        (HERE / name).write_text(build(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
