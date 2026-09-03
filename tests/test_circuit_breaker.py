"""Hermetic tests for the per-client 429 circuit breaker."""

from __future__ import annotations

import time
from typing import Any

import pytest

from finvizp.errors import (
    CircuitOpenError,
    FinvizBlockedError,
    FinvizQueryError,
    FinvizRateLimitError,
)
from tests.test_client import FakeTransport, _client, _resp


def _r429(retry_after: str | None = None) -> Any:
    headers = {"Content-Type": "text/html"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return _resp(429, headers=headers)


async def _trip(client: Any, fake: FakeTransport | None = None) -> None:
    """Issue enough 429ing calls (no internal retries) to trip the circuit."""
    for _ in range(3):
        with pytest.raises(FinvizRateLimitError):
            await client._fetch("/quote.ashx")
    if fake is not None:
        assert len(fake.calls) == 3  # one transport hit per call


async def test_breaker_trips_and_blocks_before_transport() -> None:
    fake = FakeTransport(*([_r429()] * 3))
    client = _client(fake)
    await _trip(client, fake)
    with pytest.raises(CircuitOpenError):
        await client._fetch("/quote.ashx")
    assert len(fake.calls) == 3  # raised without hitting the transport


async def test_circuit_open_error_is_rate_limit_error_with_context() -> None:
    fake = FakeTransport(*([_r429()] * 3))
    client = _client(fake)
    await _trip(client)
    with pytest.raises(CircuitOpenError) as excinfo:
        await client._fetch("/quote.ashx")
    assert isinstance(excinfo.value, FinvizRateLimitError)
    assert excinfo.value.context["retry_after"] >= 0
    assert excinfo.value.context["endpoint"] == "/quote.ashx"


async def test_probe_after_deadline_success_resets() -> None:
    fake = FakeTransport(*([_r429()] * 3), _resp(200, b"<html/>"))
    client = _client(fake)
    await _trip(client)
    assert client._circuit_open_until > 0
    client._circuit_open_until = 0.0  # deadline elapses
    resp = await client._fetch("/quote.ashx")  # half-open probe succeeds
    assert resp.status_code == 200
    assert client._circuit_429s == 0
    assert client._circuit_open_until == 0.0


async def test_probe_after_deadline_429_reopens() -> None:
    fake = FakeTransport(*([_r429()] * 4))
    client = _client(fake)
    await _trip(client, fake)
    client._circuit_open_until = 0.0  # deadline elapses
    with pytest.raises(FinvizRateLimitError):  # probe itself 429s
        await client._fetch("/quote.ashx")
    assert len(fake.calls) == 4
    assert client._circuit_429s == 4  # streak never reset
    assert client._circuit_open_until > 0  # re-opened
    with pytest.raises(CircuitOpenError):
        await client._fetch("/quote.ashx")


async def test_retry_after_sets_cooldown() -> None:
    fake = FakeTransport(*([_r429(retry_after="120")] * 3))
    client = _client(fake)
    await _trip(client)
    remaining = client._circuit_open_until - time.monotonic()
    assert 115 < remaining <= 120


async def test_missing_retry_after_uses_default_cooldown() -> None:
    fake = FakeTransport(*([_r429()] * 3))
    client = _client(fake)
    await _trip(client)
    remaining = client._circuit_open_until - time.monotonic()
    assert 55 < remaining <= 60


async def test_blocked_errors_do_not_touch_the_counter() -> None:
    fake = FakeTransport(*([_resp(403)] * 3))
    client = _client(fake)
    for _ in range(3):
        with pytest.raises(FinvizBlockedError):
            await client._fetch("/quote.ashx")
    assert client._circuit_429s == 0
    assert client._circuit_open_until == 0.0
    assert len(fake.calls) == 3  # every call reached the transport


async def test_success_between_429s_resets_streak() -> None:
    fake = FakeTransport(_r429(), _r429(), _resp(200, b"<html/>"), _r429(), _r429())
    client = _client(fake)
    with pytest.raises(FinvizRateLimitError):
        await client._fetch("/quote.ashx")
    with pytest.raises(FinvizRateLimitError):
        await client._fetch("/quote.ashx")
    resp = await client._fetch("/quote.ashx")  # closes the circuit
    assert resp.status_code == 200
    assert client._circuit_429s == 0
    with pytest.raises(FinvizRateLimitError):
        await client._fetch("/quote.ashx")
    with pytest.raises(FinvizRateLimitError):
        await client._fetch("/quote.ashx")
    assert client._circuit_429s == 2  # fresh streak, circuit still closed
    assert client._circuit_open_until == 0.0


async def test_gate_never_hides_route_validation() -> None:
    fake = FakeTransport(*([_r429()] * 3))
    client = _client(fake)
    await _trip(client)
    with pytest.raises(FinvizQueryError):  # validation wins over the gate
        await client._fetch("https://evil.com/quote.ashx")
    with pytest.raises(CircuitOpenError):
        await client._fetch("/quote.ashx")
