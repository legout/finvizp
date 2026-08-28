"""Build scrubbed structural quote-page fixtures (current + reordered).

Run with ``uv run python -m tests.fixtures.quote._build`` from the repo root.
Emits two hand-shaped HTML documents representing the verified 2026-08 stock
page structure: six ``snapshot-table2`` tables, ratings, news, insider rows,
peers/ETF ``data-boxover`` spans, profile text, and the quote header. No live
HTML is copied; every value is synthetic. The reordered variant permutes the
six tables and swaps two row pairs inside one table while keeping identical
semantics, proving the parser never depends on global table indices.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent

# label -> value for every snapshot cell across the six tables. Values keep the
# provider's display spelling so parser normalization expectations are real.
# The subset is exactly the labels the frozen ``quote_snapshot`` registry maps;
# additive/unmapped live labels are covered by the synthetic extra_fields test
# in ``tests/test_quote_parser.py`` rather than by bulk unknown-field noise.
SNAPSHOT: list[tuple[str, str]] = [
    ("Index", "DJIA, NDX, S&P 500"),
    ("Market Cap", "3.10T"),
    ("P/E", "32.10"),
    ("EPS (ttm)", "8.20"),
    ("Price", "315.46"),
    ("Change", "0.88 (0.28%)"),
    ("Dividend TTM", "1.04 (0.33%)"),
    ("Dividend Ex-Date", "Aug 10, 2026"),
    ("Earnings", "Jul 30 AMC"),
    ("Employees", "166000"),
    ("IPO Date", "Dec 12, 1980"),
    ("SMA20", "2.40%"),
    ("SMA50", "4.10%"),
    ("SMA200", "9.60%"),
    ("Shs Outstand", "15.10B"),
    ("Shs Float", "15.00B"),
    ("Beta", "1.24"),
    ("Avg Volume", "55.20M"),
    ("Volume", "52.40M"),
]

# The six live tables split the label sequence into six verified groups.
# Reordering the groups must not change the parse.
TABLE_GROUPS = [(0, 6), (6, 11), (11, 13), (13, 15), (15, 17), (17, 19)]


def _snapshot_table(cells: list[tuple[str, str]]) -> str:
    rows = "".join(
        '<tr class="table-dark-row">'
        '<td class="snapshot-td2"><div class="snapshot-td-label">'
        f"{label}</div></td>"
        '<td class="snapshot-td2"><div class="snapshot-td-content">'
        f"{value}</div></td>"
        "</tr>"
        for label, value in cells
    )
    return (
        '<table width="100%" cellpadding="3" cellspacing="0" border="0" '
        'class="js-snapshot-table snapshot-table2 screener_snapshot-table-body">'
        f"{rows}</table>"
    )


def current_tables() -> list[str]:
    return [_snapshot_table(SNAPSHOT[start:stop]) for start, stop in TABLE_GROUPS]


def reordered_tables() -> list[str]:
    groups = [SNAPSHOT[start:stop] for start, stop in TABLE_GROUPS]
    # Permute the six tables and swap the first two row pairs inside one table:
    # rows move, label->value pairing stays intact.
    permuted = [groups[i] for i in (5, 2, 4, 0, 3, 1)]
    swapped_first = permuted[3][1:3] + permuted[3][0:1] + permuted[3][3:]
    permuted[3] = swapped_first
    return [_snapshot_table(cells) for cells in permuted]


def _ratings() -> str:
    def row(date: str, action: str, analyst: str, rating: str, target: str) -> str:
        return (
            f"<tr><td>{date}</td><td><span>{action}</span></td>"
            f"<td>{analyst}</td><td>{rating}</td><td>{target}</td></tr>"
        )

    body = (
        row("Aug-17-26", "Upgrade", "Redburn", "Neutral &rarr; Buy", "$230")
        + row("Aug-10-26", "Downgrade", "Jefferies", "Hold &rarr; Underperform", "$163.66")
        + row("Aug-04-26", "Reiterated", "DZ Bank", "Buy", "$110")
    )
    return (
        '<table width="100%" class="js-table-ratings styled-table-new" '
        'cellpadding="0" cellspacing="0" border="0"><thead><tr>'
        "<th>Date</th><th>Action</th><th>Analyst</th><th>Rating Change</th>"
        "<th>Price Target Change</th></tr></thead>" + body + "</table>"
    )


def _news() -> str:
    def row(when: str, title: str, url: str, publisher: str) -> str:
        return (
            f'<tr><td width="130" align="right">{when}</td>'
            '<td align="left"><div class="news-link-container"><div class="news-link-left">'
            f'<a class="tab-link-news" href="{url}" target="_blank" rel="nofollow">'
            f"{title}</a></div>"
            f'<div class="news-link-right"><span>({publisher})</span></div></div></td></tr>'
        )

    body = (
        row("Today 05:25AM", "First headline", "https://example.com/a", "Example News")
        + row("09:00AM", "Second headline", "https://example.com/b", "Blog Source")
        + row("Aug-27-26 04:15PM", "Third headline", "https://example.com/c", "Wire")
        + row("Yesterday 11:30PM", "Fourth headline", "https://example.com/d", "Wire")
    )
    return (
        '<table width="100%" cellpadding="1" cellspacing="0" border="0" '
        'id="news-table" class="fullview-news-outer news-table" data-ticker="AAPL">'
        + body
        + "</table>"
    )


def _insider(reordered: bool = False) -> str:
    heads = [
        "Insider Trading",
        "Relationship",
        "Date",
        "Transaction",
        "Cost",
        "#Shares",
        "Value ($)",
        "#Shares Total",
        "SEC Form 4",
    ]
    order = list(range(len(heads)))
    if reordered:
        # Physically permute two header columns; cells follow their headers.
        order[4], order[5] = 5, 4

    def row(
        owner: str,
        rel: str,
        date: str,
        trans: str,
        cost: str,
        shares: str,
        value: str,
        total: str,
        sec: str,
        filed: str,
    ) -> str:
        cells = [
            f'<td><a href="insidertrading?oc=1">{owner}</a></td>',
            f"<td>{rel}</td>",
            f"<td>{date}</td>",
            f'<td class="transaction"><span>{trans}</span></td>',
            f'<td class="value">{cost}</td>',
            f'<td class="value">{shares}</td>',
            f'<td class="value">{value}</td>',
            f'<td class="value">{total}</td>',
            f'<td><a href="{sec}" target="_blank">{filed}</a></td>',
        ]
        return '<tr class="fv-insider-row">' + "".join(cells[i] for i in order) + "</tr>"

    body = row(
        "Doe Jane",
        "Director",
        "Aug 25 '26",
        "Sale",
        "310.95",
        "1,439",
        "447,457",
        "37,229",
        "http://www.sec.gov/Archives/edgar/data/1/form4.xml",
        "Aug 27 06:30 PM",
    ) + row(
        "Roe John",
        "Chief Executive Officer",
        "Jul 18 '26",
        "Buy",
        "205.10",
        "5,000",
        "1,025,500",
        "120,000",
        "http://www.sec.gov/Archives/edgar/data/1/form5.xml",
        "Jul 21 05:12 PM",
    )
    return (
        '<table cellpadding="0" cellspacing="0" width="100%" '
        'class="body-table styled-table-new"><thead><tr>'
        + "".join(f"<th>{heads[i]}</th>" for i in order)
        + f"</tr></thead>{body}</table>"
    )


def _peers_and_etf() -> str:
    def tickers(entries: list[tuple[str, str, str]]) -> str:
        return "".join(
            f'<span class="inline-flex" data-boxover-ticker="{t}" '
            f'data-boxover-company="{c}" data-boxover-value="{v}">'
            f'<a href="stock?t={t}" class="tab-link">{t}</a></span>'
            for t, c, v in entries
        )

    peers = tickers(
        [
            ("MSFT", "Microsoft Corp", "3739.13B"),
            ("GOOG", "Alphabet Inc", "4168.09B"),
        ]
    )
    etf = tickers(
        [
            ("VTI", "Total Market ETF", "AUM: 688.83B"),
            ("QQQ", "Invesco QQQ Trust", "AUM: 483.26B"),
        ]
    )
    return (
        '<div class="fullview-links">'
        '<a class="tab-link" href="screener?t=MSFT,GOOG">Peers</a>:'
        f"{peers}</div>"
        '<div class="fullview-links">'
        '<a class="tab-link" href="screener?t=VTI,QQQ">Held by</a>:'
        f"{etf}</div>"
    )


def _signals() -> str:
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'class="fullview-links table-fixed"><tbody><tr>'
        '<td class="js-quote-correlation-links-container">'
        '<div class="flex"><div class="flex-1">'
        '<a href="screener.ashx?v=111&s=ta_topgainers" class="tab-link'
        ' sig-tab-link">Top Gainers</a> '
        '<a href="screener.ashx?v=111&s=ta_newhighs" class="tab-link'
        ' sig-tab-link">New High</a> '
        '<a href="screener.ashx?v=111&s=n_majornews" class="tab-link'
        ' sig-tab-link">Major News</a>'
        "</div></div></td></tr></tbody></table>"
    )


def _chart_links() -> str:
    return (
        '<div class="chart-links">'
        '<img src="chart.ashx?t=AAPL&p=d&tkr117=y" alt="AAPL daily chart">'
        '<img src="chart.ashx?t=AAPL&p=w&tkr117=y" alt="AAPL weekly chart">'
        "</div>"
    )


def _header() -> str:
    return (
        '<div class="quote-header"><h1 class="quote-header_ticker-wrapper_ticker" '
        'data-ticker="AAPL">AAPL</h1>'
        '<h2 class="quote-header_ticker-wrapper_company">Sample Technologies Inc</h2>'
        '<div class="quote-price"><strong class="quote-price_price">315.46</strong>'
        '<span class="quote-price_change text-positive">+0.88 (0.28%)</span></div>'
        '<div class="quote-header_categories">'
        '<a href="screener?v=111&f=sec_technology" class="quote-header_category">'
        "Technology</a>"
        '<a href="screener?v=111&f=ind_hardware" class="quote-header_category">'
        "Hardware</a>"
        '<a href="screener?v=111&f=geo_usa" class="quote-header_category">USA</a>'
        '<a href="screener?v=111&f=exch_nasd" class="quote-header_category">NASD</a>'
        "</div></div>"
    )


def _auxiliary_tables() -> list[str]:
    """Six verified non-parser furniture tables that complete the sixteen.

    The live 2026-08 stock page carries sixteen tables total (live-verified
    2026-08-28): six snapshot regions plus signals, ratings, news wrapper,
    insider, the `h-px` profile-divider row, two top ad wrappers (payload
    excluded — ads are scrubbed), the statements-JSON carrier, the news
    wrapper, and the export-links strip. Only the ad payloads are dropped;
    the wrapper structures stay so the page still counts sixteen tables.
    They carry no parser markers, so none may leak into any parsed relation.
    """
    return [
        # Top ad wrapper (live: two nested class-less tables around IC_D_*).
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr><td align="center" valign="top">'
        '<table style="table-layout:fixed" width="100%"><tr>'
        '<td align="center" valign="top"><div class="mt-1"></div></td>'
        "</tr></table></td></tr></table>",
        '<table class="fullview-title"><tr><td>AAPL - Sample Technologies Inc</td></tr></table>',
        '<table class="ticker-links"><tr><td>'
        '<a href="screener.ashx?v=111&amp;f=cap_large">Large Caps</a>: Overview</td></tr></table>',
        '<table class="ticker-links"><tr><td>'
        '<a href="screener.ashx?v=111&amp;f=ta_perf_1w">'
        "1-Week Performers</a>: Roadmap</td></tr></table>",
        '<table class="quote-links"><tr><td>'
        '<a href="http://www.google.com/search?q=AAPL">Google</a></td></tr></table>',
    ]


def _page(tables: list[str], reordered: bool = False) -> str:
    return (
        "<!DOCTYPE html><html><head><title>AAPL - Sample Technologies Inc Stock"
        " Price and Quote</title></head><body>"
        + _header()
        + _auxiliary_tables()[0]
        + _signals()
        + '<div class="screener_snapshot-table-wrapper js-snapshot-table-wrapper">'
        + "".join(tables)
        + "</div>"
        + _ratings()
        + _peers_and_etf()
        + '<div class="quote-news">'
        + _news()
        + "</div>"
        + '<td class="fullview-profile quote_profile"><div class="quote_profile-bio">'
        "Sample Technologies, Inc. engages in the design and sale of imaginary "
        "devices for testing purposes. It operates through the Testing segment."
        "</div></td>"
        + _insider(reordered)
        + "".join(_auxiliary_tables()[1:])
        + _chart_links()
        + "</body></html>"
    )


def write_fixtures() -> tuple[Path, Path]:
    current = HERE / "stock-current.html"
    reordered = HERE / "stock-reordered.html"
    current.write_text(_page(current_tables()), "utf-8")
    reordered.write_text(_page(reordered_tables(), reordered=True), "utf-8")
    return current, reordered


if __name__ == "__main__":
    for path in write_fixtures():
        print(path)
