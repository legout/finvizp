"""Source-near value normalization into registry-driven Arrow tables.

Parsers hand over source-near row mappings (string display values from the
provider). This module owns deterministic Arrow construction: null sentinels,
comma numbers, compact suffixes, percentages, counts, dates, UTC timestamps,
``_raw`` companions, additive ``extra_fields``, drift warnings, and strict-mode
promotion to typed errors.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
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
# Exact integer exponents for suffix scaling (no Decimal, no rounding).
_SUFFIX_EXPONENT = {"T": 12, "B": 9, "M": 6, "K": 3}
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
    raw_overrides: Mapping[str, Sequence[str]] | None = None,
) -> Any:
    """Build a deterministic Arrow table from source-near row mappings.

    Unknown additive fields land in ``extra_fields`` with a drift warning;
    recoverable conversion failures yield typed null plus the ``_raw`` display
    and a warning (or raise when there is no companion, and always under
    ``strict_schema=True``). Known missing sentinels become Arrow null, never
    NaN, in strict mode too — ordinary missing data is not drift. Missing
    required keys always raise. Time-only displays anchor to ``response_date``
    in ``America/New_York``; ``response_date`` is the provider response's own
    date and is required only when a time-only display is actually converted
    (``"fetched_at"`` opts into using the provenance timestamp's US-Eastern
    date). DST fold/gap local times have no unambiguous UTC instant and are
    kept as raw + ``ambiguous`` status. ``raw_overrides`` lets a parser supply
    the exact provider display for a ``*_raw`` companion when it handed over a
    normalized shape (temporal normalization); value counts must match the row
    count.
    """
    dataset = schemas.dataset(dataset_name)
    if not isinstance(fetched_at, dt.datetime):
        msg = "fetched_at must be a timezone-aware datetime"
        raise FinvizDataError(msg)
    try:
        offset = fetched_at.utcoffset()
    except Exception as exc:
        msg = f"fetched_at must be a timezone-aware datetime: {exc}"
        raise FinvizDataError(msg) from exc
    if offset is None:
        # A tzinfo that cannot produce its offset (utcoffset() -> None) is not
        # timezone-aware; trusting it would mislabel unknown provenance as UTC.
        msg = "fetched_at must be a timezone-aware datetime"
        raise FinvizDataError(msg)
    if response_date is None:
        # Only a time-only display actually needs an anchor; the review work
        # below checks that per value, so a missing response_date is deferred
        # until one is seen (empty/non-temporal/dated input never requires it).
        anchor_date = None
    elif isinstance(response_date, str):
        if response_date != "fetched_at":
            msg = f"unknown response_date sentinel {response_date!r}"
            raise FinvizDataError(msg)
        anchor_date = fetched_at.astimezone(_EASTERN).date()
    elif isinstance(response_date, dt.date) and not isinstance(response_date, dt.datetime):
        anchor_date = response_date
    else:
        msg = f"response_date must be a dt.date, 'fetched_at', or None, got {response_date!r}"
        raise FinvizDataError(msg)
    fmap = dataset.field_map
    warnings: list[FetchWarning] = []
    overrides = dict(raw_overrides) if raw_overrides else {}
    for name, values in overrides.items():
        base = fmap.get(name)
        if base is None or not base.raw or f"{name}_raw" not in fmap:
            msg = (
                f"raw override key {name!r} on dataset {dataset_name!r} is not a "
                "raw-declared base field"
            )
            raise FinvizDataError(msg)
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            msg = (
                f"raw override for {name!r} on dataset {dataset_name!r} must be a "
                f"string sequence, got {type(values).__name__}"
            )
            raise FinvizDataError(msg)
        for value in values:
            if not isinstance(value, str):
                msg = (
                    f"raw override for {name!r} on dataset {dataset_name!r} must contain "
                    f"only strings, got {type(value).__name__}"
                )
                raise FinvizDataError(msg)
    if overrides:
        # Count and per-row override lookup need random access: only callers
        # that actually use overrides pay for materializing the input.
        rows_list = list(rows)
        for name, values in overrides.items():
            if len(values) != len(rows_list):
                msg = (
                    f"raw override for {name!r} on dataset {dataset_name!r} has {len(values)} "
                    f"values for {len(rows_list)} rows"
                )
                raise FinvizDataError(msg)
        rows_iter: Iterable[Mapping[str, Any]] = rows_list
    else:
        rows_iter = rows

    columns: dict[str, list[Any]] = {field.name: [] for field in dataset.fields}
    row_overrides_by_position: dict[int, dict[str, str]] = {}
    for name, values in overrides.items():
        for position, value in enumerate(values):
            row_overrides_by_position.setdefault(position, {})[name] = value
    position = -1
    for row in rows_iter:
        position += 1
        position_row_overrides = row_overrides_by_position.get(position)
        if not isinstance(row, Mapping):
            msg = f"row {position} of dataset {dataset_name!r} must be a mapping"
            raise FinvizDataError(msg)
        if "symbol" not in row:
            msg = f"row {position} of dataset {dataset_name!r} is missing required 'symbol'"
            raise FinvizDataError(msg)
        extra: list[tuple[str, str]] = []
        status_of: dict[str, str] = {}
        known: dict[str, Any] = {}
        unknown: list[tuple[str, Any]] = []
        # Per-row provider displays: override field -> this row's raw display.
        position_row_overrides = {name: values[position] for name, values in overrides.items()}
        for key, value in row.items():
            if fmap.get(str(key)) is None:
                unknown.append((str(key), value))
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
            known[str(key)] = value
        # Deterministic construction: equivalent rows must yield identical Arrow
        # data and warnings regardless of source key order, so drift is
        # reported in canonical field-name order and known fields are
        # processed in registry order below.
        unknown.sort(key=lambda entry: entry[0])
        for key, value in unknown:
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
            extra.append((key, "" if value is None else str(value)))
        for field in dataset.fields:
            name = field.name
            if name in known:
                value = known[name]
                converted, parse_status = _convert(
                    field, value, dataset_name, anchor_date, warnings, strict_schema
                )
                if converted is None and not field.nullable:
                    msg = (
                        f"field {name!r} on dataset {dataset_name!r} is non-nullable but "
                        f"row {position} normalized to null (missing, null, or sentinel)"
                    )
                    raise FinvizDataError(msg)
                columns[name].append(converted)
                if parse_status is not None:
                    status_of[name] = parse_status
                raw_name = f"{name}_raw"
                if field.raw and raw_name in fmap:
                    # Companions retain the lossless source display, always;
                    # raw_overrides restores the provider display when the row
                    # value itself is a parser-normalized shape.
                    if position_row_overrides is not None and name in position_row_overrides:
                        columns[raw_name].append(str(position_row_overrides[name]))
                    else:
                        columns[raw_name].append(None if value is None else str(value))
                continue
            if name == "extra_fields":
                continue  # filled after the loop
            if field.unit == "raw":
                base_field = fmap.get(name[: -len("_raw")]) if name.endswith("_raw") else None
                if base_field is None or not base_field.raw:
                    # Validator rejects this; reaching here means a malformed
                    # schema bypassed parse_dataset(), so fail typed, not with
                    # a raw pyarrow length mismatch.
                    msg = (
                        f"dataset {dataset_name!r} raw field {name!r} has no "
                        "raw-declared base field to mirror"
                    )
                    raise FinvizDataError(msg)
                if base_field.name in known:
                    continue  # companion already mirrored from its base field
                if name in position_row_overrides:
                    columns[name].append(str(position_row_overrides[name]))
                    continue
            if name.endswith("_status") and name[: -len("_status")] in status_of:
                continue  # parse status filled from the temporal conversion below
            if field.nullable:
                columns[name].append(None)
            elif name == "fetched_at":
                columns[name].append(fetched_at)
            else:
                msg = (
                    f"row {position} of dataset {dataset_name!r} is missing required field {name!r}"
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
    anchor_date: dt.date | None,
    warnings: list[FetchWarning],
    strict_schema: bool,
) -> tuple[Any, str | None]:
    """Convert one source-near value; returns the typed value and parse status.

    ``anchor_date`` may be ``None`` while no time-only display has been seen;
    ``_typed`` raises when one appears without a response date.
    """
    if value is None:
        return None, None
    text = value if isinstance(value, str) else str(value)
    if text in _NULL_SENTINELS and not (field.key and text == "NA" and field.name == "symbol"):
        # Sentinel -> null, except the documented real ticker ``NA`` on the
        # symbol key: key identity, live-probe drift — data, never a missing
        # marker. Non-nullable non-key fields still fall through to the
        # null -> typed-error check below.
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


def _finite(value: float, display: str) -> float:
    """Reject non-finite float results (NaN/Infinity spellings are drift)."""
    if not math.isfinite(value):
        raise ValueError(f"non-finite numeric display {display!r}")
    return value


def _scaled_decimal(text: str, display: str) -> tuple[str, int]:
    """Parse a compact display into an exact (mantissa_string, exponent) pair.

    Shared by the count and compact units: no Decimal context and no binary
    float participate, so no precision boundary can round a valid display.
    """
    if not _COMPACT.match(text):
        msg = f"invalid compact display {display!r}"
        raise ValueError(msg)
    mantissa = _COMPACT_SUFFIX.sub("", text)
    exp = 0
    if "." in mantissa:
        whole, frac = mantissa.split(".", 1)
        mantissa = whole + frac
        exp = -len(frac)
    if suffix := _COMPACT_SUFFIX.search(text):
        exp += _SUFFIX_EXPONENT[suffix.group(1).upper()]
    return mantissa, exp


def _typed(field: schemas.Field, text: str, anchor_date: dt.date | None) -> tuple[Any, str | None]:
    if field.unit == "text":
        return text, None
    # Numeric cleaning applies only to numeric units; text keeps its display.
    cleaned = _COMMA.sub("", text)
    if field.unit == "count":
        # True counts stay int64, scaled with exact integer arithmetic — no
        # Decimal context (fixed or ambient) and no binary float, so no
        # precision boundary can ever round a valid display into a wrong
        # count.
        mantissa, exp = _scaled_decimal(cleaned, text)
        coefficient = int(mantissa)
        if exp < 0:
            divisor = 10**-exp
            if coefficient % divisor:
                msg = f"non-integral count {text!r}"
                raise ValueError(msg)
            coefficient //= divisor
        else:
            coefficient *= 10**exp
        if not -(2**63) <= coefficient < 2**63:
            msg = f"count {text!r} outside signed int64 range"
            raise ValueError(msg)
        return coefficient, None
    if field.unit == "compact":
        if not _COMPACT.match(cleaned):
            return _finite(float(cleaned), text), None
        suffix = _COMPACT_SUFFIX.search(cleaned)
        if suffix is None:
            return _finite(float(cleaned), text), None
        # Exact coefficient+exponent scaled to a plain decimal string; float()
        # applies exactly one correctly-rounded strtod conversion, independent
        # of the ambient Decimal context.
        mantissa, exp = _scaled_decimal(cleaned, text)
        return _finite(float(f"{mantissa}e{exp}"), text), None
    if field.unit == "percent":
        return _finite(float(_PERCENT.sub("", cleaned)) / 100.0, text), None
    if field.unit == "number":
        return _finite(float(cleaned), text), None
    if field.unit == "date":
        if not _DATE.match(text):
            msg = f"expected ISO date (YYYY-MM-DD), got {text!r}"
            raise ValueError(msg)
        return dt.date.fromisoformat(text), None
    if field.unit == "timestamp":
        # Event displays: full datetimes are exact; time-only displays anchor to
        # the response date in America/New_York and convert to UTC only when
        # unambiguous. Fold/gap local times stay untyped (None + status).
        if _TIME_ONLY.match(text):
            if anchor_date is None:
                # Only time-only displays need the provider response date; a
                # missing one is a typed error here, not at call time. Raised
                # directly so a raw companion can't downgrade it to drift.
                msg = (
                    "response_date is required for time-only anchoring; pass the "
                    "provider response date or response_date='fetched_at' to opt in"
                )
                raise FinvizDataError(msg)
            fmt = "%H:%M:%S" if text.count(":") == 2 else "%H:%M"
            naive = dt.datetime.strptime(text, fmt).replace(
                year=anchor_date.year, month=anchor_date.month, day=anchor_date.day
            )
            utc, status = _parse_eastern(naive, "anchored")
            return utc, status
        if _DATETIME.match(text):
            naive = dt.datetime.fromisoformat(text.replace("T", " "))
            utc, status = _parse_eastern(naive, "exact")
            return utc, status
        msg = f"unrecognized timestamp display {text!r}"
        raise ValueError(msg)
    return text, None
