"""Contract tests for frozen result metadata, enums, and FetchResult accessors."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pyarrow as pa
import pytest

from finvizp.errors import FinvizDataError
from finvizp.models import Artifact
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
