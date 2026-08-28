"""Immutable data models: artifact descriptors and compound bundles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from finvizp.errors import FinvizDataError
from finvizp.results import AccessTier, ResultStatus, _freeze

__all__ = ["Artifact", "QuoteBundle"]


@dataclass(frozen=True, slots=True)
class Artifact:
    """Immutable descriptor for a chart/spectrum artifact (bytes fetched separately)."""

    source_url: str
    kind: str
    media_type: str
    fetched_at: datetime
    symbol: str | None = None
    group: str | None = None
    timeframe: str | None = None
    chart_type: str | None = None
    content_hash: str | None = None
    content_length: int | None = None


@dataclass(frozen=True, slots=True)
class QuoteBundle:
    """One coherent stock page parsed into separate relations.

    Tabular relations are Arrow tables or ``None`` when the region is absent
    (optionally warned about by the caller). Descriptors preserve
    signal/link/artifact facts. This initial 0.1-A shape ships the envelope and
    empty-table defaults; Card 0.1-G fills in the parsers that populate it.
    """

    symbol: str
    fetched_at: datetime
    snapshot: Any | None = None
    description: Any | None = None
    ratings: Any | None = None
    news: Any | None = None
    insider: Any | None = None
    peers: Any | None = None
    etf_holders: Any | None = None
    signals: Any | None = None
    artifacts: tuple[Artifact, ...] = ()
    snapshot_tables: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    status: ResultStatus = ResultStatus.EMPTY
    access_tier: AccessTier = AccessTier.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.status, ResultStatus):
            msg = f"status must be a ResultStatus, got {type(self.status).__name__}"
            raise FinvizDataError(msg)
        if not isinstance(self.access_tier, AccessTier):
            msg = f"access_tier must be an AccessTier, got {type(self.access_tier).__name__}"
            raise FinvizDataError(msg)
        if not isinstance(self.snapshot_tables, Mapping):
            msg = f"snapshot_tables must be a Mapping, got {type(self.snapshot_tables).__name__}"
            raise FinvizDataError(msg)
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "snapshot_tables", _freeze(self.snapshot_tables))
        # Freeze every container-valued relation so caller-owned dicts/lists
        # cannot mutate the "frozen" bundle through their original reference.
        for name in (
            "snapshot",
            "description",
            "ratings",
            "news",
            "insider",
            "peers",
            "etf_holders",
            "signals",
        ):
            value = getattr(self, name)
            if isinstance(value, (Mapping, list, tuple, set, frozenset)):
                object.__setattr__(self, name, _freeze(value))
