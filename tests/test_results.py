"""Contract tests for frozen result metadata, enums, and FetchResult accessors."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime

import pyarrow as pa
import pytest

from finvizp.errors import FinvizDataError
from finvizp.models import Artifact, QuoteBundle
from finvizp.results import (
    AccessTier,
    FetchResult,
    ResultMetadata,
    ResultStatus,
    SymbolResolutionRecord,
)


def _meta(**overrides: object) -> ResultMetadata:
    defaults: dict[str, object] = {
        "endpoint": "quote",
        "status": ResultStatus.COMPLETE,
        "access_tier": AccessTier.PUBLIC,
        "fetched_at": datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return ResultMetadata(**defaults)  # type: ignore[arg-type]


def test_result_status_members() -> None:
    assert {status.value for status in ResultStatus} == {"COMPLETE", "PARTIAL", "EMPTY"}


def test_access_tier_members() -> None:
    assert {tier.value for tier in AccessTier} == {
        "PUBLIC",
        "AUTHENTICATED",
        "ELITE",
        "UNKNOWN",
    }


def test_metadata_is_frozen_and_slotted() -> None:
    metadata = _meta()
    assert not hasattr(metadata, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        metadata.status = ResultStatus.EMPTY  # type: ignore[misc]


def test_metadata_defaults_are_immutable() -> None:
    metadata = _meta()
    assert metadata.cache_hit is False
    assert metadata.stale is False
    assert metadata.attempts == 1
    assert metadata.symbols == ()
    assert metadata.warnings == ()
    assert metadata.unit_errors == ()
    with pytest.raises(TypeError):
        metadata.query["t"] = "MSFT"  # type: ignore[index]


def test_metadata_completeness_counts_must_add_up() -> None:
    assert (
        _meta(
            status=ResultStatus.PARTIAL,
            requested_units=2,
            succeeded_units=1,
            failed_units=1,
        )
        is not None
    )
    with pytest.raises(FinvizDataError):
        _meta(requested_units=5, succeeded_units=1, failed_units=1)


def test_metadata_status_invariants() -> None:
    with pytest.raises(FinvizDataError):
        _meta(status=ResultStatus.COMPLETE, requested_units=1, succeeded_units=0, failed_units=1)
    with pytest.raises(FinvizDataError):
        _meta(status=ResultStatus.EMPTY, requested_units=1, succeeded_units=0, failed_units=1)
    with pytest.raises(FinvizDataError):
        _meta(
            status=ResultStatus.PARTIAL,
            requested_units=2,
            succeeded_units=2,
            failed_units=0,
        )


def test_metadata_normalizes_mutable_inputs() -> None:
    query = {"t": "AAPL", "filters": {"cap": "Large"}}
    symbols = [SymbolResolutionRecord(position=0, requested="BRK.B", canonical="BRK-B")]
    metadata = _meta(query=query, symbols=symbols)
    query["t"] = "MSFT"
    query["filters"]["cap"] = "Small"
    symbols.append(SymbolResolutionRecord(position=1, requested="AAPL", canonical="AAPL"))
    assert metadata.query["t"] == "AAPL"
    assert metadata.query["filters"]["cap"] == "Large"
    assert len(metadata.symbols) == 1
    with pytest.raises(TypeError):
        metadata.query["t"] = "MSFT"  # type: ignore[index]
    with pytest.raises(TypeError):
        metadata.query["filters"]["cap"] = "Small"  # type: ignore[index]


def test_metadata_default_query_is_empty_frozen_mapping() -> None:
    metadata = _meta()
    assert isinstance(metadata.query, Mapping)
    assert metadata.query == {}
    with pytest.raises(TypeError):
        metadata.query["t"] = "MSFT"  # type: ignore[index]


def test_metadata_rejects_non_integer_counters() -> None:
    with pytest.raises(FinvizDataError):
        _meta(requested_units=1.0, succeeded_units=1.0)  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        _meta(requested_units=True, succeeded_units=True)  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        _meta(attempts="1")  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        _meta(failed_units=0.5, requested_units=1.5, succeeded_units=1.0)  # type: ignore[arg-type]


def test_metadata_rejects_invalid_enums_and_negative_counters() -> None:
    with pytest.raises(FinvizDataError):
        _meta(status="COMPLETE")  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        _meta(access_tier="PUBLIC")  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        _meta(requested_units=-2, succeeded_units=-2)
    with pytest.raises(FinvizDataError):
        _meta(attempts=-1)


def test_metadata_rejects_non_mapping_query() -> None:
    with pytest.raises(FinvizDataError):
        _meta(query=None)  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        _meta(query="t=AAPL")  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        _meta(query=[("t", "AAPL")])  # type: ignore[arg-type]


def test_metadata_complete_requires_succeeded_unit() -> None:
    with pytest.raises(FinvizDataError):
        _meta(
            status=ResultStatus.COMPLETE,
            requested_units=0,
            succeeded_units=0,
            failed_units=0,
        )


def test_quotebundle_normalizes_mutable_inputs() -> None:
    fetched = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    table = pa.table({"symbol": ["AAPL"]})
    tables = {"snapshot": table}
    artifacts = [
        Artifact(
            source_url="https://finviz.com/chart.ashx?t=AAPL",
            kind="chart",
            media_type="image/png",
            fetched_at=fetched,
            symbol="AAPL",
        )
    ]
    bundle = QuoteBundle(
        symbol="AAPL",
        fetched_at=fetched,
        snapshot_tables=tables,
        artifacts=artifacts,  # type: ignore[arg-type]
    )
    tables["snapshot"] = None
    tables["extra"] = "mutated"
    artifacts.append(artifacts[0])
    assert bundle.snapshot_tables["snapshot"] is table
    assert "extra" not in bundle.snapshot_tables
    assert len(bundle.artifacts) == 1
    with pytest.raises(TypeError):
        bundle.snapshot_tables["extra"] = "nope"  # type: ignore[index]


def test_metadata_query_freezes_nested_sets() -> None:
    query = {"t": "AAPL", "filters": {"exchanges": {"NASDAQ", "NYSE"}}}
    metadata = _meta(query=query)
    stored = metadata.query["filters"]["exchanges"]
    assert isinstance(stored, frozenset)
    assert stored == frozenset({"NASDAQ", "NYSE"})
    query["filters"]["exchanges"].add("AMEX")
    assert "AMEX" not in stored


def test_quotebundle_snapshot_tables_freeze_nested_sets() -> None:
    fetched = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    tables = {"snapshot": {"scalars": {"price": {1.0, 2.0}}}}
    bundle = QuoteBundle(symbol="AAPL", fetched_at=fetched, snapshot_tables=tables)
    stored = bundle.snapshot_tables["snapshot"]["scalars"]["price"]
    assert isinstance(stored, frozenset)
    assert stored == frozenset({1.0, 2.0})
    tables["snapshot"]["scalars"]["price"].add(3.0)
    assert 3.0 not in stored


def test_quotebundle_freezes_relation_dicts_and_validates_declared_types() -> None:
    fetched = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    snapshot = {"market_cap": "2T"}
    bundle = QuoteBundle(symbol="AAPL", fetched_at=fetched, snapshot=snapshot)
    snapshot["market_cap"] = "mutated"
    assert bundle.snapshot["market_cap"] == "2T"
    with pytest.raises(FinvizDataError):
        QuoteBundle(symbol="AAPL", fetched_at=fetched, status="COMPLETE")  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        QuoteBundle(symbol="AAPL", fetched_at=fetched, access_tier="PUBLIC")  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError):
        QuoteBundle(symbol="AAPL", fetched_at=fetched, snapshot_tables=None)  # type: ignore[arg-type]


def test_fetchresult_is_frozen_and_generic() -> None:
    result = FetchResult[pa.Table](
        data=pa.table({"symbol": ["AAPL"]}),
        metadata=_meta(),
    )
    assert result.metadata.endpoint == "quote"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.data = pa.table({"symbol": []})  # type: ignore[misc]


def test_table_accessor_validates_runtime_kind() -> None:
    table = pa.table({"symbol": ["AAPL"]})
    result = FetchResult(data=table, metadata=_meta())
    assert result.table is table
    with pytest.raises(FinvizDataError):
        _ = FetchResult(data="nope", metadata=_meta()).table


def test_artifact_accessor_validates_runtime_kind() -> None:
    artifact = Artifact(
        source_url="https://finviz.com/chart.ashx?t=AAPL",
        kind="chart",
        media_type="image/png",
        fetched_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        symbol="AAPL",
    )
    result = FetchResult(data=artifact, metadata=_meta())
    assert result.artifact is artifact
    with pytest.raises(FinvizDataError):
        _ = FetchResult(data=pa.table({"symbol": ["AAPL"]}), metadata=_meta()).artifact


def test_symbol_resolution_record_is_frozen_and_slotted() -> None:
    record = SymbolResolutionRecord(position=0, requested="BRK.B", canonical="BRK-B")
    assert not hasattr(record, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.canonical = "AAPL"  # type: ignore[misc]
