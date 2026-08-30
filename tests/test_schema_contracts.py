"""Full/partial/empty table contracts for every registered dataset (Card 0.5-B).

Every capability-referenced schema must return its registered schema for a
complete row, a partial row (every optional field omitted), and an empty
result — the shape a caller sees never depends on how much data the provider
returned. Non-null required fields are data-mined from the registry so a new
required column breaks this test loudly instead of silently redefining
"complete". Registered keys are non-null on a full row and present on partial
rows; ``strict_schema=True`` rejects a required-field violation on any row
shape.
"""

from __future__ import annotations

import datetime as dt
import json
from importlib import resources
from pathlib import Path

import pytest

from finvizp import arrow as fa
from finvizp import schemas
from finvizp.errors import FinvizDataError

NOW = dt.datetime(2026, 8, 27, 14, 30, tzinfo=dt.UTC)
RESPONSE_DATE = dt.date(2026, 8, 27)

# One representative display per semantic unit; the builder owns conversion.
UNIT_SAMPLE = {
    "text": "x",
    "count": "3",
    "number": "1.5",
    "compact": "2.5B",
    "percent": "3.25%",
    "date": "2026-08-15",
    "timestamp": "09:30",
}

_REG_PAYLOAD = json.loads(
    resources.files("finvizp").joinpath("schema_registry.json").read_text("utf-8")
)


def _capability_datasets() -> tuple[str, ...]:
    """Dataset names some capability manifest ``schema`` reference points at."""
    caps = json.loads(Path("src/finvizp/capabilities.json").read_text("utf-8"))
    referenced = {name for cap in caps["capabilities"] for name in cap.get("schema", [])}
    return tuple(sorted(referenced & set(schemas.dataset_names())))


def _complete_row(dataset_name: str) -> dict[str, object]:
    """Minimal source row the registered contract calls complete."""
    row: dict[str, object] = {"symbol": "AAPL"}
    for field in schemas.dataset(dataset_name).fields:
        if field.name in ("symbol", "fetched_at") or field.unit in ("map", "raw"):
            continue
        if field.nullable:
            continue
        row[field.name] = UNIT_SAMPLE[field.unit]
    return row


@pytest.mark.parametrize("dataset_name", _capability_datasets())
def test_complete_partial_empty_share_registered_schema(dataset_name: str) -> None:
    """Complete, partial (optionals omitted), and empty rows share one schema.

    Required non-key fields cannot be dropped from a partial row (the builder
    raises), so "partial" here means exactly what a partial provider response
    can be: every optional column absent, keys and required columns present.
    """
    ds = schemas.dataset(dataset_name)
    row = _complete_row(dataset_name)
    key_names = [f.name for f in ds.fields if f.key]
    # extra_fields/fetched_at are builder-derived; *_raw mirrors its base field.
    partial = {
        **row,
        **{
            f.name: None
            for f in ds.fields
            if f.nullable
            and f.name not in ("extra_fields", "fetched_at")
            and not f.name.endswith(("_raw", "_status"))
        },
    }

    complete = fa.build_table(dataset_name, [row], fetched_at=NOW, response_date=RESPONSE_DATE)
    partial_table = fa.build_table(
        dataset_name, [partial], fetched_at=NOW, response_date=RESPONSE_DATE
    )
    empty = fa.build_table(dataset_name, [], fetched_at=NOW)

    expected = schemas.arrow_schema(dataset_name)
    for table in (complete, partial_table, empty):
        assert table.schema.equals(expected), dataset_name
    assert complete.num_rows == 1
    assert partial_table.num_rows == 1
    assert empty.num_rows == 0
    # Keys survive every shape; optionals null out on the partial row.
    d = partial_table.to_pydict()
    for name in key_names:
        assert d[name][0] == "AAPL" if name == "symbol" else d[name][0], dataset_name
    for field in ds.fields:
        if (
            field.nullable
            and field.unit not in ("map", "raw")
            and not field.name.endswith(("_raw", "_status"))
        ):
            assert d[field.name][0] is None, (dataset_name, field.name)


@pytest.mark.parametrize("dataset_name", _capability_datasets())
def test_missing_required_field_is_typed_error(dataset_name: str) -> None:
    """A required column absent (or sentinel-nulled) is a contract violation."""
    row = _complete_row(dataset_name)
    required = [
        f.name
        for f in schemas.dataset(dataset_name).fields
        if not f.nullable and f.name not in ("symbol", "fetched_at")
    ]
    if not required:
        return
    for drop in required:
        broken = {k: v for k, v in row.items() if k != drop}
        with pytest.raises(FinvizDataError, match=drop):
            fa.build_table(dataset_name, [broken], fetched_at=NOW)


@pytest.mark.parametrize("dataset_name", _capability_datasets())
def test_key_columns_survive_partial_rows(dataset_name: str) -> None:
    """Registered keys are non-null on a full row and survive partial rows."""
    ds = schemas.dataset(dataset_name)
    key_names = [f.name for f in ds.fields if f.key]
    row = _complete_row(dataset_name)
    partial = {
        **row,
        **{
            f.name: None
            for f in ds.fields
            if f.nullable
            and f.name not in ("extra_fields", "fetched_at")
            and not f.name.endswith(("_raw", "_status"))
        },
    }
    table = fa.build_table(dataset_name, [partial], fetched_at=NOW, response_date=RESPONSE_DATE)
    columns = table.to_pydict()
    for name in key_names:
        values = [v for v in columns[name] if v is not None]
        assert values == ["AAPL"] if name == "symbol" else values, (dataset_name, name)


def test_registry_covers_every_capability_schema_reference() -> None:
    for name in _capability_datasets():
        assert name in schemas.dataset_names(), name


def test_every_registered_dataset_is_capability_referenced() -> None:
    """No orphan schemas: the registry and the manifest stay in lockstep."""
    caps = json.loads(Path("src/finvizp/capabilities.json").read_text("utf-8"))
    referenced = {name for cap in caps["capabilities"] for name in cap.get("schema", [])}
    orphans = set(schemas.dataset_names()) - referenced
    assert orphans == set()


def test_registry_versions_unchanged_without_bump() -> None:
    """Guard against accidental version bumps; a real change updates deliberately."""
    versions = {ds["name"]: ds["version"] for ds in _REG_PAYLOAD["datasets"]}
    assert versions == {
        "symbol_universe": 1,
        "symbol_search": 1,
        "statements": 1,
        "quote_snapshot": 1,
        "quote_description": 1,
        "quote_ratings": 1,
        "quote_news": 1,
        "quote_insider": 1,
        "quote_peers": 1,
        "quote_etf_holders": 2,
        "quote_signals": 1,
        "earnings_screen": 1,
        "economic_calendar": 1,
        "economic_details": 1,
        "futures_tiles": 1,
    }


def test_dataset_versions_match_registry() -> None:
    for ds in schemas.registry().values():
        assert schemas.dataset_version(ds.name) == ds.version
