"""Contract tests for the event-loop-safe sync bridge."""

from __future__ import annotations

import asyncio

import pytest

from finvizp._sync import run_sync
from finvizp.errors import FinvizQueryError


async def _value() -> int:
    return 41 + 1


async def _raises() -> int:
    msg = "bad symbol"
    raise FinvizQueryError(msg, context={"t": "AAP?"})


def test_success_outside_event_loop() -> None:
    assert run_sync(_value()) == 42


def test_exceptions_propagate_unchanged() -> None:
    with pytest.raises(FinvizQueryError):
        run_sync(_raises())


def test_rejects_active_event_loop() -> None:
    async def inside() -> None:
        with pytest.raises(RuntimeError, match="event loop"):
            run_sync(_value())

    asyncio.run(inside())


def test_each_call_gets_a_fresh_loop() -> None:
    # Would raise "cannot be called from a running event loop" if any loop leaked.
    assert run_sync(_value()) == 42
    assert asyncio.run(_value()) == 42
    assert run_sync(_value()) == 42
