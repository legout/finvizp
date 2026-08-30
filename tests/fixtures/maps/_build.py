"""Write the scrubbed structural fixture: the map page + hierarchy asset.

Run with ``uv run python -m tests.fixtures.maps._build`` from the repo root.
Mirrors the verified 2026-08-30 public map structure (see
representation-evidence.md); every value is synthetic.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent

# Hierarchy: 2 sectors, 3 industries, 8 leaves (synthetic tickers).
# Leaf shape matches the verified provider data module:
#   {name: <ticker>, description: <company>, value: <market-cap weight>}
# One industry deliberately has no perf for a leaf and the perf payload has
# one perf-only symbol (the verified FOX/GOOG/NWS share-class drift).
SECTORS = [
    (
        "Technology",
        [
            (
                "Software",
                [
                    ("AAA", "Alpha App Corp", 12000),
                    ("BBB", "Beta Bits Inc", 8000),
                    ("CCC", "Gamma Cloud Co", 6000),
                ],
            ),
            (
                "Semiconductors",
                [
                    ("DDD", "Delta Devices Inc", 15000),
                    ("EEE", "Epsilon Chips Co", 9000),
                ],
            ),
        ],
    ),
    (
        "Healthcare",
        [
            (
                "Pharmaceuticals",
                [
                    ("FFF", "Foxtrot Pharma Inc", 11000),
                    ("GGG", "Gamma Genomics Co", 4000),
                ],
            ),
            (
                "Medical Devices",
                [
                    ("HHH", "Hotel Health Inc", 3000),
                ],
            ),
        ],
    ),
]

# Performance payload: subtype/version/hash mirror the verified envelope.
PERF: dict[str, float] = {
    "AAA": 0.51,
    "BBB": -0.29,
    "CCC": 1.22,
    "DDD": 0.0,
    "EEE": 2.51,
    "FFF": -1.05,
    "GGG": 0.31,
    "HHH": 0.75,
    # perf-only share-class variants (provider folds these into the leaf):
    "AAA-P": 0.44,
}


def _perf_json() -> str:
    import json

    nodes = ",".join(f'"{sym}":{perf:g}' for sym, perf in PERF.items())
    return (
        '{"nodes":{' + nodes + '},"additional":{},"subtype":"d1","version":15,'
        '"hash":"0000FIXTURE0000HASH"}'
    )


def _hierarchy_js() -> str:
    parts = []
    for sector, industries in SECTORS:
        ind_parts = []
        for industry, leaves in industries:
            nodes = ",".join(
                f'{{name:"{sym}",description:"{desc}",value:{val}}}' for sym, desc, val in leaves
            )
            ind_parts.append(f'{{name:"{industry}",children:[{nodes}]}}')
        parts.append(f'{{name:"{sector}",children:[{",".join(ind_parts)}]}}')
    return 'e.exports={name:"Root",children:[' + ",".join(parts) + "]}"


CHUNK_NAME = "mapbase.v1.0f1xTURE.js"


def map_page() -> str:
    """The public map page: embedded initialPerf + hierarchy preload link."""
    return (
        "<!DOCTYPE html><html lang=\"en\" class=\"is-map dark\"><head>"
        "<title>S&amp;P 500 Map</title>"
        f'<link rel="preload" as="script" href="/assets/dist/{CHUNK_NAME}" '
        'data-chunk-id="map_base_sec"></head>'
        '<body class="m-0 has-map"><div class="content map">'
        '<div class="fv-container "><div id="root"></div>'
        "<script type=\"text/javascript\">\n"
        "window.addEventListener('Finviz:FinvizInitCanvas', () => window.FinvizInitCanvas({\n"
        "            initData: undefined,\n"
        f"            initialPerf: {_perf_json()},\n"
        "            generate: false,\n"
        "        }), {once: true});\n"
        "</script></div></div>"
        "<div class=\"footer\" style=\"margin-top: 15px;padding-bottom: 115px\">"
        "Stock quotes delayed by 1 minute. Futures and options delayed by 15 minutes."
        "</div></body></html>"
    )


def hierarchy_asset() -> str:
    """The preloaded static data asset (module export assignment)."""
    return (
        "(self.webpackChunkFinvizModern=self.webpackChunkFinvizModern||[])"
        f".push([[8119],{{10163(e){{{_hierarchy_js()}}}}}]);"
    )


def write_fixtures() -> dict[str, Path]:
    out: dict[str, Path] = {}

    def emit(name: str, text: str) -> None:
        path = HERE / name
        path.write_text(text, "utf-8")
        out[name] = path

    emit("sp500-embedded.html", map_page())
    emit("sp500-hierarchy.js", hierarchy_asset())
    return out


if __name__ == "__main__":
    for path in write_fixtures().values():
        print(path)
