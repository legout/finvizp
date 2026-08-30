"""Build scrubbed structural insider-feed fixtures (global/fund/manager).

Run with ``uv run python -m tests.fixtures.insider._build`` from the repo root.
Emits three hand-shaped documents representing the live structures verified
2026-08-30 through the planned transport (bounded one-request probes; no live
HTML copied, every value synthetic):

- ``global.html``: the ``/insidertrading.ashx`` page whose ``insider-table``
  carries Ticker/Owner/Relationship/Date/Transaction/Cost/#Shares/Value ($)/
  #Shares Total/SEC Form 4 columns with ticker anchors, owner feeds, and SEC
  form links;
- ``fund.html``: an N-PORT fund page whose entire contract is the embedded
  ``initialFundDetailsData``/``initialFundReportDates`` JSON (no HTML tables);
- ``manager.html``: the 13F manager twin with ``initialManagerDetailsData``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent

PAGE = (
    "<!DOCTYPE html><html><head><title>{title} - Insider Transactions - Form 4</title>"
    "</head><body>{table}</body></html>"
)

_HEADS = [
    "Ticker",
    "Owner",
    "Relationship",
    "Date",
    "Transaction",
    "Cost",
    "#Shares",
    "Value ($)",
    "#Shares Total",
    "SEC Form 4",
]


def _row(
    ticker: str,
    owner: str,
    relationship: str,
    date: str,
    transaction: str,
    cost: str,
    shares: str,
    value: str,
    total: str,
    sec_url: str,
    filed: str,
) -> str:
    return (
        '<tr class="fv-insider-row is-buy-1 cursor-pointer">'
        f'<td data-boxover-ticker="{ticker}" data-boxover-company="Sample {ticker} Inc">'
        f'<a href="stock?t={ticker}&amp;b=2">{ticker}</a></td>'
        f'<td><a href="insidertrading?oc=1260030&amp;tc=7&amp;b=2">{owner}</a></td>'
        f"<td>{relationship}</td>"
        f"<td>{date}</td>"
        f'<td class="transaction"><span>{transaction}</span></td>'
        f'<td class="value">{cost}</td>'
        f'<td class="value">{shares}</td>'
        f'<td class="value">{value}</td>'
        f'<td class="value">{total}</td>'
        f'<td><a href="{sec_url}" target="_blank" rel="nofollow">{filed}</a></td>'
        "</tr>"
    )


_ROWS = (
    _row(
        "VTSI",
        "BARBER GRANT",
        "Director",
        "Aug 27 '26",
        "Buy",
        "3.05",
        "2,000",
        "6,100",
        "23,042",
        "http://www.sec.gov/Archives/edgar/data/1/form4-a.xml",
        "Aug 28 09:51 PM",
    )
    + _row(
        "SNTH",
        "SUBIN NEIL S",
        "Chief Executive Officer",
        "Jul 18 '26",
        "Sale",
        "12.40",
        "5,000",
        "62,000",
        "120,000",
        "http://www.sec.gov/Archives/edgar/data/2/form4-b.xml",
        "Jul 21 05:12 PM",
    )
    + _row(
        "FESK",
        "Fesko John",
        "PRESIDENT",
        "Aug 10 '26",
        "Sale",
        "321.53",
        "6,517",
        "2,095,411",
        "197,257",
        "http://www.sec.gov/Archives/edgar/data/3/form4-c.xml",
        "Aug 28 09:35 PM",
    )
)


def write_global() -> Path:
    table = (
        '<table id="insider-table" class="styled-table-new is-rounded is-condensed '
        'mt-2 w-full table-fixed"><thead><tr>'
        + "".join(f"<th>{head}</th>" for head in _HEADS)
        + f"</tr></thead><tbody>{_ROWS}</tbody></table>"
    )
    path = HERE / "global.html"
    path.write_text(PAGE.format(title="Latest Insider Trading", table=table), "utf-8")
    return path


def _top(ticker: str, name: str, value: int, put_call: str, **boxover: Any) -> dict:
    box = {k.rstrip("_"): v for k, v in boxover.items()}
    return {
        "ticker": ticker,
        "name": name,
        "value": value,
        "putCall": put_call,
        "boxoverData": box,
    }


_ALLOCATIONS = [
    {"quarter": "Q2 2026", "pctAllocation": 99.47, "usdAllocation": 3282172197},
    {"quarter": "Q1 2026", "pctAllocation": 98.11, "usdAllocation": 2854416081},
]
_SECTOR_ALLOCATIONS = [
    {
        "sector": "Manufacturing",
        "quarter": "Q2 2026",
        "pctAllocation": 24.33,
        "usdAllocation": 237558650,
    },
    {"sector": "Finance", "quarter": "Q2 2026", "pctAllocation": 18.02, "usdAllocation": 175948000},
]


def _page(payload: dict, title: str) -> str:
    return (
        "<!DOCTYPE html><html><head><title>"
        + title
        + "</title></head><body><script>"
        + json.dumps(payload)
        + "</script></body></html>"
    )


def _summary(investor_id: str, **overrides: int) -> dict:
    summary = {
        "investorId": investor_id,
        "reportDate": "2026-06-30",
        "totalCountOfInvestments": 107,
        "totalSumOfUsdValue": 3297262771,
        "totalSumOfUsdValuePrevQ": 2854416081,
        "countOfNewPurchased": 21,
        "countOfSoldOut": 15,
        "countOfAddedTo": 12,
        "countOfReduced": 27,
        "percOfPortfolioOfTop10": 40.24,
        "turnoverPerc": 33.64,
        "timeHeldTop10": 15.3,
    }
    summary.update(overrides)
    return summary


def write_fund() -> Path:
    payload = {
        "initialFundDetailsData": {
            "countryAllocations": _ALLOCATIONS,
            "filer": {
                "investorId": "0000002230",
                "investorType": "nport_fund",
                "reportDate": "2026-06-30",
                "filerCik": 2230,
                "filerTicker": "ADX",
                "filerName": "SAMPLE DIVERSIFIED EQUITY FUND, INC.",
                "countOfOtherManagers": 0,
                "otherManagerCik": None,
                "otherManagerCleanedName": None,
                "otherManagerName": None,
                "seriesId": "0000002230",
                "seriesLei": None,
                "seriesName": None,
                "slug": "na-0000002230",
            },
            "latestSummary": _summary("0000002230"),
            "topBuysUsd": [
                _top(
                    "AMD",
                    "Sample Micro Devices Inc",
                    48317440,
                    "share",
                    ticker_="AMD",
                    country="USA",
                    company="Sample Micro Devices Inc",
                    industry="Semiconductors",
                    marketCap=760047.68,
                ),
            ],
            "topSellsUsd": [
                _top(
                    "XLV",
                    "Sample Health Care Select ETF",
                    -41783850,
                    "share",
                    ticker_="XLV",
                    country="USA",
                    company="Sample Health Care ETF",
                    industry="Exchange Traded Fund",
                    aum=44197.48,
                ),
            ],
            "topBuysPct": [],
            "topSellsPct": [],
            "mapData": {"name": "root", "children": [], "duplicateTickers": [], "hash": "x"},
        },
        "initialFundReportDates": ["2026-06-30", "2026-03-31", "2025-12-31"],
    }
    path = HERE / "fund.html"
    path.write_text(
        _page(payload, "SAMPLE DIVERSIFIED EQUITY FUND, INC. N-PORT Portfolio"), "utf-8"
    )
    return path


def write_manager() -> Path:
    payload = {
        "initialManagerDetailsData": {
            "individualManagers": [],
            "sectorAllocations": _SECTOR_ALLOCATIONS,
            "filer": {
                "investorId": "1000097",
                "investorType": "manager_13f",
                "reportDate": "2026-06-30",
                "filerCik": 1000097,
                "filerTicker": None,
                "filerName": "KINGDOM SAMPLE MANAGEMENT, L.L.C.",
                "countOfOtherManagers": 0,
                "otherManagerCik": None,
                "otherManagerCleanedName": None,
                "otherManagerName": None,
                "seriesId": None,
                "seriesLei": None,
                "seriesName": None,
                "slug": "kingdon-capital-management-llc-1000097",
            },
            "latestSummary": _summary(
                "1000097", totalCountOfInvestments=74, totalSumOfUsdValue=976167326
            ),
            "topBuysUsd": [
                _top(
                    "SOXX",
                    "Sample Semiconductor ETF",
                    83298800,
                    "put",
                    ticker_="SOXX",
                    country="USA",
                    company="Sample Semiconductor ETF",
                    industry="Exchange Traded Fund",
                    aum=41590.25,
                ),
            ],
            "topSellsUsd": [
                _top(
                    "SPY",
                    "Sample S&P 500 ETF",
                    -185346900,
                    "put",
                    ticker_="SPY",
                    country="USA",
                    company="Sample S&P 500 ETF",
                    industry="Exchange Traded Fund",
                    aum=814409.64,
                ),
            ],
            "topBuysPct": [],
            "topSellsPct": [],
            "mapData": {"name": "root", "children": [], "duplicateTickers": [], "hash": "y"},
        },
        "initialManagerReportDates": ["2026-06-30", "2026-03-31", "2025-12-31"],
    }
    path = HERE / "manager.html"
    path.write_text(_page(payload, "KINGDOM SAMPLE MANAGEMENT, L.L.C. - 13F Portfolio"), "utf-8")
    return path


if __name__ == "__main__":
    for path in (write_global(), write_fund(), write_manager()):
        print(path)
