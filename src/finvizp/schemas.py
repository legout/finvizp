"""Versioned schema registry: parse, validate, and expose dataset contracts.

The checked-in ``schema_registry.json`` is the single source of truth for
dataset names, schema versions, ordered fields, Arrow types, units, nullability,
key/temporal hints, ``_raw`` companions, and the ``extra_fields`` map.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from finvizp.errors import FinvizDataError

__all__ = [
    "Dataset",
    "Field",
    "arrow_schema",
    "dataset",
    "dataset_names",
    "dataset_version",
    "parse_dataset",
    "registry",
]

TYPE_VOCABULARY: frozenset[str] = frozenset(
    {"string", "int64", "float64", "date32", "timestamp_us_utc", "map_string_string"}
)
UNIT_VOCABULARY: frozenset[str] = frozenset(
    {"text", "count", "number", "compact", "percent", "date", "timestamp", "raw", "map"}
)
# Each semantic unit maps to exactly one physical Arrow type vocabulary entry.
UNIT_TYPES: dict[str, str] = {
    "text": "string",
    "count": "int64",
    "number": "float64",
    "compact": "float64",
    "percent": "float64",
    "date": "date32",
    "timestamp": "timestamp_us_utc",
    "raw": "string",
    "map": "map_string_string",
}
_COMMON_FIELDS = ("symbol", "fetched_at")


@dataclass(frozen=True, slots=True)
class Field:
    """One registry field contract."""

    name: str
    type: str
    unit: str
    nullable: bool
    temporal: bool = False
    key: bool = False
    raw: bool = False


@dataclass(frozen=True, slots=True)
class Dataset:
    """One versioned dataset contract with deterministic field order."""

    name: str
    version: int
    fields: tuple[Field, ...]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)

    @property
    def field_map(self) -> dict[str, Field]:
        return {field.name: field for field in self.fields}


def parse_dataset(payload: Any) -> Dataset:
    """Validate one registry dataset payload into a :class:`Dataset`."""
    if not isinstance(payload, dict):
        msg = f"dataset payload must be an object, got {type(payload).__name__}"
        raise FinvizDataError(msg)
    name = payload.get("name")
    version = payload.get("version")
    fields = payload.get("fields")
    if not isinstance(name, str) or not name:
        msg = f"dataset name must be a non-empty string, got {name!r}"
        raise FinvizDataError(msg)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        msg = f"dataset {name!r} version must be a positive integer, got {version!r}"
        raise FinvizDataError(msg)
    if not isinstance(fields, list) or not fields:
        msg = f"dataset {name!r} fields must be a non-empty list"
        raise FinvizDataError(msg)

    parsed: list[Field] = []
    seen: set[str] = set()
    for raw_field in fields:
        if not isinstance(raw_field, dict):
            msg = f"dataset {name!r} field must be an object, got {type(raw_field).__name__}"
            raise FinvizDataError(msg)
        field = _parse_field(name, raw_field)
        if field.name in seen:
            msg = f"dataset {name!r} has duplicate field {field.name!r}"
            raise FinvizDataError(msg)
        seen.add(field.name)
        parsed.append(field)

    dataset = Dataset(name=name, version=version, fields=tuple(parsed))
    _validate_dataset(dataset)
    return dataset


def _parse_field(dataset_name: str, payload: dict[str, Any]) -> Field:
    fname = payload.get("name")
    ftype = payload.get("type")
    unit = payload.get("unit")
    nullable = payload.get("nullable")
    if not isinstance(fname, str) or not fname:
        msg = f"dataset {dataset_name!r} field name must be a non-empty string, got {fname!r}"
        raise FinvizDataError(msg)
    if ftype not in TYPE_VOCABULARY:
        msg = f"dataset {dataset_name!r} field {fname!r} has unknown type {ftype!r}"
        raise FinvizDataError(msg)
    if unit not in UNIT_VOCABULARY:
        msg = f"dataset {dataset_name!r} field {fname!r} has unknown unit {unit!r}"
        raise FinvizDataError(msg)
    if UNIT_TYPES[unit] != ftype:
        msg = (
            f"dataset {dataset_name!r} field {fname!r}: unit {unit!r} requires type "
            f"{UNIT_TYPES[unit]!r}, got {ftype!r}"
        )
        raise FinvizDataError(msg)
    if not isinstance(nullable, bool):
        msg = (
            f"dataset {dataset_name!r} field {fname!r} 'nullable' must be a bool, got {nullable!r}"
        )
        raise FinvizDataError(msg)
    hints = {
        "key": payload.get("key", False),
        "temporal": payload.get("temporal", False),
        "raw": payload.get("raw", False),
    }
    for hint_name, hint_value in hints.items():
        if not isinstance(hint_value, bool):
            msg = (
                f"dataset {dataset_name!r} field {fname!r} {hint_name!r} must be a bool, "
                f"got {hint_value!r}"
            )
            raise FinvizDataError(msg)
    key = hints["key"]
    temporal = hints["temporal"]
    raw = hints["raw"]
    if temporal != (unit in {"date", "timestamp"}):
        msg = (
            f"dataset {dataset_name!r} field {fname!r}: temporal hint must "
            f"{'be set' if unit in {'date', 'timestamp'} else 'not be set'} for unit {unit!r}"
        )
        raise FinvizDataError(msg)
    if key and nullable:
        msg = f"dataset {dataset_name!r} field {fname!r} is a key and cannot be nullable"
        raise FinvizDataError(msg)
    return Field(
        name=fname, type=ftype, unit=unit, nullable=nullable, temporal=temporal, key=key, raw=raw
    )


def _validate_dataset(dataset: Dataset) -> None:
    fmap = dataset.field_map
    for required in _COMMON_FIELDS:
        if required not in fmap:
            msg = f"dataset {dataset.name!r} is missing required field {required!r}"
            raise FinvizDataError(msg)
    fetched = fmap["fetched_at"]
    if fetched.unit != "timestamp" or fetched.nullable:
        msg = f"dataset {dataset.name!r} fetched_at must be a non-null timestamp"
        raise FinvizDataError(msg)
    symbol = fmap["symbol"]
    if symbol.unit != "text" or symbol.nullable or not symbol.key:
        msg = f"dataset {dataset.name!r} symbol must be a non-null key text field"
        raise FinvizDataError(msg)
    maps = [f for f in dataset.fields if f.unit == "map"]
    if len(maps) != 1 or maps[0].name != "extra_fields" or not maps[0].nullable:
        msg = f"dataset {dataset.name!r} must declare exactly one nullable 'extra_fields' map field"
        raise FinvizDataError(msg)
    for field in dataset.fields:
        if field.unit != "raw":
            if field.raw:
                companion = fmap.get(f"{field.name}_raw")
                if companion is None or companion.unit != "raw" or not companion.nullable:
                    msg = (
                        f"dataset {dataset.name!r} field {field.name!r} declares raw=true "
                        f"but has no nullable {field.name + '_raw'!r} companion"
                    )
                    raise FinvizDataError(msg)
                if field.unit == "timestamp":
                    status = fmap.get(f"{field.name}_status")
                    if status is None or status.unit != "text" or not status.nullable:
                        msg = (
                            f"dataset {dataset.name!r} timestamp field {field.name!r} "
                            f"requires a nullable {field.name + '_status'!r} text field"
                        )
                        raise FinvizDataError(msg)
            continue
        base_name = field.name[: -len("_raw")] if field.name.endswith("_raw") else None
        base = fmap.get(base_name) if base_name else None
        if base is None or not base.nullable or base.unit in {"text", "map", "raw"}:
            msg = (
                f"dataset {dataset.name!r} raw companion {field.name!r} must mirror a "
                "nullable non-text base field"
            )
            raise FinvizDataError(msg)
    for field in dataset.fields:
        # A ``raw: true`` declaration promises the builder retains the source
        # display; without the companion column that promise is a silent no-op.
        if field.raw and f"{field.name}_raw" not in fmap:
            msg = (
                f"dataset {dataset.name!r} field {field.name!r} declares raw but "
                f"has no {field.name + '_raw'!r} companion column"
            )
            raise FinvizDataError(msg)


@lru_cache(maxsize=1)
def registry() -> dict[str, Dataset]:
    """Load and validate the checked-in registry; keyed deterministically by name."""
    text = resources.files("finvizp").joinpath("schema_registry.json").read_text("utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict) or not isinstance(payload.get("datasets"), list):
        msg = "schema registry must be an object with a datasets list"
        raise FinvizDataError(msg)
    datasets: dict[str, Dataset] = {}
    for entry in payload["datasets"]:
        dataset = parse_dataset(entry)
        if dataset.name in datasets:
            msg = f"schema registry has duplicate dataset name {dataset.name!r}"
            raise FinvizDataError(msg)
        datasets[dataset.name] = dataset
    return dict(sorted(datasets.items()))


def dataset_names() -> tuple[str, ...]:
    """All registered dataset names in deterministic order."""
    return tuple(registry())


def dataset(name: str) -> Dataset:
    """One registered dataset contract."""
    try:
        return registry()[name]
    except KeyError:
        msg = f"unknown dataset {name!r}; known datasets: {', '.join(registry())}"
        raise FinvizDataError(msg) from None


def dataset_version(name: str) -> int:
    """Schema version of one registered dataset."""
    return dataset(name).version


_ARROW_TYPES: dict[str, Any] = {}
try:
    import pyarrow as pa
except ImportError:  # pragma: no cover - pyarrow is a core dependency
    pass
else:
    _ARROW_TYPES = {
        "string": pa.string(),
        "int64": pa.int64(),
        "float64": pa.float64(),
        "date32": pa.date32(),
        "timestamp_us_utc": pa.timestamp("us", tz="UTC"),
        "map_string_string": pa.map_(pa.string(), pa.string()),
    }


def arrow_schema(name: str) -> Any:
    """Deterministic :class:`pyarrow.Schema` for one registered dataset."""
    import pyarrow as pa

    dataset_ = dataset(name)
    fields = [
        pa.field(field.name, _ARROW_TYPES[field.type], nullable=field.nullable)
        for field in dataset_.fields
    ]
    return pa.schema(fields)
