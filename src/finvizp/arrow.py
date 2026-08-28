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
_EASTERN = ZoneInfo("America/New_York")


def dataset_field_names(name: str) -> tuple[str, ...]:
    """Ordered registry field names for one dataset."""
    return schemas.dataset(name).field_names


def build_table(
    dataset_name: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    fetched_at: dt.datetime,
    response_date: dt.date | None = None,
    strict_schema: bool = False,
    on_warning: WarningCallback | None = None,
) -> Any:
    """Build a deterministic Arrow table from source-near row mappings.

    Unknown additive fields land in ``extra_fields`` with a drift warning;
    recoverable conversion failures yield typed null plus the ``_raw`` display
    and a warning (or raise when there is no companion, and always under
    ``strict_schema=True``). Known missing sentinels become Arrow null, never
    NaN, in strict mode too — ordinary missing data is not drift. Missing
    required keys always raise. Time-only displays anchor to ``response_date``
    in ``America/New_York``; ``response_date`` is the provider response's own
    date and must be supplied explicitly (``"fetched_at"`` opts into using the
    provenance timestamp's US-Eastern date). DST fold/gap local times have no
    unambiguous UTC instant and are kept as raw + ``ambiguous`` status.
    """
    dataset = schemas.dataset(dataset_name)
    if fetched_at.tzinfo is None:
        msg = "fetched_at must be timezone-aware"
        raise FinvizDataError(msg)
    if response_date is None:
        # fetched_at is request provenance, not the provider's response date;
        # guessing it can attach time-only events to the wrong day.
        msg = (
            "response_date is required for time-only anchoring; pass the "
            "provider response date or response_date='fetched_at' to opt in"
        )
        raise FinvizDataError(msg)
    if isinstance(response_date, str):
        if response_date != "fetched_at":
            msg = f"unknown response_date sentinel {response_date!r}"
            raise FinvizDataError(msg)
        anchor_date = fetched_at.astimezone(_EASTERN).date()
    else:
        anchor_date = response_date
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
        status_of: dict[str, str] = {}
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
            if key == "fetched_at" or (key == "extra_fields" and fmap.get(key)):
                msg = (
                    f"field {key!r} on dataset {dataset_name!r} is derived "
                    "(provenance/drift) and cannot be set in source rows"
                )
                raise FinvizDataError(msg)
            if (key.endswith("_raw") or key.endswith("_status")) and fmap.get(key):
                msg = (
                    f"field {key!r} on dataset {dataset_name!r} is derived from its base "
                    "field and cannot be set directly"
                )
                raise FinvizDataError(msg)
            converted, parse_status = _convert(
                field, value, dataset_name, anchor_date, warnings, strict_schema
            )
            if converted is None and not field.nullable:
                msg = (
                    f"field {key!r} on dataset {dataset_name!r} is non-nullable but "
                    f"row {position} normalized to null (missing, null, or sentinel)"
                )
                raise FinvizDataError(msg)
            columns[key].append(converted)
            if parse_status is not None:
                status_of[key] = parse_status
            raw_name = f"{key}_raw"
            if field.raw and raw_name in fmap:
                # Companions retain the lossless source display, always.
                columns[raw_name].append(None if value is None else str(value))
        for field in dataset.fields:
            if field.name in row or field.name == "extra_fields":
                continue
            if field.unit == "raw" and field.name.removesuffix("_raw") in row:
                continue  # companion already mirrored from its base field
            if field.name.endswith("_status") and field.name[: -len("_status")] in status_of:
                continue  # parse status filled from the temporal conversion below
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
        for base_name, status in status_of.items():
            status_name = f"{base_name}_status"
            if status_name in fmap:
                columns[status_name].append(status)
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
    anchor_date: dt.date,
    warnings: list[FetchWarning],
    strict_schema: bool,
) -> tuple[Any, str | None]:
    """Convert one source-near value; returns the typed value and parse status."""
    if value is None:
        return None, None
    text = value if isinstance(value, str) else str(value)
    if text in _NULL_SENTINELS:
        warnings.append(
            FetchWarning(
                code="null_sentinel",
                message=f"null sentinel {text!r} on field {field.name!r}",
                endpoint=dataset_name,
            )
        )
        # Ordinary missing data is not drift: null even under strict_schema.
        return None, None
    try:
        converted, parse_status = _typed(field, text, anchor_date)
    except (ValueError, TypeError) as exc:
        message = f"cannot convert {text!r} to unit {field.unit!r} on field {field.name!r}: {exc}"
        if strict_schema:
            msg = f"{message} (strict_schema=True)"
            raise FinvizDataError(msg) from exc
        raw_name = f"{field.name}_raw"
        if raw_name not in schemas.dataset(dataset_name).field_map:
            raise FinvizDataError(message) from exc
        # Recoverable drift: typed null + lossless raw display + warning.
        warnings.append(
            FetchWarning(code="conversion_failed", message=message, endpoint=dataset_name)
        )
        return None, None
    if parse_status == "ambiguous":
        # DST fold/gap: no unambiguous UTC instant exists. Keep the raw
        # display; strict mode treats the uncertainty as an error.
        message = (
            f"ambiguous local time {text!r} on field {field.name!r} "
            "(DST transition); no unambiguous UTC instant"
        )
        if strict_schema:
            msg = f"{message} (strict_schema=True)"
            raise FinvizDataError(msg)
        warnings.append(FetchWarning(code="ambiguous_time", message=message, endpoint=dataset_name))
        return None, "ambiguous"
    return converted, parse_status


def _parse_eastern(
    naive: dt.datetime, exact_status: str = "exact"
) -> tuple[dt.datetime | None, str]:
    """Localize a naive US-Eastern datetime; detect DST ambiguity.

    Returns the UTC instant plus a parse status. Ambiguous fold times
    (fall-back) and nonexistent gap times (spring-forward) have no single
    unambiguous UTC instant: the typed value is ``None`` with status
    ``"ambiguous"``, keeping the raw display for lossless recovery.
    ``exact_status`` names the parse (``exact`` for full datetimes,
    ``anchored`` for time-only displays whose date came from the response).
    """
    offset = _EASTERN.utcoffset(naive.replace(tzinfo=_EASTERN, fold=0))
    offset_late = _EASTERN.utcoffset(naive.replace(tzinfo=_EASTERN, fold=1))
    if offset is None or offset_late is None:  # pragma: no cover - ZoneInfo always defines
        msg = f"cannot resolve US Eastern offset for {naive!r}"
        raise ValueError(msg)
    if offset != offset_late:
        # PEP 495: differing fold-0/fold-1 offsets mean this local time occurs
        # twice (fall-back) or not at all (spring gap); no single UTC instant
        # is unambiguous, so callers keep the raw display instead of inventing
        # an instant.
        return None, "ambiguous"
    return naive.replace(tzinfo=_EASTERN).astimezone(dt.UTC), exact_status


def _typed(field: schemas.Field, text: str, anchor_date: dt.date) -> tuple[Any, str | None]:
    if field.unit == "text":
        return text, None
    # Numeric cleaning applies only to numeric units; text keeps its display.
    cleaned = _COMMA.sub("", text)
    if field.unit == "count":
        # True counts stay int64: plain or compact displays, base units only.
        if _COMPACT.match(cleaned):
            suffix = _COMPACT_SUFFIX.search(cleaned)
            if suffix is not None:
                scale = _SUFFIX_SCALE[suffix.group(1).upper()]
                cleaned = str(float(_COMPACT_SUFFIX.sub("", cleaned)) * scale)
        value = float(cleaned)
        if not value.is_integer():
            msg = f"non-integral count {text!r}"
            raise ValueError(msg)
        return int(value), None
    if field.unit == "compact":
        if not _COMPACT.match(cleaned):
            return float(cleaned), None
        suffix = _COMPACT_SUFFIX.search(cleaned)
        if suffix is None:
            return float(cleaned), None
        scale = _SUFFIX_SCALE[suffix.group(1).upper()]
        return float(_COMPACT_SUFFIX.sub("", cleaned)) * scale, None
    if field.unit == "percent":
        return float(_PERCENT.sub("", cleaned)) / 100.0, None
    if field.unit == "number":
        return float(cleaned), None
    if field.unit == "date":
        if not _DATE.match(text):
            msg = f"expected ISO date (YYYY-MM-DD), got {text!r}"
            raise ValueError(msg)
        return dt.date.fromisoformat(text), None
    if field.unit == "timestamp":
        # Event displays: full datetimes are exact; time-only displays anchor to
        # the response date in America/New_York and convert to UTC only when
        # unambiguous. Fold/gap local times stay untyped (None + status).
        if _TIME_ONLY.match(text) or _DATETIME.match(text):
            if _TIME_ONLY.match(text):
                fmt = "%H:%M:%S" if text.count(":") == 2 else "%H:%M"
                naive = dt.datetime.strptime(text, fmt).replace(
                    year=anchor_date.year, month=anchor_date.month, day=anchor_date.day
                )
                status_label = "anchored"
            else:
                naive = dt.datetime.fromisoformat(text.replace("T", " "))
                status_label = "exact"
            utc, status = _parse_eastern(naive, status_label)
            return utc, status
        msg = f"unrecognized timestamp display {text!r}"
        raise ValueError(msg)
    return text, None
