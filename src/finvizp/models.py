"""Immutable data models: artifact descriptors and compound bundles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from finvizp.results import AccessTier, ResultStatus

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
