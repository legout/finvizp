"""Statement operation tests: six codes, long Arrow tables, batching (0.1-F).

RED-first: every test below fails until ``finvizp.statements`` exists.
Covers: code validation pre-network, all six statement/period combinations,
long-table rows, float64/blank/raw-value handling, batch order/dedupe,
strict/partial/all-fail, cancellation, cache/sync, and recognized empty versus
parse drift.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from importlib import import_module
from typing import Any

import pyarrow as pa
import pytest

statements = import_module("finvizp.statements")

from finvizp.client import ClientResponse, FinvizClient  # noqa: E402
from finvizp.errors import (  # noqa: E402
    FinvizBatchError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizPartialError,
    FinvizQueryError,
)
from finvizp.results import AccessTier, FetchResult, ResultStatus  # noqa: E402

FETCHED_AT = dt.datetime(2026, 8, 28, 14, 30, tzinfo=dt.UTC)


def _json_response(payload: dict[str, Any]) -> ClientResponse:
    import json

    return ClientResponse(
        endpoint="/api/statement",
        url="https://finviz.com/api/statement",
        query={},
        status_code=200,
        headers={"content-type": "application/json"},
        data=json.loads(json.dumps(payload)),
        content_kind="json",
        response_hash="0" * 64,
        fetched_at=FETCHED_AT,
        access_tier=AccessTier.PUBLIC,
        browser_profile="chrome",
        route_fingerprint="finviz-route-v1:test",
        attempts=1,
    )


def _client_with(payload: dict[str, Any] | Exception, calls: list | None = None) -> FinvizClient:
    """A client whose transport always answers with one scripted payload."""

    class ScriptedBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            from fastreq.backends.base import NormalizedResponse

            if calls is not None:
                calls.append(config)
            if isinstance(payload, Exception):
                raise payload
            body = b"NO DATA"  # pragma: no cover - replaced below
            import json as _json

            body = _json.dumps(payload).encode()
            return NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "application/json"},
                content=body,
                url="https://finviz.com/api/statement",
                is_json=True,
            )

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> ScriptedBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    return FinvizClient(transport=ScriptedBackend(), retry_attempts=0)


# --- statement code validation (pre-network) ------------------------------------


@pytest.mark.parametrize("bad", ["XX", "ia", "income", "", "IAX"])
def test_invalid_statement_code_fails_before_network(bad: str) -> None:
    calls: list[Any] = []
    client = _client_with({"error": "no data"}, calls)
    with pytest.raises(FinvizQueryError, match="statement"):
        asyncio.run(statements.statements_async("AAPL", statement=bad, client=client))
    assert calls == []


def test_statement_codes_map_to_all_six_combinations() -> None:
    for code, (kind, periodicity) in statements.STATEMENT_CODES.items():
        assert (kind, periodicity) in {
            ("income", "annual"),
            ("income", "quarterly"),
            ("balance", "annual"),
            ("balance", "quarterly"),
            ("cashflow", "annual"),
            ("cashflow", "quarterly"),
        }, code
    assert set(statements.STATEMENT_CODES) == {"IA", "IQ", "BA", "BQ", "CA", "CQ"}


# --- long Arrow table ----------------------------------------------------------


async def test_statements_returns_long_arrow_table() -> None:
    from tests.fixtures.statements import income_annual

    client = _client_with(income_annual.PAYLOAD)
    result = await statements.statements_async("AAPL", statement="IA", client=client)
    assert isinstance(result, FetchResult)
    table = result.table
    assert isinstance(table, pa.Table)
    from finvizp import arrow as fa

    assert table.schema.names == list(fa.dataset_field_names("statements"))
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.endpoint == "/api/statement"
    # 6 metrics x 4 periods
    assert table.num_rows == 24
    row = table.to_pylist()[0]
    assert row["symbol"] == "AAPL"
    assert row["statement_kind"] == "income"
    assert row["periodicity"] == "annual"
    assert row["metric"] == "Total Revenue"
    assert row["period_label"] == "TTM"
    assert row["period_end_date"] is None
    assert row["currency"] == "USD"
    # fetched_at is the live transport stamp (client._finish), never the local
    # fixture clock; the parse is deterministic for a given fetched_at.
    assert row["fetched_at"].tzinfo is not None


async def test_heterogeneous_values_use_float64_and_blanks_become_null() -> None:
    from tests.fixtures.statements import income_annual

    client = _client_with(income_annual.PAYLOAD)
    table = (await statements.statements_async("AAPL", statement="IA", client=client)).table
    value_type = table.schema.field("value").type
    assert pa.types.is_float64(value_type)
    rows = table.to_pylist()
    revenue_ttm = next(
        r for r in rows if r["metric"] == "Total Revenue" and r["period_label"] == "TTM"
    )
    assert revenue_ttm["value"] == pytest.approx(401500.0)
    blank = next(
        r for r in rows if r["metric"] == "Research and Development" and r["period_label"] == "TTM"
    )
    assert blank["value"] is None  # blank -> Arrow null, never NaN
    assert blank["value_raw"] == ""  # raw display survives verbatim
    signed = next(
        r
        for r in rows
        if r["metric"] == "Research and Development" and r["period_label"] == "2025FY"
    )
    assert signed["value"] == pytest.approx(-31370.0)
    assert signed["value_raw"] == "-31,370.00"
    ratio = next(r for r in rows if r["metric"] == "Diluted EPS" and r["period_label"] == "TTM")
    assert ratio["value"] == pytest.approx(0.62)


async def test_stable_period_order_preserved_in_long_rows() -> None:
    from tests.fixtures.statements import income_annual

    client = _client_with(income_annual.PAYLOAD)
    table = (await statements.statements_async("AAPL", statement="IA", client=client)).table
    labels = table.column("period_label").to_pylist()
    first_block = labels[:4]  # metric-major, so the first metric owns the order
    assert first_block == ["TTM", "2025FY", "2024FY", "2023FY"]


@pytest.mark.parametrize(
    ("code", "kind", "periodicity"),
    [
        ("IA", "income", "annual"),
        ("IQ", "income", "quarterly"),
        ("BA", "balance", "annual"),
        ("BQ", "balance", "quarterly"),
        ("CA", "cashflow", "annual"),
        ("CQ", "cashflow", "quarterly"),
    ],
)
async def test_all_six_combinations_stamp_kind_and_periodicity(
    code: str, kind: str, periodicity: str
) -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": ["9/30/2025"], "X": ["1.00"]},
    }
    client = _client_with(payload)
    result = await statements.statements_async("AAPL", statement=code, client=client)
    table = result.table
    assert table.num_rows == 1
    row = table.to_pylist()[0]
    assert row["statement_kind"] == kind
    assert row["periodicity"] == periodicity


async def test_parsed_quarterly_period_end_and_length() -> None:
    payload = {
        "currency": "USD",
        "data": {
            "Period": ["2026Q2", "2026Q1"],
            "Period End Date": ["3/31/2026", "12/31/2025"],
            "X": ["1.00", "2.00"],
        },
    }
    client = _client_with(payload)
    table = (await statements.statements_async("AAPL", statement="BQ", client=client)).table
    rows = table.to_pylist()
    q2 = next(r for r in rows if r["period_label"] == "2026Q2")
    assert q2["period_end_date"] == dt.date(2026, 3, 31)
    assert q2["period_length_days"] == 90


# --- recognized empty versus parse drift ----------------------------------------


async def test_no_data_error_is_recognized_empty_result() -> None:
    client = _client_with({"error": "no data"})
    result = await statements.statements_async("NOPE", statement="IA", client=client)
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.table.num_rows == 0
    # Registered schema survives an empty result.
    from finvizp import arrow as fa

    assert result.table.schema.names == list(fa.dataset_field_names("statements"))


async def test_structural_drift_raises_parse_error() -> None:
    client = _client_with({"currency": "USD", "data": {"Total Revenue": ["1.00"]}})
    with pytest.raises(FinvizParseError):
        await statements.statements_async("AAPL", statement="IA", client=client)


async def test_not_found_typed_error_propagates() -> None:
    client = _client_with(FinvizNotFoundError("resource not found (404)"))
    with pytest.raises(FinvizNotFoundError):
        await statements.statements_async("AAPL", statement="IA", client=client)


# --- batching: order, dedupe, strict/partial/all-fail ---------------------------


async def test_batch_preserves_first_occurrence_order_and_dedupes() -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["1.00"]},
    }
    calls: list[Any] = []
    client = _client_with(payload, calls)
    result = await statements.statements_batch_async(
        ["MSFT", "AAPL", "MSFT"], statement="IA", client=client
    )
    table = result.table
    symbols = list(dict.fromkeys(table.column("symbol").to_pylist()))
    assert symbols == ["MSFT", "AAPL"]  # canonical first-occurrence order
    requested = [c.params["t"] for c in calls]
    assert requested.count("MSFT") == 1  # deduped: one fetch per canonical symbol


async def test_batch_with_class_share_notation() -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["1.00"]},
    }
    calls: list[Any] = []
    client = _client_with(payload, calls)
    result = await statements.statements_batch_async(
        ["brk.b", "BRK/B"], statement="IA", client=client
    )
    assert [c.params["t"] for c in calls] == ["BRK-B"]
    assert set(result.table.column("symbol").to_pylist()) == {"BRK-B"}


async def test_batch_all_fail_raises_by_default() -> None:
    class FailingBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            from fastreq.exceptions import BackendError

            raise BackendError("boom")

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> FailingBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=FailingBackend(), retry_attempts=0)
    with pytest.raises(Exception) as excinfo:
        await statements.statements_batch_async(["AAPL", "MSFT"], statement="IA", client=client)
    assert not isinstance(excinfo.value, asyncio.CancelledError)


async def test_batch_all_fail_still_raises_with_allow_partial() -> None:
    class FailingBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            from fastreq.exceptions import BackendError

            raise BackendError("boom")

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> FailingBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=FailingBackend(), retry_attempts=0)
    with pytest.raises(FinvizBatchError):
        await statements.statements_batch_async(
            ["AAPL", "MSFT"], statement="IA", client=client, allow_partial=True
        )


async def test_strict_batch_raises_partial_exception_carrying_partial_result() -> None:
    good = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["1.00"]},
    }
    calls: list[Any] = []

    class PickBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            import json as _json

            from fastreq.backends.base import NormalizedResponse

            calls.append(config)
            # Second requested symbol (MSFT) fails with a typed verdict.
            if config.params["t"] == "MSFT":
                from fastreq.exceptions import BackendError

                raise BackendError("blocked")
            body = _json.dumps(good).encode()
            return NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "application/json"},
                content=body,
                url="https://finviz.com/api/statement",
                is_json=True,
            )

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> PickBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=PickBackend(), retry_attempts=0)

    with pytest.raises(FinvizPartialError) as excinfo:
        await statements.statements_batch_async(["AAPL", "MSFT"], statement="IA", client=client)
    partial = excinfo.value.partial_result
    assert partial.metadata.status is ResultStatus.PARTIAL
    assert partial.table.num_rows == 1
    assert partial.metadata.failed_units == 1
    assert partial.metadata.succeeded_units == 1


async def test_allow_partial_returns_partial_result() -> None:
    good = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["1.00"]},
    }

    class PickBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            import json as _json

            from fastreq.backends.base import NormalizedResponse
            from fastreq.exceptions import BackendError

            if config.params["t"] == "MSFT":
                raise BackendError("blocked")
            body = _json.dumps(good).encode()
            return NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "application/json"},
                content=body,
                url="https://finviz.com/api/statement",
                is_json=True,
            )

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> PickBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=PickBackend(), retry_attempts=0)
    result = await statements.statements_batch_async(
        ["AAPL", "MSFT"], statement="IA", client=client, allow_partial=True
    )
    assert result.metadata.status is ResultStatus.PARTIAL
    assert result.metadata.succeeded_units == 1
    assert result.metadata.failed_units == 1


# --- cancellation ---------------------------------------------------------------


async def test_cancellation_propagates_immediately() -> None:
    started = asyncio.Event()

    class SlowBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            started.set()
            await asyncio.sleep(30)
            raise AssertionError("never reached")  # pragma: no cover

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> SlowBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=SlowBackend(), retry_attempts=0)
    task = asyncio.create_task(statements.statements_async("AAPL", statement="IA", client=client))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- cache ---------------------------------------------------------------------


async def test_cache_reuse_reports_hit_without_new_request() -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["1.00"]},
    }
    calls: list[Any] = []
    client = _client_with(payload, calls)
    client._cache_ttl = 60.0  # enable caching on the test client
    first = await statements.statements_async("AAPL", statement="IA", client=client)
    second = await statements.statements_async("AAPL", statement="IA", client=client)
    assert len(calls) == 1
    assert second.metadata.cache_hit is True
    assert second.metadata.fetched_at == first.metadata.fetched_at


async def test_different_statement_codes_have_distinct_cache_keys() -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["1.00"]},
    }
    calls: list[Any] = []
    client = _client_with(payload, calls)
    client._cache_ttl = 60.0
    await statements.statements_async("AAPL", statement="IA", client=client)
    await statements.statements_async("AAPL", statement="IQ", client=client)
    assert len(calls) == 2


# --- sync wrapper ----------------------------------------------------------------


def test_sync_wrapper_runs_outside_loop() -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["1.00"]},
    }
    client = _client_with(payload)
    result = statements.statements("AAPL", statement="IA", client=client)
    assert result.table.num_rows == 1


def test_sync_wrapper_rejects_active_loop() -> None:
    async def inside() -> None:
        client = _client_with({"error": "no data"})
        with pytest.raises(RuntimeError, match="event loop"):
            statements.statements("AAPL", statement="IA", client=client)

    asyncio.run(inside())


# --- transient client default ---------------------------------------------------


async def test_client_argument_is_required_consistently() -> None:
    """Omitting the client creates a transient client (architecture contract)."""
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["1.00"]},
    }
    calls: list[Any] = []
    holder: list[Any] = []

    class RecordingClient(FinvizClient):
        def __init__(self) -> None:
            super().__init__(retry_attempts=0)
            holder.append(self)

    # Patch the module's transient-client factory so no live network occurs.
    original = statements._transient_client
    statements._transient_client = RecordingClient
    try:

        class Scripted:
            async def request(self, config: Any, stream_callback: Any = None) -> Any:
                import json as _json

                from fastreq.backends.base import NormalizedResponse

                calls.append(config)
                body = _json.dumps(payload).encode()
                return NormalizedResponse.from_backend(
                    status_code=200,
                    headers={"Content-Type": "application/json"},
                    content=body,
                    url="https://finviz.com/api/statement",
                    is_json=True,
                )

            async def close(self) -> None:
                pass

            async def __aenter__(self) -> Scripted:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            def supports_http2(self) -> bool:
                return True

        # RecordingClient still owns a real transport; retarget it.
        holder.clear()
        statements._transient_client = lambda: _client_with(payload, calls)  # type: ignore[assignment]
        result = await statements.statements_async("AAPL", statement="IA")
        assert result.table.num_rows == 1
        assert calls
    finally:
        statements._transient_client = original
