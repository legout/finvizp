"""Write the scrubbed structural fixtures for the 0.4 markets card.

Run with ``uv run python -m tests.fixtures.markets._build`` from the repo
root. Mirrors the verified 2026-08-30 public forex/crypto page structures
(see representation-evidence.md); every value is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent

DELAY_FOOTER = (
    '<div class="footer" style="margin-top: 15px;padding-bottom: 115px">'
    "Stock quotes delayed by 1 minute. Futures and options delayed by 15 minutes."
    "</div>"
)

# --- performance tables ----------------------------------------------------------

# Verified columns (2026-08-30 live probe): No./Pair/Price + Perf 5Min..Year for
# forex; crypto adds Ticker and Name between No. and Price.
FOREX_HEADERS = [
    "No.",
    "Pair",
    "Price",
    "Perf 5Min",
    "Perf Hour",
    "Perf Day",
    "Perf Week",
    "Perf Month",
    "Perf Quart",
    "Perf Half",
    "Perf YTD",
    "Perf Year",
]
CRYPTO_HEADERS = ["No.", "Ticker", "Name", "Price", *FOREX_HEADERS[3:]]

# pair, price display, then ten percent displays (5min/hour/day/week/month/quart/half/ytd/year)
FOREX_ROWS = [
    ("BTC/USD", "78900.1000", ["0.03%", "-0.11%", "1.02%", "1.02%", "22.72%", "6.62%", "17.23%", "-9.77%", "-27.73%"]),
    ("USD/CHF", "0.8091", ["-0.02%", "-0.00%", "0.62%", "1.01%", "-1.16%", "3.22%", "5.35%", "2.02%", "0.97%"]),
    ("USD/JPY", "160.08", ["-0.01%", "-0.00%", "0.43%", "0.70%", "-2.30%", "0.53%", "2.61%", "2.06%", "8.95%"]),
    ("USD/CAD", "1.3925", ["0.01%", "0.02%", "0.31%", "0.44%", "-0.85%", "1.12%", "2.03%", "1.48%", "3.11%"]),
    ("GBP/JPY", "199.44", ["0.05%", "0.10%", "0.90%", "1.35%", "-3.10%", "1.01%", "4.02%", "2.55%", "6.10%"]),
    ("EUR/GBP", "0.8312", ["-0.01%", "-0.02%", "0.11%", "0.21%", "-0.44%", "0.88%", "1.90%", "1.21%", "2.44%"]),
    ("GBP/USD", "1.2681", ["0.02%", "0.04%", "0.52%", "0.81%", "-1.90%", "2.11%", "3.44%", "2.02%", "4.55%"]),
    ("AUD/USD", "0.6511", ["-0.03%", "-0.05%", "0.22%", "0.35%", "-1.10%", "0.91%", "2.30%", "1.55%", "3.02%"]),
    ("EUR/USD", "1.0518", ["0.01%", "0.01%", "0.33%", "0.55%", "-1.32%", "1.75%", "2.85%", "2.10%", "4.01%"]),
    ("NZD/USD", "0.5888", ["-0.02%", "-0.03%", "0.18%", "0.29%", "-0.98%", "0.72%", "1.99%", "1.33%", "2.88%"]),
]

# ticker, name, price display, then ten percent displays
CRYPTO_ROWS = [
    ("UNI", "Uniswap", "5.3980", ["0.11%", "-0.70%", "10.10%", "10.10%", "23.50%", "76.46%", "40.79%", "-4.32%", "-45.55%"]),
    ("ZEC", "Zcash", "868.0500", ["0.09%", "-0.19%", "4.42%", "4.42%", "88.52%", "59.75%", "275.05%", "70.43%", "2037.53%"]),
    ("BCH", "Bitcoin Cash", "253.2700", ["0.12%", "-0.17%", "3.41%", "3.41%", "18.30%", "-16.53%", "-44.84%", "-57.37%", "-54.03%"]),
    ("DOT", "Polkadot", "2.7100", ["0.05%", "-0.11%", "1.90%", "1.90%", "9.20%", "-8.10%", "-21.05%", "-31.22%", "-40.10%"]),
    ("BTC", "Bitcoin", "84520.1000", ["0.04%", "-0.09%", "1.10%", "1.10%", "12.30%", "4.10%", "9.90%", "-3.20%", "-18.40%"]),
    ("ETH", "Ethereum", "2810.5500", ["0.07%", "-0.12%", "1.70%", "1.70%", "15.10%", "6.20%", "12.40%", "-1.90%", "-12.60%"]),
    ("SOL", "Solana", "141.2200", ["0.09%", "-0.15%", "2.40%", "2.40%", "19.80%", "9.10%", "18.20%", "-6.10%", "-24.30%"]),
    ("XRP", "Ripple", "2.0500", ["0.03%", "-0.08%", "1.30%", "1.30%", "8.60%", "3.20%", "7.80%", "-2.40%", "-15.10%"]),
    ("ADA", "Cardano", "0.4120", ["0.02%", "-0.06%", "1.00%", "1.00%", "7.40%", "2.10%", "5.60%", "-4.80%", "-20.20%"]),
    ("DOGE", "Dogecoin", "0.1520", ["0.06%", "-0.14%", "2.90%", "2.90%", "16.40%", "11.20%", "21.30%", "-8.90%", "-30.50%"]),
]


def _perf_table(headers: list[str], rows: list[tuple], *, div_id: str, title: str) -> str:
    head = "".join(f"<th>{label}</th>" for label in headers)
    body_rows = []
    for index, row in enumerate(rows, start=1):
        cells = [f'<td height="10" align="right">{index}</td>']
        if "Ticker" in headers:
            ticker, name, price, perfs = row
            cells.append(
                f'<td height="10" align="left"><a href="crypto?t={ticker}" '
                f'class="tab-link">{ticker}</a></td>'
            )
            cells.append(f'<td height="10" align="left">{name}</td>')
            cells.append(
                f'<td height="10" align="right"><span class="color-text is-positive">{price}</span></td>'
            )
            cells.extend(
                f'<td height="10" align="right"><span class="color-text is-positive">{perf}</span></td>'
                for perf in perfs
            )
        else:
            pair, price, perfs = row
            symbol = pair.replace("/", "")
            cells.append(
                f'<td height="10" align="left"><a href="forex?t={symbol}" '
                f'class="tab-link">{pair}</a></td>'
            )
            cells.append(
                f'<td height="10" align="right"><span class="color-text is-positive">{price}</span></td>'
            )
            cells.extend(
                f'<td height="10" align="right"><span class="color-text is-positive">{perf}</span></td>'
                for perf in perfs
            )
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        f"<title>{title}</title></head><body>"
        f'<div id="{div_id}">'
        '<table class="styled-table-new is-medium is-rounded is-tabular-nums '
        'w-full mx-auto groups_table" style="max-width:1264px">'
        f"<thead><tr align=\"center\" valign=\"middle\">{head}</tr></thead>"
        + "".join(body_rows)
        + "</table></div>"
        + DELAY_FOOTER
        + "</body></html>"
    )


# --- tile pages ------------------------------------------------------------------


def _tiles(
    event: str, entries: list[dict], *, title: str
) -> str:
    payload = json.dumps({entry["ticker"]: entry for entry in entries}, separators=(",", ":"))
    return (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        f"<title>{title}</title></head><body>"
        '<div class="content"><div class="tiles">'
        '<script type="text/javascript">\n'
        f"window.addEventListener('Finviz:{event}', () => window.{event}(\\n{payload}\\n), {{once: true}});\\n"
        "</script>"
        "</div></div>"
        + DELAY_FOOTER
        + "</body></html>"
    )


def _tile(ticker: str, label: str, last: float, change: float, change_usd: float,
          prev: float, high: float, low: float, spark: list[float]) -> dict:
    return {
        "label": label,
        "ticker": ticker,
        "last": last,
        "change": change,
        "changeUsd": change_usd,
        "prevClose": prev,
        "high": high,
        "low": low,
        "sparkline": spark,
        "sparklineDateChanges": {"288": "4PM", "216": "10AM", "144": "4AM", "72": "10PM"},
    }


def _spark(start: float, step: float) -> list[float]:
    """Ten verbatim points; the provider payload keeps no per-point timestamps."""
    return [round(start + step * index, 5) for index in range(10)]


FOREX_TILES = [
    _tile("AUDUSD", "AUD/USD", 0.71591, -0.46, -0.0033, 0.71925, 0.72079, 0.71554, _spark(0.71953, 0.00001)),
    _tile("EURUSD", "EUR/USD", 1.05180, 0.33, 0.0035, 1.04830, 1.05340, 1.04710, _spark(1.04831, 0.00002)),
    _tile("BTCUSD", "BTC/USD", 78900.1, 1.02, 796.5, 78103.6, 79120.0, 77890.5, _spark(78103.6, 8.5)),
]

CRYPTO_TILES = [
    _tile("AAVEUSD", "AAVE/USD", 128.16, 1.79, 2.25, 125.91, 130.01, 123.23, _spark(123.93, 0.08)),
    _tile("BTCUSD", "BTC/USD", 84520.1, 1.10, 918.4, 83601.7, 84990.0, 83320.1, _spark(83601.7, 10.2)),
    _tile("ETHUSD", "ETH/USD", 2810.55, 1.70, 46.9, 2763.65, 2830.0, 2755.2, _spark(2763.65, 0.9)),
]


# --- chart gallery ---------------------------------------------------------------


def _charts(*, kind: str, title: str, pairs: list[tuple[str, str]]) -> str:
    images = []
    for symbol, label in pairs:
        url = (
            f"https://charts2-node.finviz.com/chart?w=324&amp;h=219&amp;bw=2&amp;bm=1&amp;"
            f"bb=1&amp;t=@{symbol.lower()}&amp;tf=d&amp;s=linear&amp;pm=0&amp;am=0&amp;"
            f"ct=candle_stick&amp;tm=d&amp;r="
        )
        images.append(
            f'<img class="charts-gal"  srcset="{url} 1x, {url}&amp;sf=2 2x" '
            f'width="324" height="219" alt="{label} Chart Daily" loading="lazy">'
        )
    return (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        f"<title>{title}</title></head><body>"
        f'<div class="charts" id="{kind}-charts">'
        + "".join(images)
        + "</div>"
        + DELAY_FOOTER
        + "</body></html>"
    )


def write_fixtures() -> dict[str, Path]:
    out: dict[str, Path] = {}

    def emit(name: str, text: str) -> None:
        path = HERE / name
        path.write_text(text, "utf-8")
        out[name] = path

    emit(
        "forex-performance.html",
        _perf_table(FOREX_HEADERS, FOREX_ROWS, div_id="forex_performance", title="Forex Performance"),
    )
    emit(
        "crypto-performance.html",
        _perf_table(CRYPTO_HEADERS, CRYPTO_ROWS, div_id="crypto_performance", title="Crypto Performance"),
    )
    emit("forex-tiles.html", _tiles("FinvizInitForex", FOREX_TILES, title="Forex Prices"))
    emit("crypto-tiles.html", _tiles("FinvizInitCrypto", CRYPTO_TILES, title="Crypto Prices"))
    emit(
        "forex-charts.html",
        _charts(
            kind="forex",
            title="Forex Charts Daily",
            pairs=[("EURUSD", "EUR/USD"), ("GBPUSD", "GBP/USD"), ("USDJPY", "USD/JPY")],
        ),
    )
    emit(
        "crypto-charts.html",
        _charts(
            kind="crypto",
            title="Crypto Charts Daily",
            pairs=[("BTCUSD", "BTC/USD"), ("ETHUSD", "ETH/USD")],
        ),
    )
    return out


if __name__ == "__main__":
    for path in write_fixtures().values():
        print(path)
