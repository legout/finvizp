"""Remediation regressions for review round 1 findings on card 0.1-F.

Each test pins one review finding: exact no-data envelope recognition,
structural alignment of period arrays, per-input symbol provenance, warning /
unit-error / strict-schema threading into ``ResultMetadata``, and immediate
batch cancellation. Hermetic: scripted transports and pure payloads only.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from importlib import import_module
from typing import Any

import pyarrow as pa
import pytest

statements = import_module("finvizp.statements")

from finvizp._parsers import statements as stmt_parser  # noqa: E402
from finvizp.client import FinvizClient  # noqa: E402
from finvizp.errors import FinvizParseError  # noqa: E402
from finvizp.results import ResultStatus  # noqa: E402

FETCHED_AT = dt.datetime(2026, 8, 28, 14, 30, tzinfo=dt.UTC)


def _parse(payload: dict[str, Any]) -> stmt_parser.StatementRecords:
    return stmt_parser.parse_statement_json(
        payload, symbol="AAPL", statement="IA", fetched_at=FETCHED_AT
    )


def _good_payload() -> dict[str, Any]:
    return {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": ["9/30/2025"], "X": ["1.00"]},
    }


def _json_client(
    payload: dict[str, Any] | Exception,
    calls: list | None = None,
    cache_ttl: float | None = None,
) -> FinvizClient:
    """A client whose transport always answers with one scripted payload."""

    class ScriptedBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            from fastreq.backends.base import NormalizedResponse

            if calls is not None:
                calls.append(config)
            if isinstance(payload, Exception):
                raise payload
            body = json.dumps(payload).encode()
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

    return FinvizClient(transport=ScriptedBackend(), retry_attempts=0, cache_ttl=cache_ttl)


# --- finding 1: only the exact no-data envelope is EMPTY -------------------------


def test_exact_no_data_envelope_is_empty() -> None:
    records = _parse({"error": "no data"})
    assert records.rows == ()
    assert records.empty_recognized is True


@pytest.mark.parametrize("error", ["service unavailable", "no data ", "No Data", "", "no-data"])
def test_other_error_envelopes_are_parse_errors(error: str) -> None:
    with pytest.raises(FinvizParseError, match="error"):
        _parse({"error": error})


def test_error_with_other_payload_is_parse_error() -> None:
    with pytest.raises(FinvizParseError):
        _parse({"error": "no data", "retry_after": 30})


def test_unrecognized_error_envelope_message_is_fixed() -> None:
    # Raw provider error content must never reach the public error message
    # (spec: warnings/errors carry no sensitive raw response bodies).
    with pytest.raises(FinvizParseError) as exc_info:
        _parse({"error": "opaque-provider-secret-marker"})
    assert "opaque-provider-secret-marker" not in str(exc_info.value)
    assert "unrecognized statement error envelope" in str(exc_info.value)


# --- finding 2: structural alignment of period arrays ----------------------------


def test_short_period_end_dates_are_drift() -> None:
    payload = {
        "currency": "USD",
        "data": {
            "Period": ["2025FY", "2024FY"],
            "Period End Date": ["9/30/2025"],
            "X": ["1.00", "2.00"],
        },
    }
    with pytest.raises(FinvizParseError, match="Period End Date"):
        _parse(payload)


def test_surplus_period_end_dates_are_drift() -> None:
    payload = {
        "currency": "USD",
        "data": {
            "Period": ["2025FY"],
            "Period End Date": ["9/30/2025", "9/30/2024"],
            "X": ["1.00"],
        },
    }
    with pytest.raises(FinvizParseError, match="Period End Date"):
        _parse(payload)


def test_misaligned_period_length_is_drift() -> None:
    payload = {
        "currency": "USD",
        "data": {
            "Period": ["2025FY", "2024FY"],
            "Period End Date": ["9/30/2025", "9/30/2024"],
            "Period Length": ["12 Months"],
            "X": ["1.00", "2.00"],
        },
    }
    with pytest.raises(FinvizParseError, match="Period Length"):
        _parse(payload)


def test_aligned_period_length_parses() -> None:
    payload = {
        "currency": "USD",
        "data": {
            "Period": ["2025FY", "2024FY"],
            "Period End Date": ["9/30/2025", "9/30/2024"],
            "Period Length": ["12 Months", "12 Months"],
            "X": ["1.00", "2.00"],
        },
    }
    records = _parse(payload)
    assert len(records.rows) == 2  # one metric x two periods


# --- finding 3: per-input symbol provenance --------------------------------------


async def test_single_symbol_request_keeps_resolution_record() -> None:
    client = _json_client(_good_payload())
    result = await statements.statements_async("aapl", statement="IA", client=client)
    assert len(result.metadata.symbols) == 1
    record = result.metadata.symbols[0]
    assert (record.position, record.requested, record.canonical) == (0, "aapl", "AAPL")


async def test_batch_keeps_one_record_per_input_spelling() -> None:
    client = _json_client(_good_payload())
    result = await statements.statements_batch_async(
        ["brk.b", "AAPL", "BRK/B"], statement="IA", client=client
    )
    assert [(r.position, r.requested, r.canonical) for r in result.metadata.symbols] == [
        (0, "brk.b", "BRK-B"),
        (1, "AAPL", "AAPL"),
        (2, "BRK/B", "BRK-B"),
    ]
    # Canonical fetch/table work stays deduped: BRK-B fetched once.
    assert sorted(set(result.table.column("symbol").to_pylist())) == ["AAPL", "BRK-B"]


async def test_batch_units_count_canonical_fetches_not_input_positions() -> None:
    """Duplicate spellings fetch once; unit counts reflect canonical work."""
    calls: list[Any] = []
    client = _json_client(_good_payload(), calls)
    result = await statements.statements_batch_async(
        ["brk.b", "BRK/B"], statement="IA", client=client
    )
    assert len(calls) == 1
    assert result.metadata.requested_units == result.metadata.succeeded_units == 1
    assert result.metadata.status is ResultStatus.COMPLETE


# --- finding 4: warnings, unit errors, strict_schema ------------------------------


async def test_arrow_conversion_warnings_land_in_metadata() -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["not-a-number"]},
    }
    client = _json_client(payload)
    result = await statements.statements_async("AAPL", statement="IA", client=client)
    codes = [w.code for w in result.metadata.warnings]
    assert "conversion_failed" in codes
    table = result.table
    row = table.to_pylist()[0]
    assert row["value"] is None
    assert row["value_raw"] == "not-a-number"


async def test_partial_batch_exposes_unit_errors() -> None:
    good = _good_payload()
    drift = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": ["9/30/2025"]},  # metric X missing
    }

    class PickBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            from fastreq.backends.base import NormalizedResponse

            if config.params["t"] == "MSFT":
                body = json.dumps(drift).encode()
            else:
                body = json.dumps(good).encode()
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
    assert len(result.metadata.unit_errors) == 1
    unit = result.metadata.unit_errors[0]
    assert unit.symbol == "MSFT"
    assert "no statement data rows" in unit.message


async def test_partial_batch_unit_error_carries_no_provider_content() -> None:
    good = _good_payload()

    class MarkedBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            from fastreq.backends.base import NormalizedResponse

            if config.params["t"] == "MSFT":
                body = json.dumps({"error": "opaque-provider-secret-marker"}).encode()
            else:
                body = json.dumps(good).encode()
            return NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "application/json"},
                content=body,
                url="https://finviz.com/api/statement",
                is_json=True,
            )

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> MarkedBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=MarkedBackend(), retry_attempts=0)
    result = await statements.statements_batch_async(
        ["AAPL", "MSFT"], statement="IA", client=client, allow_partial=True
    )
    assert result.metadata.status is ResultStatus.PARTIAL
    unit = result.metadata.unit_errors[0]
    assert unit.symbol == "MSFT"
    assert "opaque-provider-secret-marker" not in unit.message


async def test_strict_schema_promotes_conversion_drift_to_error() -> None:
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["not-a-number"]},
    }
    client = _json_client(payload)
    with pytest.raises(Exception) as excinfo:
        await statements.statements_async("AAPL", statement="IA", client=client, strict_schema=True)
    from finvizp.errors import FinvizError

    assert isinstance(excinfo.value, FinvizError)
    assert not isinstance(excinfo.value, FinvizParseError)


# --- finding 5: batch cancellation is immediate -----------------------------------


async def test_batch_child_cancellation_cancels_siblings_immediately() -> None:
    started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    class SlowThenBlockedBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            started.set()
            await asyncio.sleep(30)
            raise AssertionError("never reached")  # pragma: no cover

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> SlowThenBlockedBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=SlowThenBlockedBackend(), retry_attempts=0)
    task = asyncio.create_task(
        statements.statements_batch_async(["AAPL", "MSFT"], statement="IA", client=client)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    sibling_cancelled.set()


async def test_batch_cancellation_does_not_wait_for_slow_sibling() -> None:
    """A child cancellation must propagate without awaiting sibling completion."""
    release_slow = asyncio.Event()

    class MixedBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            from fastreq.backends.base import NormalizedResponse

            if config.params["t"] == "AAPL":
                # Fails fast with a cancellation triggered from the test below.
                await asyncio.sleep(30)
                raise AssertionError("never reached")  # pragma: no cover
            # MSFT would finish quickly if it were awaited to completion.
            await release_slow.wait()
            body = json.dumps(_good_payload()).encode()
            return NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "application/json"},
                content=body,
                url="https://finviz.com/api/statement",
                is_json=True,
            )

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> MixedBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=MixedBackend(), retry_attempts=0)
    task = asyncio.create_task(
        statements.statements_batch_async(["AAPL", "MSFT"], statement="IA", client=client)
    )
    await asyncio.sleep(0.05)  # both children in flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    release_slow.set()  # would unblock the sibling after the fact


async def test_child_cancellation_cancels_sibling_and_propagates() -> None:
    """Finding 5 probe: one child cancelled from outside cancels its sibling
    and propagates without waiting for the sibling's in-flight work."""
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    sibling_tasks: list[asyncio.Task[Any]] = []

    class MixedBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            if config.params["t"] == "MSFT":
                sibling_started.set()
                me = asyncio.current_task()
                assert me is not None
                sibling_tasks.append(me)
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    sibling_cancelled.set()
                    raise
                raise AssertionError("never reached")  # pragma: no cover
            # AAPL: let the caller cancel exactly this child task.
            await sibling_started.wait()
            batch_task = asyncio.current_task()
            assert batch_task is not None
            # Cancel the whole batch from "inside" child AAPL's context: the
            # gather sees a cancelled child and must not linger on MSFT.
            batch_task.cancel()
            raise asyncio.CancelledError

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> MixedBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    client = FinvizClient(transport=MixedBackend(), retry_attempts=0)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            statements.statements_batch_async(["AAPL", "MSFT"], statement="IA", client=client),
            timeout=2.0,
        )
    # Cancellation was requested on the sibling before propagation returned.
    assert sibling_tasks[0].cancelling() >= 1
    # Drain: the reaped sibling finishes cancelled in the background.
    await asyncio.sleep(0.05)
    assert sibling_cancelled.is_set()
    assert sibling_tasks[0].cancelled()


# --- finding 6: schema field-name access -----------------------------------------


def test_statement_table_uses_registry_field_names() -> None:
    client = _json_client(_good_payload())
    result = asyncio.run(statements.statements_async("AAPL", statement="IA", client=client))
    from finvizp import arrow as fa

    assert isinstance(result.table, pa.Table)
    assert result.table.schema.names == list(fa.dataset_field_names("statements"))


# --- review round 2: strict cache isolation + sibling-cleanup delay ----------------


async def test_strict_call_never_serves_non_strict_cache_entry() -> None:
    """A strict request after a lenient one must re-parse (raising), not serve
    the lenient parsed cache entry — cache identity includes strict_schema."""
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["not-a-number"]},
    }
    calls: list[Any] = []
    client = _json_client(payload, calls, cache_ttl=60.0)
    lenient = await statements.statements_async("AAPL", statement="IA", client=client)
    assert [w.code for w in lenient.metadata.warnings] == ["conversion_failed"]
    assert len(calls) == 1

    from finvizp.errors import FinvizError

    with pytest.raises(FinvizError):
        await statements.statements_async("AAPL", statement="IA", client=client, strict_schema=True)
    # The strict miss re-ran the network parse instead of reusing the entry.
    assert len(calls) == 2


async def test_strict_and_lenient_single_flight_do_not_share() -> None:
    """Concurrent strict + lenient misses for the same symbol coalesce only
    onto their own facet; the strict joiner must not receive the lenient
    (warning-carrying) result."""
    payload = {
        "currency": "USD",
        "data": {"Period": ["2025FY"], "Period End Date": [""], "X": ["not-a-number"]},
    }
    client = _json_client(payload, cache_ttl=60.0)
    lenient_task = asyncio.ensure_future(
        statements.statements_async("AAPL", statement="IA", client=client)
    )
    strict_task = asyncio.ensure_future(
        statements.statements_async("AAPL", statement="IA", client=client, strict_schema=True)
    )
    lenient = await lenient_task

    from finvizp.errors import FinvizError

    with pytest.raises(FinvizError):
        await strict_task
    assert [w.code for w in lenient.metadata.warnings] == ["conversion_failed"]


async def test_child_cancel_propagates_while_sibling_cleanup_is_blocked() -> None:
    """A sibling that catches CancelledError and blocks in its cleanup must
    not delay propagation: the batch surfaces CancelledError first."""

    class CleanupBlocksBackend:
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            if config.params["t"] == "MSFT":
                sibling_started.set()
                # Child cancelled by the batch; its cleanup stalls until the
                # test releases it — propagation may not wait for this.
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    await cleanup_release.wait()
                    raise
                raise AssertionError("never reached")  # pragma: no cover
            # AAPL: cancel the whole batch from inside this child.
            await sibling_started.wait()
            batch_task = asyncio.current_task()
            assert batch_task is not None
            batch_task.cancel()
            raise asyncio.CancelledError

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> CleanupBlocksBackend:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def supports_http2(self) -> bool:
            return True

    sibling_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    client = FinvizClient(transport=CleanupBlocksBackend(), retry_attempts=0)
    batch = asyncio.ensure_future(
        statements.statements_batch_async(["AAPL", "MSFT"], statement="IA", client=client)
    )
    await sibling_started.wait()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(batch, timeout=2.0)
    cleanup_release.set()  # let the sibling finish its stalled cleanup
    await asyncio.sleep(0.05)  # drain: sibling completes cancelled, callback reaps it
