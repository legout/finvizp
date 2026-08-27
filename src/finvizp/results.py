"""Immutable result envelope: frozen enums, metadata, and generic FetchResult."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from finvizp.errors import FetchWarning, FinvizDataError, UnitError

T = TypeVar("T")


class ResultStatus(StrEnum):
    """Completeness of one fetch result."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"


class AccessTier(StrEnum):
    """Access tier inferred from response evidence."""

    PUBLIC = "PUBLIC"
    AUTHENTICATED = "AUTHENTICATED"
    ELITE = "ELITE"
    UNKNOWN = "UNKNOWN"


def _frozen_mapping(mapping: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(mapping or {}))


@dataclass(frozen=True, slots=True)
class SymbolResolutionRecord:
    """One input position resolved to its canonical provider symbol."""

    position: int
    requested: str
    canonical: str


@dataclass(frozen=True, slots=True)
class ResultMetadata:
    """Frozen request provenance, completeness, access, and cache facts."""

    endpoint: str
    status: ResultStatus
    access_tier: AccessTier
    fetched_at: datetime
    served_at: datetime | None = None
    query: Mapping[str, Any] = field(default_factory=lambda: _frozen_mapping(None))
    symbols: tuple[SymbolResolutionRecord, ...] = ()
    warnings: tuple[FetchWarning, ...] = ()
    unit_errors: tuple[UnitError, ...] = ()
    requested_units: int = 1
    succeeded_units: int = 1
    failed_units: int = 0
    attempts: int = 1
    cache_hit: bool = False
    stale: bool = False
    response_hash: str | None = None
    route_fingerprint: str | None = None
    parser_version: str | None = None
    schema_version: int | None = None

    def __post_init__(self) -> None:
        if self.requested_units != self.succeeded_units + self.failed_units:
            msg = (
                f"completeness counts do not add up: requested={self.requested_units} "
                f"succeeded={self.succeeded_units} failed={self.failed_units}"
            )
            raise FinvizDataError(msg)
        if self.status is ResultStatus.COMPLETE and self.failed_units:
            msg = "COMPLETE result cannot have failed units"
            raise FinvizDataError(msg)
        if self.status is ResultStatus.PARTIAL and not (self.failed_units and self.succeeded_units):
            msg = "PARTIAL result requires both succeeded and failed units"
            raise FinvizDataError(msg)
        if self.status is ResultStatus.EMPTY and (self.succeeded_units or self.failed_units):
            msg = "EMPTY result cannot have succeeded or failed units"
            raise FinvizDataError(msg)
        if self.served_at is not None and self.served_at < self.fetched_at:
            msg = "served_at cannot precede fetched_at"
            raise FinvizDataError(msg)


@dataclass(frozen=True, slots=True)
class FetchResult(Generic[T]):
    """Immutable envelope for every network operation; ``.data`` is canonical."""

    data: T
    metadata: ResultMetadata

    @property
    def table(self) -> Any:
        """Return ``.data`` when it is an Arrow table; raise a typed error otherwise."""
        if not isinstance(self.data, _ARROW_TABLE_TYPES):
            msg = (
                f".table requires an Arrow table, got {type(self.data).__name__}; "
                "use .data for the canonical value"
            )
            raise FinvizDataError(msg)
        return self.data

    @property
    def artifact(self) -> Any:
        """Return ``.data`` when it is an Artifact; raise a typed error otherwise."""
        from finvizp.models import Artifact

        if not isinstance(self.data, Artifact):
            msg = (
                f".artifact requires an Artifact, got {type(self.data).__name__}; "
                "use .data for the canonical value"
            )
            raise FinvizDataError(msg)
        return self.data


_ARROW_TABLE_TYPES: tuple[type, ...] = ()
try:
    import pyarrow as pa
except ImportError:  # pragma: no cover - pyarrow is a core dependency
    pass
else:
    _ARROW_TABLE_TYPES = (pa.Table,)
