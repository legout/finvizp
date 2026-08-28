"""Source-near value normalization into registry-driven Arrow tables.

Parsers hand over source-near row mappings (string display values from the
provider). This module owns deterministic Arrow construction: null sentinels,
comma numbers, compact suffixes, percentages, counts, dates, UTC timestamps,
``_raw`` companions, additive ``extra_fields``, drift warnings, and strict-mode
promotion to typed errors.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from zoneinfo import ZoneInfo

from finvizp import schemas
from finvizp.errors import FetchWarning, FinvizDataError

__all__ = ["build_table", "dataset_field_names"]

WarningCallback = Callable[[FetchWarning], Any]

# Provider display strings that mean "known missing", never NaN. The dashes are
# real provider sentinels (em/en), written as escapes to keep RUF001 quiet.
_NULL_SENTINELS = frozenset(
    {"", "-", "--", "---", "\u2014", "\u2013", "n/a", "N/A", "NA", "None", "null"}
)
_COMMA = re.compile(r",")
_COMPACT = re.compile(r"^[+-]?\d+(?:\.\d+)?[TMBK]?$", re.IGNORECASE)
_COMPACT_SUFFIX = re.compile(r"(?i)([TMBK])$")
_PERCENT = re.compile(r"(?i)%\s*$")
_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SUFFIX_SCALE = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}


def dataset_field_names(name: str) -> tuple[str, ...]:
    """Ordered registry field names for one dataset."""
    return schemas.dataset(name).field_names


def build_table(
    dataset_name: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    fetched_at: dt.datetime,
    strict_schema: bool = False,
    on_warning: WarningCallback | None = None,
) -> Any:
    """Build a deterministic Arrow table from source-near row mappings.

    Unknown additive fields land in ``extra_fields`` with a drift warning;
    unexpected conversion failures preserve the raw display in the dataset's
    ``_raw`` companion (or raise when there is none), and become typed errors
    under ``strict_schema=True``. Missing required keys always raise. Known
    missing sentinels become Arrow null, never NaN.
    """
    dataset = schemas.dataset(dataset_name)
    if fetched_at.tzinfo is None:
        msg = "fetched_at must be timezone-aware"
        raise FinvizDataError(msg)
    fmap = dataset.field_map
    warnings: list[FetchWarning] = []

    columns: dict[str, list[Any]] = {field.name: [] for field in dataset.fields}
    for position, row in enumerate(rows):
        if not isinstance(row, Mapping):
            msg = f"row {position} of dataset {dataset_name!r} must be a mapping"
            raise FinvizDataError(msg)
        if "symbol" not in row:
            msg = f"row {position} of dataset {dataset_name!r} is missing required 'symbol'"
            raise FinvizDataError(msg)
        extra: list[tuple[str, str]] = []
        for key, value in row.items():
            field = fmap.get(str(key))
            if field is None:
                warnings.append(
                    FetchWarning(
                        code="unknown_field",
                        message=f"unknown field {key!r} on dataset {dataset_name!r}",
                        symbol=str(row.get("symbol")) if row.get("symbol") is not None else None,
                        endpoint=dataset_name,
                    )
                )
                if strict_schema:
                    msg = f"unknown field {key!r} on dataset {dataset_name!r} (strict_schema=True)"
                    raise FinvizDataError(msg)
                extra.append((str(key), "" if value is None else str(value)))
                continue
            if key == "fetched_at":
                continue  # provenance column is filled from the shared timestamp
            columns[key].append(_convert(field, value, dataset_name, warnings, strict_schema))
            raw_name = f"{key}_raw"
            if field.raw and raw_name in fmap:
                # Companions retain the lossless source display, always.
                columns[raw_name].append(None if value is None else str(value))
        for field in dataset.fields:
            if field.name in row or field.name == "extra_fields":
                continue
            if field.unit == "raw" and field.name.removesuffix("_raw") in row:
                continue  # companion already mirrored from its base field
            if field.nullable:
                columns[field.name].append(None)
            elif field.name == "fetched_at":
                columns[field.name].append(fetched_at)
            else:
                msg = (
                    f"row {position} of dataset {dataset_name!r} is missing required "
                    f"field {field.name!r}"
                )
                raise FinvizDataError(msg)
        if "extra_fields" in columns:
            columns["extra_fields"].append(extra)

    import pyarrow as pa

    schema = schemas.arrow_schema(dataset_name)
    arrays = [
        pa.array(columns[field.name], type=schema.field(field.name).type)
        for field in dataset.fields
    ]
    table = pa.Table.from_arrays(arrays, schema=schema)
    if warnings and on_warning is not None:
        for warning in warnings:
            on_warning(warning)
    return table


def _convert(
    field: schemas.Field,
    value: Any,
    dataset_name: str,
    warnings: list[FetchWarning],
    strict_schema: bool,
) -> Any:
    """Convert one source-near value to its typed representation."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if text in _NULL_SENTINELS:
        warnings.append(
            FetchWarning(
                code="null_sentinel",
                message=f"null sentinel {text!r} on field {field.name!r}",
                endpoint=dataset_name,
            )
        )
        if strict_schema:
            msg = f"null sentinel {text!r} on field {field.name!r} (strict_schema=True)"
            raise FinvizDataError(msg)
        return None
    cleaned = _COMMA.sub("", text)
    try:
        return _typed(field, cleaned)
    except (ValueError, TypeError) as exc:
        message = f"cannot convert {text!r} to unit {field.unit!r} on field {field.name!r}: {exc}"
        if strict_schema:
            msg = f"{message} (strict_schema=True)"
            raise FinvizDataError(msg) from exc
        raw_name = f"{field.name}_raw"
        if raw_name not in schemas.dataset(dataset_name).field_map:
            raise FinvizDataError(message) from exc
        # Preserve the raw display for lossless recovery instead of dropping it.
        warnings.append(
            FetchWarning(code="conversion_failed", message=message, endpoint=dataset_name)
        )
        return text


def _typed(field: schemas.Field, text: str) -> Any:
    if field.unit == "count":
        return int(text)
    if field.unit == "compact":
        if not _COMPACT.match(text):
            return float(text)
        suffix = _COMPACT_SUFFIX.search(text)
        if suffix is None:
            return float(text)
        scale = _SUFFIX_SCALE[suffix.group(1).upper()]
        return float(_COMPACT_SUFFIX.sub("", text)) * scale
    if field.unit == "percent":
        return float(_PERCENT.sub("", text)) / 100.0
    if field.unit == "number":
        return float(text)
    if field.unit == "date":
        if not _DATE.match(text):
            msg = f"expected ISO date (YYYY-MM-DD), got {text!r}"
            raise ValueError(msg)
        return dt.date.fromisoformat(text)
    if field.unit == "timestamp":
        # Relative/time-only event displays (news/ratings windows) anchor to
        # today in US Eastern, then convert to unambiguous UTC timestamps.
        if _TIME_ONLY.match(text):
            fmt = "%H:%M:%S" if text.count(":") == 2 else "%H:%M"
            eastern = ZoneInfo("America/New_York")
            return dt.datetime.strptime(text, fmt).replace(tzinfo=eastern).astimezone(dt.UTC)
        if _DATETIME.match(text):
            naive = dt.datetime.fromisoformat(text.replace("T", " "))
            return naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(dt.UTC)
        msg = f"unrecognized timestamp display {text!r}"
        raise ValueError(msg)
    return text
