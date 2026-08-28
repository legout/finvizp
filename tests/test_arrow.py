"""Normalization tests for the registry-driven Arrow builder."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator, Mapping
from decimal import localcontext

import pyarrow as pa
import pytest

from finvizp import arrow as fa
from finvizp import schemas
from finvizp.errors import FetchWarning, FinvizDataError

NOW = dt.datetime(2026, 8, 27, 14, 30, tzinfo=dt.UTC)


RESPONSE_DATE = dt.date(2026, 8, 27)


def _build(dataset: str, rows: Iterable[Mapping[str, object]], **kwargs: object) -> pa.Table:
    if "response_date" not in kwargs:
        kwargs["response_date"] = RESPONSE_DATE
    return fa.build_table(dataset, rows, fetched_at=NOW, **kwargs)  # type: ignore[arg-type]


def _rows(table: pa.Table) -> list[dict[str, object]]:
    return table.to_pylist()


def test_common_fields_filled_and_deterministic() -> None:
    table = _build("symbol_universe", [{"symbol": "BRK-B"}])
    assert table.schema.names == list(fa.dataset_field_names("symbol_universe"))
    row = _rows(table)[0]
    assert row["symbol"] == "BRK-B"
    assert row["fetched_at"] == NOW


def test_empty_rows_produce_registered_empty_table() -> None:
    table = _build("symbol_search", [])
    assert table.num_rows == 0
    assert table.schema.names == list(fa.dataset_field_names("symbol_search"))
    for f in table.schema:
        assert f.nullable or f.name in {"symbol", "fetched_at"}


def test_null_sentinels_become_null() -> None:
    for sentinel in ("", "-", "--", "—", "n/a", "N/A", "NA", "None"):
        table = _build("symbol_search", [{"symbol": "AAPL", "company": sentinel}])
        assert _rows(table)[0]["company"] is None, sentinel


def test_comma_number_normalized() -> None:
    table = _build(
        "symbol_search", [{"symbol": "AAPL", "exchange": "NASDAQ", "market_cap": "1,234,567"}]
    )
    assert table.schema.field("market_cap").type == pa.float64()
    row = _rows(table)[0]
    assert row["market_cap"] == 1234567.0


def test_compact_suffixes_to_base_units() -> None:
    cases = {"1.5T": 1.5e12, "890.5B": 890.5e9, "12.3M": 12.3e6, "456K": 456e3, "789": 789.0}
    for text, expected in cases.items():
        table = _build("symbol_search", [{"symbol": "AAPL", "market_cap": text}])
        assert _rows(table)[0]["market_cap"] == pytest.approx(expected), text


def test_percent_to_fraction() -> None:
    for text, expected in {"3.25%": 0.0325, "-1.5%": -0.015, "3.25": 0.0325}.items():
        table = _build("symbol_search", [{"symbol": "AAPL", "div_yield": text}])
        assert _rows(table)[0]["div_yield"] == pytest.approx(expected), text


def test_counts_to_int64() -> None:
    table = _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT", "rank": "3"}])
    assert table.schema.field("rank").type == pa.int64()
    assert _rows(table)[0]["rank"] == 3


def test_floats_and_negative_compact() -> None:
    table = _build(
        "symbol_search",
        [{"symbol": "AAPL", "market_cap": "-2.4B", "div_yield": "0.5%"}],
    )
    row = _rows(table)[0]
    assert row["market_cap"] == pytest.approx(-2.4e9)
    assert row["div_yield"] == pytest.approx(0.005)


def test_date32_and_utc_timestamp() -> None:
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "price": "232.04", "ex_dividend_date": "2026-08-15"}],
    )
    assert table.schema.field("ex_dividend_date").type == pa.date32()
    assert _rows(table)[0]["ex_dividend_date"] == dt.date(2026, 8, 15)
    ts = _build(
        "quote_news",
        [
            {
                "symbol": "AAPL",
                "title": "x",
                "url": "https://example.com/a",
                "published_at": "10:00:00",
            }
        ],
    )
    published = _rows(ts)[0]["published_at"]
    assert published.tzinfo is not None and str(published.tzinfo) == "UTC"


def test_string_retention_and_raw_companions() -> None:
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "price": "232.04"}],
    )
    row = _rows(table)[0]
    assert row["price"] == pytest.approx(232.04)
    assert row["price_raw"] == "232.04"
    # text columns keep their exact display without any raw companion.
    plain = _build("symbol_search", [{"symbol": "AAPL", "company": "Apple Inc."}])
    assert _rows(plain)[0]["company"] == "Apple Inc."


def test_typed_value_null_retains_raw_and_warns() -> None:
    records: list[FetchWarning] = []

    def _warn(w: FetchWarning) -> None:
        records.append(w)

    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "price": "N/A"}],
        on_warning=_warn,
    )
    row = _rows(table)[0]
    assert row["price"] is None
    assert row["price_raw"] == "N/A"
    codes = [w.code for w in records]
    assert any(code == "null_sentinel" for code in codes)
    assert all("cookie" not in w.message.lower() for w in records)


def test_unknown_field_goes_to_extra_fields_with_drift_warning() -> None:
    records: list[FetchWarning] = []
    table = _build(
        "symbol_search",
        [{"symbol": "AAPL", "company": "Apple", "brand_new_ratio": "3.5%"}],
        on_warning=records.append,
    )
    row = _rows(table)[0]
    assert row["extra_fields"] == [("brand_new_ratio", "3.5%")]
    assert any(w.code == "unknown_field" for w in records)


def test_extra_fields_typed_as_map() -> None:
    table = _build("symbol_search", [{"symbol": "AAPL"}])
    assert pa.types.is_map(table.schema.field("extra_fields").type)


def test_raw_overrides_validation() -> None:
    """Override plumbing: unknown keys, count mismatches, and non-string values are typed errors."""
    # key that is not a raw-declared base field
    with pytest.raises(FinvizDataError, match="raw override"):
        _build("quote_snapshot", [{"symbol": "AAPL"}], raw_overrides={"company": ["x"]})
    # derived key
    with pytest.raises(FinvizDataError, match="raw override"):
        _build("quote_snapshot", [{"symbol": "AAPL"}], raw_overrides={"price_raw": ["x"]})
    # count mismatch
    with pytest.raises(FinvizDataError, match="1 values for 2 rows"):
        _build(
            "quote_snapshot",
            [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
            raw_overrides={"price": ["1.00"]},
        )
    # non-string sequence member
    with pytest.raises(FinvizDataError, match="string"):
        _build("quote_snapshot", [{"symbol": "AAPL"}], raw_overrides={"price": [123.0]})
    # non-string/sequence value
    with pytest.raises(FinvizDataError, match="string"):
        _build("quote_snapshot", [{"symbol": "AAPL"}], raw_overrides={"price": "232.04"})
    # a valid override wins over the normalized shape
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "ex_dividend_date": "2026-08-10"}],
        raw_overrides={"ex_dividend_date": ["Aug 10, 2026"]},
    )
    row = _rows(table)[0]
    assert row["ex_dividend_date"] == dt.date(2026, 8, 10)
    assert row["ex_dividend_date_raw"] == "Aug 10, 2026"
    # empty rows + empty overrides stay fine
    table = _build("quote_snapshot", [], raw_overrides={})
    assert table.num_rows == 0


def test_builder_streams_rows_without_raw_overrides() -> None:
    """No overrides: a generator input is consumed lazily, exactly once."""

    def _gen() -> Iterator[dict[str, object]]:
        yield {"symbol": "AAPL"}
        yield {"symbol": "MSFT"}

    stream = _gen()
    streamed = _build("symbol_universe", stream)
    listed = _build("symbol_universe", [{"symbol": "AAPL"}, {"symbol": "MSFT"}])
    assert streamed.equals(listed)
    assert streamed.num_rows == 2
    # exactly-once: re-iterating the exhausted generator yields nothing more
    assert list(stream) == []


def test_strict_schema_promotes_drift_to_errors() -> None:
    with pytest.raises(FinvizDataError):
        _build("symbol_search", [{"symbol": "AAPL", "brand_new_ratio": "3.5%"}], strict_schema=True)
    with pytest.raises(FinvizDataError):
        _build(
            "quote_snapshot",
            [{"symbol": "AAPL", "price": "garbage"}],
            strict_schema=True,
        )


def test_required_conversion_failure_raises_data_error() -> None:
    with pytest.raises(FinvizDataError):
        _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT", "rank": "many"}])


def test_field_order_is_registry_deterministic() -> None:
    table_a = _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT"}])
    table_b = _build("quote_peers", [{"symbol": "AAPL", "peer": "GOOG"}])
    assert table_a.schema.equals(table_b.schema)
    assert table_a.schema.names == table_b.schema.names


def test_missing_required_symbol_rejected() -> None:
    with pytest.raises(FinvizDataError):
        _build("symbol_search", [{"company": "Apple"}])


# --- review rework: items 1-6 -------------------------------------------------


def test_every_registered_schema_builds_empty() -> None:
    """Item 6: parameterize the empty-table contract over the whole registry."""
    for name in schemas.dataset_names():
        table = _build(name, [])
        assert table.num_rows == 0, name
        assert table.schema.names == list(fa.dataset_field_names(name)), name


def test_optional_conversion_failure_yields_typed_null_raw_and_warning() -> None:
    """Item 1: recoverable conversion failure -> null + raw + warning, no raise."""
    records: list[FetchWarning] = []
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "price": "garbage"}],
        on_warning=records.append,
    )
    row = _rows(table)[0]
    assert row["price"] is None
    assert row["price_raw"] == "garbage"
    assert any(w.code == "conversion_failed" for w in records)


def test_optional_conversion_failure_without_raw_companion_raises() -> None:
    table = _build("symbol_search", [{"symbol": "AAPL", "div_yield": "garbage"}])
    assert _rows(table)[0]["div_yield"] is None
    with pytest.raises(FinvizDataError):
        _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT", "rank": "many"}])


def test_raw_declaration_requires_companion_column() -> None:
    """Item 3: every ``raw: true`` base field has a registered companion."""
    for ds in schemas.registry().values():
        fmap = ds.field_map
        for field in ds.fields:
            if field.raw:
                assert f"{field.name}_raw" in fmap, (ds.name, field.name)


def test_extra_fields_present_where_drift_is_accepted() -> None:
    """Item 4: datasets that accept drift keep unknown fields, not drop them."""
    for name in schemas.dataset_names():
        fmap = schemas.dataset(name).field_map
        optional = [f for f in schemas.dataset(name).fields if f.nullable and not f.key]
        if len(optional) > 1:  # drift-accepting dataset: more than symbol+fetched_at
            assert "extra_fields" in fmap, name


def test_unknown_field_survives_in_extra_fields_on_quote_news() -> None:
    records: list[FetchWarning] = []
    table = _build(
        "quote_news",
        [
            {
                "symbol": "AAPL",
                "title": "t",
                "url": "https://example.com/a",
                "published_at": "10:00",
                "provider_new": "v",
            }
        ],
        on_warning=records.append,
    )
    row = _rows(table)[0]
    assert row["extra_fields"] == [("provider_new", "v")]
    assert any(w.code == "unknown_field" for w in records)


def test_strict_schema_allows_null_sentinels() -> None:
    """Item 5: ordinary missing data stays null even under strict mode."""
    table = _build("quote_snapshot", [{"symbol": "AAPL", "price": "N/A"}], strict_schema=True)
    row = _rows(table)[0]
    assert row["price"] is None
    assert row["price_raw"] == "N/A"


def test_time_only_anchors_to_response_date() -> None:
    """Item 2: time-only values use the response date, never 1900-01-01."""
    response_date = dt.date(2026, 8, 20)
    table = _build(
        "quote_news",
        [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
        response_date=response_date,
    )
    published = _rows(table)[0]["published_at"]
    assert published == dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC)  # 10:00 EDT


def test_time_only_parse_status_and_raw() -> None:
    table = _build(
        "quote_news",
        [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
        response_date=dt.date(2026, 8, 20),
        raw_overrides={"published_at": ["Today 10:00AM"]},
    )
    row = _rows(table)[0]
    assert row["published_at_raw"] == "Today 10:00AM"
    assert row["published_at_status"] == "anchored"  # date assumed from response


def test_time_only_dst_spring_gap_is_ambiguous() -> None:
    """A nonexistent local time (spring-forward gap) has no UTC instant: null + status."""
    table = _build(
        "quote_news",
        [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "02:30"}],
        response_date=dt.date(2026, 3, 8),  # US spring-forward day
    )
    row = _rows(table)[0]
    assert row["published_at"] is None
    assert row["published_at_status"] == "ambiguous"
    assert row["published_at_raw"] == "02:30"


def test_time_only_dst_fall_back_is_ambiguous() -> None:
    """A wall time occurring twice (fall-back) has no single UTC instant: null + status."""
    table = _build(
        "quote_news",
        [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "01:30"}],
        response_date=dt.date(2026, 11, 1),  # US fall-back day
    )
    row = _rows(table)[0]
    assert row["published_at"] is None
    assert row["published_at_status"] == "ambiguous"
    assert row["published_at_raw"] == "01:30"


def test_explicit_datetime_dst_ambiguity_stays_null() -> None:
    """Explicit date-times share the fold/gap path and are likewise not invented."""
    table = _build(
        "quote_news",
        [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "2026-11-01 01:30:00"}],
    )
    row = _rows(table)[0]
    assert row["published_at"] is None
    assert row["published_at_status"] == "ambiguous"


def test_time_only_requires_response_date() -> None:
    """No silent fetch-provenance anchoring: absent response_date is a typed error."""
    late_fetch = dt.datetime(2026, 8, 28, 3, tzinfo=dt.UTC)  # Eastern date 2026-08-27
    with pytest.raises(FinvizDataError):
        fa.build_table(
            "quote_news",
            [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
            fetched_at=late_fetch,
        )
    explicit = fa.build_table(
        "quote_news",
        [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
        fetched_at=late_fetch,
        response_date=dt.date(2026, 8, 20),
    )
    published = _rows(explicit)[0]["published_at"]
    assert published == dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC)


def test_derived_fetched_at_and_extra_fields_rejected() -> None:
    """Builder-derived columns cannot be supplied as input; typed error, not PyArrow."""
    with pytest.raises(FinvizDataError):
        _build("symbol_search", [{"symbol": "AAPL", "fetched_at": "spoof"}])
    with pytest.raises(FinvizDataError):
        _build("symbol_search", [{"symbol": "AAPL", "extra_fields": {"x": "y"}}])


def test_strict_schema_rejects_ambiguous_local_time() -> None:
    """Strict mode promotes DST ambiguity to a typed error instead of null."""
    with pytest.raises(FinvizDataError):
        _build(
            "quote_news",
            [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "01:30"}],
            response_date=dt.date(2026, 11, 1),
            strict_schema=True,
        )


def test_response_date_fetched_at_sentinel_opts_into_provenance_anchor() -> None:
    table = _build(
        "quote_news",
        [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
        response_date="fetched_at",
    )
    row = _rows(table)[0]
    assert row["published_at"] == dt.datetime(2026, 8, 27, 14, 0, tzinfo=dt.UTC)  # 10:00 EDT
    assert row["published_at_status"] == "anchored"


def test_unknown_response_date_sentinel_rejected() -> None:
    with pytest.raises(FinvizDataError):
        _build(
            "quote_news",
            [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
            response_date="yesterday",
        )


def test_explicit_raw_companion_key_rejected() -> None:
    """`*_raw`/`*_status` columns are derived; setting them directly is a typed error."""
    with pytest.raises(FinvizDataError):
        _build("quote_snapshot", [{"symbol": "AAPL", "price": "232.04", "price_raw": "x"}])
    with pytest.raises(FinvizDataError):
        _build(
            "quote_news",
            [{"symbol": "AAPL", "title": "t", "url": "u", "published_at_status": "exact"}],
        )


def test_explicit_datetime_timestamp_is_exact() -> None:
    table = _build(
        "quote_news",
        [
            {
                "symbol": "AAPL",
                "title": "t",
                "url": "u",
                "published_at": "2026-08-20 10:00:00",
            }
        ],
    )
    row = _rows(table)[0]
    assert row["published_at"] == dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC)
    assert row["published_at_status"] == "exact"
    assert row["published_at_raw"] == "2026-08-20 10:00:00"


# --- review run 48: items 1-4 -------------------------------------------------


def test_text_commas_preserved() -> None:
    """Item 1: numeric comma cleaning must never touch text columns."""
    table = _build(
        "symbol_search",
        [{"symbol": "BRK.B", "company": "Berkshire Hathaway, Inc.", "exchange": "NYSE"}],
    )
    row = _rows(table)[0]
    assert row["company"] == "Berkshire Hathaway, Inc."
    records: list[FetchWarning] = []
    table = _build(
        "quote_news",
        [{"symbol": "AAPL", "title": "Fed, ECB meet", "url": "u"}],
        on_warning=records.append,
    )
    assert _rows(table)[0]["title"] == "Fed, ECB meet"
    assert records == []


def test_non_nullable_field_null_rejected() -> None:
    """Item 2: null in a registry non-null field is a typed error, not a null row."""
    with pytest.raises(FinvizDataError, match="symbol"):
        _build("symbol_search", [{"symbol": None}])
    with pytest.raises(FinvizDataError, match="title"):
        _build("quote_news", [{"symbol": "AAPL", "title": None, "url": "u"}])
    # required-field null paths: missing key, sentinel value, conversion-to-none
    with pytest.raises(FinvizDataError, match="symbol"):
        _build("symbol_search", [{"company": "Apple"}])
    with pytest.raises(FinvizDataError, match="title"):
        _build("quote_news", [{"symbol": "AAPL", "title": "N/A", "url": "u"}])
    with pytest.raises(FinvizDataError, match="statement_kind"):
        _build(
            "statements",
            [
                {
                    "symbol": "AAPL",
                    "statement_kind": "---",
                    "periodicity": "q",
                    "period_label": "Q2",
                    "metric": "rev",
                    "value": "1",
                }
            ],
        )


def test_compact_count_normalized_to_int64() -> None:
    """Item 3: compact count displays become base-unit int64; raw retained."""
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "volume": "1.5M", "average_volume": "45,678K"}],
    )
    assert table.schema.field("volume").type == pa.int64()
    row = _rows(table)[0]
    assert row["volume"] == 1_500_000
    assert row["volume_raw"] == "1.5M"
    assert row["average_volume"] == 45_678_000
    assert row["average_volume_raw"] == "45,678K"
    # plain integers and negatives still work
    table = _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT", "rank": "3"}])
    assert _rows(table)[0]["rank"] == 3
    # non-integral compact counts are rejected deterministically (raw companion)
    records: list[FetchWarning] = []
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "volume": "1.23456789M"}],
        on_warning=records.append,
    )
    row = _rows(table)[0]
    assert row["volume"] is None
    assert row["volume_raw"] == "1.23456789M"
    assert any(w.code == "conversion_failed" for w in records)
    # invalid count on a field without a companion raises
    with pytest.raises(FinvizDataError):
        _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT", "rank": "many"}])


# --- review run 50: items 1-2 -------------------------------------------------


def test_count_parses_exact_beyond_float53() -> None:
    """Run-50 item 1: int64 counts never round-trip through binary float."""
    exact = 9_007_199_254_740_993  # 2**53 + 1: float() would round it to ...992
    table = _build("quote_snapshot", [{"symbol": "AAPL", "volume": str(exact)}])
    row = _rows(table)[0]
    assert row["volume"] == exact
    assert row["volume_raw"] == str(exact)
    # comma displays parse the same, exactly
    table = _build("quote_snapshot", [{"symbol": "AAPL", "volume": "9,007,199,254,740,993"}])
    assert _rows(table)[0]["volume"] == exact


def test_count_out_of_int64_range_is_conversion_failure() -> None:
    """Run-50 item 1: overflow is recoverable drift, never a raw OverflowError."""
    records: list[FetchWarning] = []
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "volume": str(2**63)}],
        on_warning=records.append,
    )
    row = _rows(table)[0]
    assert row["volume"] is None
    assert row["volume_raw"] == str(2**63)
    assert any(w.code == "conversion_failed" for w in records)
    # without a raw companion the same overflow is a typed error, not OverflowError
    with pytest.raises(FinvizDataError, match="int64"):
        _build("quote_peers", [{"symbol": "AAPL", "peer": "MSFT", "rank": str(2**63)}])
    with pytest.raises(FinvizDataError):
        _build(
            "quote_snapshot",
            [{"symbol": "AAPL", "volume": str(2**63)}],
            strict_schema=True,
        )


def test_count_int64_boundaries_parse() -> None:
    """Exact int64 limits (and a compact display of one) are representable."""
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "volume": str(2**63 - 1), "average_volume": str(-(2**63))}],
    )
    row = _rows(table)[0]
    assert row["volume"] == 2**63 - 1
    assert row["average_volume"] == -(2**63)
    # compact display scaling stays exact in Decimal space (int64 max in K)
    table = _build("quote_snapshot", [{"symbol": "AAPL", "volume": "9,223,372,036,854,775.807K"}])
    assert _rows(table)[0]["volume"] == 2**63 - 1


def test_compact_scaling_is_independent_of_decimal_context() -> None:
    """Run-63 item 1: suffix-bearing compact (float64) conversion ignores ambient precision."""
    display = "1.23456789M"
    expected = float("1234567.89")  # exact positional decimal, one correct rounding
    t_default = _build("symbol_search", [{"symbol": "AAPL", "market_cap": display}])
    with localcontext() as ctx:
        ctx.prec = 2  # hostile ambient precision must not corrupt compact parsing
        t_low = _build("symbol_search", [{"symbol": "AAPL", "market_cap": display}])
    assert t_low.equals(t_default)
    assert _rows(t_low)[0]["market_cap"] == pytest.approx(expected)
    # long mantissa: nearest-float64 rounding of the exact value, not context truncation
    long_display = "1." + "1" * 30 + "M"
    expected_long = float("1111111." + "1" * 30)
    t_default = _build("symbol_search", [{"symbol": "AAPL", "market_cap": long_display}])
    with localcontext() as ctx:
        ctx.prec = 2
        t_low = _build("symbol_search", [{"symbol": "AAPL", "market_cap": long_display}])
    assert t_low.equals(t_default)
    assert _rows(t_low)[0]["market_cap"] == expected_long


def test_count_scaling_is_independent_of_decimal_context() -> None:
    """Run-56 item 1: count conversion is exact regardless of ambient precision."""
    with localcontext() as ctx:
        ctx.prec = 5  # low ambient precision must not corrupt count parsing
        # non-integral compact stays a conversion failure; rounding it would be silent corruption
        records: list[FetchWarning] = []
        table = _build(
            "quote_snapshot",
            [{"symbol": "AAPL", "volume": "1.23456789M"}],
            on_warning=records.append,
        )
        row = _rows(table)[0]
        assert row["volume"] is None
        assert row["volume_raw"] == "1.23456789M"
        assert any(w.code == "conversion_failed" for w in records)
        # integral compact still normalizes exactly, no warning
        table = _build("quote_snapshot", [{"symbol": "AAPL", "volume": "1.5M"}])
        assert _rows(table)[0]["volume"] == 1_500_000
        # compact int64 boundary stays exact under low ambient precision
        table = _build(
            "quote_snapshot", [{"symbol": "AAPL", "volume": "9,223,372,036,854,775.807K"}]
        )
        assert _rows(table)[0]["volume"] == 2**63 - 1


def test_count_long_mantissa_is_never_rounded() -> None:
    """Run-61 item: no fixed Decimal precision may round a long mantissa."""
    # 41 significant digits + K: true value 1000 + 1e-37, non-integral —
    # a fixed-precision context rounds it to exactly 1000 (silent corruption).
    display = "1." + "0" * 39 + "1K"
    records: list[FetchWarning] = []
    table = _build(
        "quote_snapshot",
        [{"symbol": "AAPL", "volume": display}],
        on_warning=records.append,
    )
    row = _rows(table)[0]
    assert row["volume"] is None
    assert row["volume_raw"] == display
    assert any(w.code == "conversion_failed" for w in records)
    with pytest.raises(FinvizDataError):
        _build("quote_snapshot", [{"symbol": "AAPL", "volume": display}], strict_schema=True)
    # integral control: same-length mantissa whose value IS exactly integral
    exact_display = "1." + "0" * 39 + "M"
    table = _build("quote_snapshot", [{"symbol": "AAPL", "volume": exact_display}])
    row = _rows(table)[0]
    assert row["volume"] == 1_000_000
    assert row["volume_raw"] == exact_display


def test_fetched_at_non_aware_tzinfo_rejected() -> None:
    """Run-56 item 3: tzinfo whose utcoffset() is None is not timezone-aware."""

    class _UnknownOffset(dt.tzinfo):
        def utcoffset(self, tz_dt: dt.datetime | None) -> dt.timedelta | None:
            return None

        def dst(self, tz_dt: dt.datetime | None) -> dt.timedelta | None:
            return None

        def tzname(self, tz_dt: dt.datetime | None) -> str | None:
            return None

    with pytest.raises(FinvizDataError, match="fetched_at"):
        fa.build_table(
            "symbol_universe",
            [{"symbol": "AAPL"}],
            fetched_at=dt.datetime(2026, 8, 27, 12, 34, 56, tzinfo=_UnknownOffset()),  # type: ignore[arg-type]
        )

    # a tzinfo whose utcoffset() raises is a typed error, not a leaked exception
    class _BrokenOffset(dt.tzinfo):
        def utcoffset(self, tz_dt: dt.datetime | None) -> dt.timedelta | None:
            raise ValueError("no offset known")

        def dst(self, tz_dt: dt.datetime | None) -> dt.timedelta | None:
            return None

        def tzname(self, tz_dt: dt.datetime | None) -> str | None:
            return None

    with pytest.raises(FinvizDataError, match="fetched_at"):
        fa.build_table(
            "symbol_universe",
            [{"symbol": "AAPL"}],
            fetched_at=dt.datetime(2026, 8, 27, 12, 34, 56, tzinfo=_BrokenOffset()),  # type: ignore[arg-type]
        )


def test_response_date_not_needed_without_time_only() -> None:
    """Run-50 item 2: response_date is required only to anchor time-only displays."""
    # non-temporal rows on a temporal dataset's sibling
    table = fa.build_table("symbol_universe", [{"symbol": "AAPL"}], fetched_at=NOW)
    assert table.num_rows == 1
    # empty rows, even on a dataset carrying timestamps
    table = fa.build_table("quote_news", [], fetched_at=NOW)
    assert table.num_rows == 0
    # a fully dated timestamp carries its own anchor
    table = fa.build_table(
        "quote_news",
        [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "2026-08-20 10:00:00"}],
        fetched_at=NOW,
    )
    row = _rows(table)[0]
    assert row["published_at"] == dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC)
    assert row["published_at_status"] == "exact"
    # ...while a time-only display still demands the response date
    with pytest.raises(FinvizDataError, match="response_date"):
        fa.build_table(
            "quote_news",
            [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
            fetched_at=NOW,
        )


# --- review run 53: items 1-3 -------------------------------------------------


def test_non_finite_numeric_displays_are_drift() -> None:
    """Run-53 item 1: NaN/Infinity spellings never become silent float values."""
    for dataset, row, field in (
        ("quote_snapshot", {"symbol": "AAPL", "price": "NaN"}, "price"),
        ("symbol_search", {"symbol": "AAPL", "market_cap": "Infinity"}, "market_cap"),
        ("symbol_search", {"symbol": "AAPL", "div_yield": "-Infinity%"}, "div_yield"),
    ):
        records: list[FetchWarning] = []
        table = _build(dataset, [row], on_warning=records.append)
        assert _rows(table)[0][field] is None, (dataset, field)
        assert _rows(table)[0][f"{field}_raw"] == row[field]
        assert any(w.code == "conversion_failed" for w in records), (dataset, field)
        with pytest.raises(FinvizDataError):
            _build(dataset, [row], strict_schema=True)
    # recognized null-like spelling with a finite prefix still fails, not nan
    records: list[FetchWarning] = []
    table = _build(
        "quote_snapshot", [{"symbol": "AAPL", "price": "nan"}], on_warning=records.append
    )
    assert _rows(table)[0]["price"] is None
    assert any(w.code == "conversion_failed" for w in records)


def test_extra_fields_are_canonically_ordered() -> None:
    """Run-53 item 2: input key insertion order never changes Arrow data."""
    base = {"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}
    rows = [dict(base, zeta="1", alpha="2"), dict(base, alpha="2", zeta="1")]
    t1 = _build("quote_news", [rows[0]])
    t2 = _build("quote_news", [rows[1]])
    assert t1.equals(t2)
    assert _rows(t1)[0]["extra_fields"] == [("alpha", "2"), ("zeta", "1")]
    # drift warnings follow the same canonical field order
    seen: list[str] = []

    def _record(w: FetchWarning) -> None:
        if w.code == "unknown_field":
            seen.append(str(w.message))

    _build("quote_news", [rows[0]], on_warning=_record)
    assert seen == sorted(seen)


def test_provenance_argument_types_are_validated() -> None:
    """Run-53 item 3: bad fetched_at/response_date types raise the typed error."""
    with pytest.raises(FinvizDataError, match="fetched_at"):
        fa.build_table("symbol_universe", [{"symbol": "AAPL"}], fetched_at=dt.date(2026, 8, 27))  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError, match="fetched_at"):
        fa.build_table("symbol_universe", [], fetched_at="2026-08-27")  # type: ignore[arg-type]
    with pytest.raises(FinvizDataError, match="response_date"):
        fa.build_table(
            "quote_news",
            [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
            fetched_at=NOW,
            response_date=42,  # type: ignore[arg-type]
        )
    with pytest.raises(FinvizDataError, match="response_date"):
        fa.build_table(
            "quote_news",
            [{"symbol": "AAPL", "title": "t", "url": "u", "published_at": "10:00"}],
            fetched_at=NOW,
            response_date="tomorrow",  # type: ignore[arg-type]
        )
