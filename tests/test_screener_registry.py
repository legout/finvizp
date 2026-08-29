"""Tests for the checked-in screener provider-code registry."""

from __future__ import annotations

import datetime as dt
import re

import pytest

from finvizp._queries.screener import screener_registry
from finvizp.errors import FinvizDataError

CODE_GRAMMAR = re.compile(r"^[a-z0-9]+(?:[-_.][a-z0-9]+)*$")


def test_registry_loads_with_version_and_observation_date() -> None:
    reg = screener_registry()
    assert reg.version >= 1
    dt.date.fromisoformat(reg.observation_date)
    assert reg.page_size >= 1


def test_registry_names_unique_per_namespace() -> None:
    reg = screener_registry()
    for names in (
        reg.filters.keys(),
        reg.signals.keys(),
        reg.orders.keys(),
        reg.views.keys(),
        reg.columns.keys(),
    ):
        assert len(names) == len(set(names))


def test_registry_provider_codes_unique_per_namespace() -> None:
    reg = screener_registry()
    assert len({f.code for f in reg.filters.values()}) == len(reg.filters)
    assert len({s.code for s in reg.signals.values()}) == len(reg.signals)
    assert len({o.code for o in reg.orders.values()}) == len(reg.orders)
    assert len({v.code for v in reg.views.values()}) == len(reg.views)
    assert len({c.code for c in reg.columns.values()}) == len(reg.columns)


def test_registry_codes_match_grammar() -> None:
    reg = screener_registry()
    for spec in reg.filters.values():
        assert CODE_GRAMMAR.match(spec.code), spec.name
        for option in spec.options:
            assert CODE_GRAMMAR.match(option.code), (spec.name, option.name)
            assert option.name
    for spec in [*reg.signals.values(), *reg.orders.values()]:
        assert CODE_GRAMMAR.match(spec.code), spec.name
    for spec in reg.views.values():
        assert re.match(r"^[0-9]+$", spec.code), spec.name
    for spec in reg.columns.values():
        assert re.match(r"^[0-9]+$", spec.code), spec.name


def test_filter_types_in_vocabulary() -> None:
    reg = screener_registry()
    vocabulary = {"categorical", "numeric", "date"}
    for spec in reg.filters.values():
        assert spec.type in vocabulary, spec.name


def test_every_filter_has_options_with_unique_codes() -> None:
    reg = screener_registry()
    for spec in reg.filters.values():
        assert len(spec.options) >= 2, spec.name
        codes = [option.code for option in spec.options]
        assert len(codes) == len(set(codes)), spec.name
        names = [option.name for option in spec.options]
        assert len(names) == len(set(names)), spec.name


def test_fixed_views_declare_columns_custom_and_ticker_do_not() -> None:
    reg = screener_registry()
    fixed = {"overview", "valuation", "financial", "ownership", "performance", "technical"}
    for name in fixed:
        assert reg.views[name].columns, name
    for name in ("custom", "ticker"):
        assert reg.views[name].columns == (), name


def test_registry_rejects_duplicate_names(tmp_path, monkeypatch) -> None:
    import finvizp._queries.screener as qs

    payload = """
    {"version": 1, "observation_date": "2026-08-30", "page_size": 20,
     "filters": [
       {"name": "A", "code": "aa", "type": "categorical", "options": [{"name": "x", "code": "1"}]},
       {"name": "A", "code": "bb", "type": "categorical", "options": [{"name": "x", "code": "1"}]}
     ],
     "signals": [], "orders": [], "views": [], "columns": []}
    """
    fake = tmp_path / "screener_registry.json"
    fake.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(qs, "_registry_path", lambda: fake)
    with pytest.raises(FinvizDataError, match="duplicate filter name"):
        qs.screener_registry.__wrapped__()
