"""Contract tests for the versioned Arrow schema registry."""

from __future__ import annotations

import pyarrow as pa
import pytest

from finvizp import schemas
from finvizp.errors import FinvizDataError

REQUIRED_DATASETS = (
    "symbol_universe",
    "symbol_search",
    "statements",
    "quote_snapshot",
    "quote_description",
    "quote_ratings",
    "quote_news",
    "quote_insider",
    "quote_peers",
    "quote_etf_holders",
    "quote_signals",
)

TYPE_VOCABULARY = frozenset(
    {"string", "int64", "float64", "date32", "timestamp_us_utc", "map_string_string"}
)
UNIT_VOCABULARY = frozenset(
    {"text", "count", "number", "compact", "percent", "date", "timestamp", "raw", "map"}
)
UNIT_TYPES = {
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


def _payload(**overrides: object) -> dict[str, object]:
    fields = [
        {
            "name": "symbol",
            "type": "string",
            "unit": "text",
            "nullable": False,
            "key": True,
        },
        {
            "name": "extra_fields",
            "type": "map_string_string",
            "unit": "map",
            "nullable": True,
        },
        {
            "name": "fetched_at",
            "type": "timestamp_us_utc",
            "unit": "timestamp",
            "nullable": False,
            "temporal": True,
        },
    ]
    payload: dict[str, object] = {"name": "sample", "version": 1, "fields": fields}
    payload.update(overrides)
    return payload


def test_required_datasets_present() -> None:
    assert set(REQUIRED_DATASETS) <= set(schemas.dataset_names())


def test_dataset_names_unique_and_deterministic() -> None:
    names = list(schemas.dataset_names())
    assert len(names) == len(set(names))
    assert names == sorted(names)


def test_dataset_versions_positive() -> None:
    for ds in schemas.registry().values():
        assert isinstance(ds.version, int) and ds.version >= 1


def test_fields_unique_ordered_nonempty() -> None:
    for ds in schemas.registry().values():
        names = list(ds.field_names)
        assert names, ds.name
        assert len(names) == len(set(names)), ds.name
        assert names == [f.name for f in ds.fields]


def test_types_and_units_in_vocabulary() -> None:
    for ds in schemas.registry().values():
        for f in ds.fields:
            assert f.type in TYPE_VOCABULARY, (ds.name, f.name)
            assert f.unit in UNIT_VOCABULARY, (ds.name, f.name)


def test_unit_type_agreement() -> None:
    for ds in schemas.registry().values():
        for f in ds.fields:
            assert f.type == UNIT_TYPES[f.unit], (ds.name, f.name)


def test_temporal_hints_match_units() -> None:
    for ds in schemas.registry().values():
        for f in ds.fields:
            assert f.temporal == (f.unit in {"date", "timestamp"}), (ds.name, f.name)


def test_key_fields_non_nullable_and_symbol_key_present() -> None:
    for ds in schemas.registry().values():
        assert not ds.field_map["symbol"].nullable
        assert ds.field_map["symbol"].key
        for f in ds.fields:
            if f.key:
                assert not f.nullable, (ds.name, f.name)


def test_common_row_fields_present() -> None:
    for ds in schemas.registry().values():
        fetched = ds.field_map["fetched_at"]
        assert fetched.unit == "timestamp"
        assert not fetched.nullable
        assert fetched.temporal


def test_raw_companions_reference_nullable_base() -> None:
    for ds in schemas.registry().values():
        for f in ds.fields:
            if f.unit != "raw":
                continue
            assert f.name.endswith("_raw"), (ds.name, f.name)
            base = ds.field_map.get(f.name[: -len("_raw")])
            assert base is not None, (ds.name, f.name)
            assert base.nullable, (ds.name, f.name)
            assert base.unit not in {"text", "map", "raw"}, (ds.name, f.name)


def test_every_dataset_has_exactly_one_extra_fields_map() -> None:
    for ds in schemas.registry().values():
        maps = [f for f in ds.fields if f.unit == "map"]
        assert len(maps) == 1 and maps[0].name == "extra_fields", ds.name
        assert maps[0].nullable


def test_registered_raw_fields_have_status_when_temporal() -> None:
    for ds in schemas.registry().values():
        fmap = ds.field_map
        for f in ds.fields:
            if f.raw and f.unit == "timestamp":
                assert f"{f.name}_status" in fmap, (ds.name, f.name)


def test_registry_payload_rejects_raw_flag_without_companion() -> None:
    bad = {"name": "x", "type": "float64", "unit": "number", "nullable": True, "raw": True}
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(fields=[*_payload()["fields"], bad]))  # type: ignore[list-item]


def test_registry_payload_rejects_timestamp_without_status_field() -> None:
    fields = [
        *_payload()["fields"],  # type: ignore[list-item]
        {
            "name": "published_at",
            "type": "timestamp_us_utc",
            "unit": "timestamp",
            "nullable": True,
            "temporal": True,
            "raw": True,
        },
        {"name": "published_at_raw", "type": "string", "unit": "raw", "nullable": True},
    ]
    with pytest.raises(FinvizDataError, match="published_at_status"):
        schemas.parse_dataset(_payload(fields=fields))


def test_registry_payload_rejects_missing_extra_fields_map() -> None:
    fields = [
        {"name": "symbol", "type": "string", "unit": "text", "nullable": False, "key": True},
        {
            "name": "fetched_at",
            "type": "timestamp_us_utc",
            "unit": "timestamp",
            "nullable": False,
            "temporal": True,
        },
    ]
    with pytest.raises(FinvizDataError, match="extra_fields"):
        schemas.parse_dataset(_payload(fields=fields))  # type: ignore[list-item]


def test_arrow_schema_matches_registry() -> None:
    for ds in schemas.registry().values():
        schema = schemas.arrow_schema(ds.name)
        assert isinstance(schema, pa.Schema)
        assert schema.names == list(ds.field_names)
        for f in ds.fields:
            field = schema.field(f.name)
            assert field.nullable is f.nullable
            if f.type == "timestamp_us_utc":
                assert pa.types.is_timestamp(field.type)
                assert field.type.tz == "UTC"
            if f.type == "map_string_string":
                assert pa.types.is_map(field.type)
        assert schemas.arrow_schema(ds.name).equals(schema)


def test_unknown_dataset_rejected() -> None:
    with pytest.raises(FinvizDataError):
        schemas.dataset("nope")


def test_registry_payload_rejects_invalid_versions() -> None:
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(version=0))


def test_registry_payload_rejects_duplicate_fields() -> None:
    fields = _payload()["fields"]
    fields = [*fields, dict(fields[0])]  # type: ignore[list-item]
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(fields=fields))


def test_registry_payload_rejects_unknown_type_and_unit() -> None:
    bad = {"name": "x", "type": "float32", "unit": "number", "nullable": True}
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(fields=[bad]))  # type: ignore[list-item]
    bad_unit = {"name": "x", "type": "float64", "unit": "currency", "nullable": True}
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(fields=[bad_unit]))  # type: ignore[list-item]


def test_registry_payload_rejects_unit_type_mismatch() -> None:
    bad = {"name": "x", "type": "string", "unit": "percent", "nullable": True}
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(fields=[bad]))  # type: ignore[list-item]


def test_registry_payload_rejects_nullable_key_field() -> None:
    fields = _payload()["fields"]
    fields[0] = {**fields[0], "nullable": True}  # type: ignore[index]
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(fields=fields))


def test_registry_payload_rejects_raw_companion_without_base() -> None:
    fields = [
        *_payload()["fields"],  # type: ignore[list-item]
        {"name": "value_raw", "type": "string", "unit": "raw", "nullable": True},
    ]
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(fields=fields))


def test_registry_payload_rejects_raw_companion_of_text_base() -> None:
    fields = [
        *_payload()["fields"],  # type: ignore[list-item]
        {"name": "company", "type": "string", "unit": "text", "nullable": True},
        {"name": "company_raw", "type": "string", "unit": "raw", "nullable": True},
    ]
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset(_payload(fields=fields))


def test_registry_payload_rejects_missing_common_fields() -> None:
    with pytest.raises(FinvizDataError):
        schemas.parse_dataset({"name": "sample", "version": 1, "fields": []})


def test_parse_dataset_preserves_field_order() -> None:
    ds = schemas.parse_dataset(_payload())
    assert list(ds.field_names) == ["symbol", "extra_fields", "fetched_at"]
