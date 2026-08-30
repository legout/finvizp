"""Immutable data models: artifact descriptors and compound bundles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from finvizp.errors import FinvizDataError
from finvizp.results import AccessTier, ResultStatus, _freeze

__all__ = ["Artifact", "MapBundle", "MapConstituent", "QuoteBundle"]


@dataclass(frozen=True, slots=True)
class Artifact:
    """Immutable descriptor for a chart/spectrum artifact (bytes fetched separately).

    Descriptor fields describe the artifact without downloading it. The
    explicit :func:`finvizp.artifacts.download_artifact` returns a derived
    descriptor carrying the download state: ``content`` holds the raw bytes
    in memory, or ``path`` the filesystem target they were written to.
    ``content`` is excluded from ``repr``/``eq`` so a downloaded descriptor
    compares like the descriptor it came from and never renders bytes.
    """

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
    content: bytes | None = field(default=None, compare=False, repr=False)
    path: Path | None = field(default=None, compare=False, repr=False)


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
    fingerprint: str | None = None

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
        artifacts = tuple(self.artifacts)
        for element in artifacts:
            if not isinstance(element, Artifact):
                msg = f"artifacts elements must be Artifact, got {type(element).__name__}"
                raise FinvizDataError(msg)
        object.__setattr__(self, "artifacts", artifacts)
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


@dataclass(frozen=True, slots=True)
class MapConstituent:
    """One map symbol leaf flattened with its hierarchy path and perf."""

    symbol: str
    sector: str
    industry: str
    description: str | None
    value: float
    perf: float | None


@dataclass(frozen=True, slots=True)
class MapBundle:
    """One coherent public map: hierarchy tree plus flat constituents.

    ``root`` carries the provider's own Root -> sectors -> industries ->
    symbol-leaf tree (leaves join the embedded perf payload by ticker).
    ``constituents`` is the same leaf set in hierarchy order, flattened with
    sector/industry context. ``unmapped_perf`` records perf-only share-class
    symbols the hierarchy does not carry (verified FOX/GOOG/NWS drift): the
    renderer folds them into their class leaves locally, so the bundle reports
    them instead of guessing a placement. ``delay_minutes`` is the page's own
    delay statement; ``symbol`` names the map (``SP500`` for the public
    ``/map.ashx`` surface).
    """

    symbol: str
    fetched_at: datetime
    root: Any | None = None
    constituents: tuple[MapConstituent, ...] = ()
    perf: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    unmapped_perf: tuple[str, ...] = ()
    subtype: str | None = None
    version: int | None = None
    payload_hash: str | None = None
    hierarchy_url: str | None = None
    delay_minutes: float | None = None
    access_tier: AccessTier = AccessTier.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.access_tier, AccessTier):
            msg = f"access_tier must be an AccessTier, got {type(self.access_tier).__name__}"
            raise FinvizDataError(msg)
        if not isinstance(self.perf, Mapping):
            msg = f"perf must be a Mapping, got {type(self.perf).__name__}"
            raise FinvizDataError(msg)
        constituents = tuple(self.constituents)
        for element in constituents:
            if not isinstance(element, MapConstituent):
                msg = f"constituents elements must be MapConstituent, got {type(element).__name__}"
                raise FinvizDataError(msg)
        object.__setattr__(self, "constituents", constituents)
        object.__setattr__(self, "perf", _freeze(self.perf))
