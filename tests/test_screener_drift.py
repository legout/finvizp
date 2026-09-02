"""Tests for bounded screener registry drift tooling (0.2-D).

RED-first: every test fails until ``scripts.screener_drift`` exists.
Hermetic: pure diff comparisons use in-memory payload dicts; the live command
path is exercised against a transport double, never live HTTP.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

BASE = "https://finviz.com"


def _payload(**overrides: Any) -> dict[str, Any]:
    """Two minimal registry payloads (live shape) differing in one signal."""
    base: dict[str, Any] = {
        "version": 1,
        "observation_date": "2026-08-30",
        "page_size": 20,
        "filters": [
            {"name": "Exchange", "code": "exch", "type": "categorical", "options": []},
        ],
        "signals": [{"name": "Top Gainers", "code": "ta_topgainers"}],
        "orders": [{"name": "Ticker", "code": "ticker"}],
        "views": [{"name": "overview", "code": "111", "columns": ["No."]}],
        "columns": [{"name": "No.", "code": "0"}],
    }
    for key, value in overrides.items():
        base[key] = value
    return base


# --- 1. pure comparison: added/removed/changed, deterministic -------------------


def test_identical_payloads_report_no_changes() -> None:
    from scripts.screener_drift import compare_registries

    report = compare_registries(_payload(), _payload())
    assert report["unchanged"] is True
    for section in ("filters", "signals", "orders", "views", "columns"):
        assert report[section] == {"added": [], "removed": [], "changed": []}


def test_added_and_removed_entries_are_reported_per_namespace() -> None:
    from scripts.screener_drift import compare_registries

    live = _payload(
        signals=[
            {"name": "Top Gainers", "code": "ta_topgainers"},
            {"name": "New Signal", "code": "ta_newsignal"},
        ],
        orders=[],
    )
    report = compare_registries(_payload(), live)
    assert report["signals"] == {
        "added": [{"name": "New Signal", "code": "ta_newsignal"}],
        "removed": [],
        "changed": [],
    }
    assert report["orders"]["removed"] == [{"name": "Ticker", "code": "ticker"}]
    assert report["unchanged"] is False


def test_changed_code_and_metadata_are_reported() -> None:
    from scripts.screener_drift import compare_registries

    live = _payload(views=[{"name": "overview", "code": "112", "columns": ["No."]}])
    report = compare_registries(_payload(), live)
    assert report["views"]["changed"] == [
        {"name": "overview", "fields": {"code": {"checked_in": "111", "live": "112"}}}
    ]

    live = _payload(views=[{"name": "overview", "code": "111", "columns": ["No.", "Ticker"]}])
    report = compare_registries(_payload(), live)
    assert report["views"]["changed"] == [
        {
            "name": "overview",
            "fields": {"columns": {"checked_in": ["No."], "live": ["No.", "Ticker"]}},
        }
    ]


def test_filter_option_changes_are_reported() -> None:
    from scripts.screener_drift import compare_registries

    checked_in = _payload(
        filters=[
            {
                "name": "Exchange",
                "code": "exch",
                "type": "categorical",
                "options": [{"name": "NYSE", "code": "nyse"}],
            }
        ]
    )
    live = _payload(
        filters=[
            {
                "name": "Exchange",
                "code": "exch",
                "type": "categorical",
                "options": [{"name": "NYSE", "code": "nyse2"}],
            }
        ]
    )
    report = compare_registries(checked_in, live)
    assert report["filters"]["changed"] == [
        {
            "name": "Exchange",
            "fields": {
                "options": {
                    "checked_in": [{"name": "NYSE", "code": "nyse"}],
                    "live": [{"name": "NYSE", "code": "nyse2"}],
                }
            },
        }
    ]


def test_comparison_is_deterministic_and_order_insensitive() -> None:
    from scripts.screener_drift import compare_registries

    live = _payload(
        signals=[
            {"name": "Z Signal", "code": "z_signal"},
            {"name": "A Signal", "code": "a_signal"},
        ]
    )
    shuffled = _payload(
        signals=[
            {"name": "A Signal", "code": "a_signal"},
            {"name": "Z Signal", "code": "z_signal"},
        ]
    )
    one = compare_registries(_payload(), live)
    two = compare_registries(_payload(), shuffled)
    assert one == two
    names = [entry["name"] for entry in one["signals"]["added"]]
    assert names == sorted(names)
    assert compare_registries(_payload(), _payload()) == compare_registries(_payload(), _payload())


def test_filter_grammar_violations_are_reported_not_raised() -> None:
    """Codes the checked-in grammar would reject are drift evidence, not crashes."""
    from scripts.screener_drift import compare_registries

    live = _payload(
        orders=[
            {"name": "Ticker", "code": "ticker"},
            {"name": "Bad Grammar", "code": "Bad Code!"},
        ]
    )
    report = compare_registries(_payload(), live)
    assert report["orders"]["added"] == [{"name": "Bad Grammar", "code": "Bad Code!"}]


# --- 2. explicit opt-in command: bounded requests, never mutates -----------------


def _v151_html() -> str:
    return (
        "<html><head><title>Stock Screener</title></head><body>"
        '<div id="screener-total" class="count-text">#1 / 7 Total</div>'
        '<select id="orderSelect">'
        '<option value="screener?v=152&o=ticker">Ticker</option>'
        '<option value="screener?v=152&o=marketcap">Market Cap.</option>'
        "</select>"
        '<select id="signalSelect">'
        '<option value="screener?v=152&s=ta_topgainers">Top Gainers</option>'
        "</select>"
        '<select id="fs_exch" data-filter="exch">'
        '<option value="">Any</option><option value="nyse">NYSE</option>'
        "</select>"
        "</body></html>"
    )


class _DriftTransport(Backend):
    """Serves one scrubbed provider-shaped metadata page per request."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    @property
    def name(self) -> str:
        return "drift-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.urls.append(str(config.url))
        return NormalizedResponse.from_backend(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=_v151_html().encode(),
            url=str(config.url),
            is_json=False,
        )

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> _DriftTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _client(fake: _DriftTransport, **kwargs: Any):
    kwargs.setdefault("retry_attempts", 0)
    kwargs.setdefault("retry_backoff", 0.0)
    from finvizp.client import FinvizClient

    return FinvizClient(transport=fake, **kwargs)


async def test_collect_observations_is_bounded_and_safe() -> None:
    from scripts.screener_drift import OBSERVATION_PATHS, collect_observations

    fake = _DriftTransport()
    observations = await collect_observations(client=_client(fake))
    # Bounded: exactly the reviewed observation requests, nothing else.
    assert len(fake.urls) == len(OBSERVATION_PATHS)
    assert all(urlsplit(url).hostname == "finviz.com" for url in fake.urls)
    assert observations["orders"] == {"ticker": "Ticker", "marketcap": "Market Cap."}
    assert observations["signals"] == {"ta_topgainers": "Top Gainers"}


def test_live_report_requires_explicit_opt_in_flag() -> None:
    from scripts.screener_drift import build_live_report

    with pytest.raises(TypeError):
        build_live_report()  # live access must be an explicit keyword argument
    with pytest.raises(ValueError, match="live"):
        build_live_report(live="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="observations"):
        build_live_report(live=True, observations={})


def test_live_report_never_mutates_the_registry(tmp_path) -> None:
    """The report goes where the caller points it; the package registry is read-only."""
    from pathlib import Path

    from finvizp._queries import screener as qs
    from scripts import screener_drift

    registry_path = Path(str(qs._registry_path()))
    before = registry_path.read_text("utf-8")
    out = tmp_path / "drift.json"
    report = screener_drift.build_live_report(
        live=False, observations={"orders": {"ticker": "Ticker"}}, out_path=out
    )
    assert out.read_text("utf-8") == report
    assert registry_path.read_text("utf-8") == before
    assert json.loads(report)["meta"]["registry_mutated"] is False


def test_report_json_is_reviewable_and_ordered() -> None:
    from scripts.screener_drift import build_live_report

    text = build_live_report(live=False, observations={"orders": {}})
    data = json.loads(text)
    assert set(data) == {"meta", "report"}
    assert data["meta"]["observation_date"]
    assert data["meta"]["registry_version"] >= 1
    assert data["meta"]["registry_mutated"] is False
    assert data["report"]["unchanged"] is True
    # Deterministic bytes: same inputs, same output.
    assert text == build_live_report(live=False, observations={"orders": {}})


def test_report_carries_no_secrets_or_raw_bodies() -> None:
    from scripts.screener_drift import build_live_report

    text = build_live_report(
        live=False,
        observations={"orders": {"ticker": "session=abc; <html>body</html>"}},
    )
    lowered = text.lower()
    assert "cookie" not in lowered
    assert "proxy" not in lowered
    assert "<html" not in lowered
    assert "session=abc" not in lowered


def test_report_emits_added_live_entries(tmp_path) -> None:
    """A live entry absent from the registry shows up as reviewable added drift."""
    from scripts.screener_drift import build_live_report

    text = build_live_report(
        live=False,
        observations={"signals": {"ta_topgainers": "Top Gainers", "zz_new": "Brand New"}},
    )
    report = json.loads(text)["report"]
    assert report["signals"]["added"] == [{"name": "Brand New", "code": "zz_new"}]


def test_registry_module_exposes_no_public_surface() -> None:
    """Drift tooling stays out of the package's public namespace."""
    import finvizp

    assert not hasattr(finvizp, "screener_drift")
