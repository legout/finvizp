"""Hermetic RED tests for client-integrated TTL caching and per-client single-flight."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse
from fastreq.exceptions import BackendError

from finvizp.client import FinvizClient
from finvizp.errors import (
    FinvizEntitlementError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizTransportError,
)

BASE = "https://finviz.com"


def _resp(
    body: bytes = b"<html><body>hi</body></html>",
    content_type: str = "text/html",
    url: str = f"{BASE}/quote.ashx",
    status: int = 200,
) -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=status,
        headers={"Content-Type": content_type},
        content=body,
        url=url,
        is_json=False,
    )


class CountingTransport(Backend):
    """Counts backend requests; replays scripted responses/exceptions once each."""

    def __init__(self, *scripted: Any) -> None:
        self.scripted: list[Any] = list(scripted)
        self.calls = 0

    @property
    def name(self) -> str:
        return "counting"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls += 1
        if self.scripted:
            item = self.scripted.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return _resp()

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> CountingTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def supports_http2(self) -> bool:
        return True


class SlowTransport(CountingTransport):
    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        await asyncio.sleep(0.05)
        return await CountingTransport.request(self, config, stream_callback)


def _client(fake: CountingTransport, **kwargs: Any) -> FinvizClient:
    return FinvizClient(transport=fake, retry_attempts=0, retry_backoff=0.0, **kwargs)


# --- TTL / controls -----------------------------------------------------------


async def test_cache_hit_preserves_facts_and_updates_provenance() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    first = await client.fetch("/quote.ashx", params={"t": "AAPL"})
    second = await client.fetch("/quote.ashx", params={"t": "AAPL"})
    assert fake.calls == 1  # one underlying request
    assert first.response_hash == second.response_hash
    assert second.fetched_at == first.fetched_at  # original fetch time survives
    assert second.attempts == first.attempts
    assert second.served_at is not None and second.served_at >= second.fetched_at
    assert second.cache_hit is True and second.stale is False
    assert first.cache_hit is False
    # Different query is a different key.
    await client.fetch("/quote.ashx", params={"t": "MSFT"})
    assert fake.calls == 2


async def test_expired_ttl_refetches() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=0.05)
    await client.fetch("/quote.ashx")
    await asyncio.sleep(0.08)
    await client.fetch("/quote.ashx")
    assert fake.calls == 2
    second_hit = await client.fetch("/quote.ashx")  # re-cached after refetch
    assert second_hit.cache_hit is True


async def test_default_has_no_ttl_and_cache_false_disables() -> None:
    fake = CountingTransport()
    client = _client(fake)
    await client.fetch("/quote.ashx")
    await client.fetch("/quote.ashx")
    assert fake.calls == 2  # default: no TTL -> no caching
    fresh = _client(fake, cache_ttl=60.0)
    await fresh.fetch("/quote.ashx", cache=False)
    await fresh.fetch("/quote.ashx", cache=False)
    assert fake.calls == 4


async def test_refresh_bypasses_and_replaces_entry() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client.fetch("/quote.ashx")
    await client.fetch("/quote.ashx", refresh=True)
    assert fake.calls == 2
    hit = await client.fetch("/quote.ashx")
    assert fake.calls == 2  # refresh replaced the entry
    assert hit.cache_hit is True


async def test_invalidate_and_clear() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client.fetch("/quote.ashx", params={"t": "AAPL"})
    assert client.invalidate("/quote.ashx", params={"t": "AAPL"}) is True
    await client.fetch("/quote.ashx", params={"t": "AAPL"})
    assert fake.calls == 2
    assert client.invalidate("/quote.ashx", params={"t": "AAPL"}) is True
    assert client.invalidate("/nope.ashx") is False
    await client.fetch("/quote.ashx", params={"t": "AAPL"})
    assert fake.calls == 3
    await client.fetch("/quote.ashx", params={"t": "MSFT"})
    assert client.clear_cache() == 2
    assert client.clear_cache() == 0


async def test_cache_disabled_entirely_by_configuration() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0, cache=False)
    await client.fetch("/quote.ashx")
    await client.fetch("/quote.ashx")
    assert fake.calls == 2
    assert client.clear_cache() == 0


# --- key isolation ------------------------------------------------------------


async def test_cache_keys_isolate_access_tier_route_and_profile() -> None:
    fake = CountingTransport()
    authed = _client(fake, auth_cookies={"sid": "abc"}, cache_ttl=60.0)
    public = _client(fake, cache_ttl=60.0)
    await authed.fetch("/quote.ashx")
    await public.fetch("/quote.ashx")
    assert fake.calls == 2  # auth scope never shares a key with public
    await authed.fetch("/quote.ashx")
    assert fake.calls == 2
    # Different route via client construction (a per-call proxy override on an
    # authenticated client is rejected by route pinning).
    via_pool = _client(fake, proxies=["http://pool-9:1"], cache_ttl=60.0)
    await via_pool.fetch("/quote.ashx")
    assert fake.calls == 3  # different route -> different key
    profiled = _client(fake, browser_profile="chrome131", cache_ttl=60.0)
    await profiled.fetch("/quote.ashx")
    assert fake.calls == 4  # different browser identity -> different key


# --- stale-if-error -----------------------------------------------------------


async def test_stale_if_error_disabled_by_default() -> None:
    fake = CountingTransport(_resp(), BackendError("boom"))
    client = _client(fake, cache_ttl=0.05)
    await client.fetch("/quote.ashx")
    await asyncio.sleep(0.08)  # entry is now expired/stale
    with pytest.raises(FinvizTransportError):
        await client.fetch("/quote.ashx")
    assert fake.calls == 2


async def test_explicit_stale_if_error_serves_stale_on_transport_failure() -> None:
    fake = CountingTransport(_resp(), BackendError("boom"))
    client = _client(fake, cache_ttl=0.05, stale_if_error=True)
    await client.fetch("/quote.ashx")
    await asyncio.sleep(0.08)
    stale = await client.fetch("/quote.ashx")  # transport failure -> stale fallback
    assert fake.calls == 2  # the failure was still a real underlying attempt
    assert stale.stale is True and stale.cache_hit is True
    assert stale.served_at is not None and stale.fetched_at < stale.served_at


@pytest.mark.parametrize(
    ("scripted", "error"),
    [
        (_resp(content_type="garbage"), FinvizParseError),
        (_resp(url=f"{BASE}/login.aspx"), FinvizEntitlementError),
        (_resp(status=404), FinvizNotFoundError),
    ],
)
async def test_stale_if_error_never_masks_verdict_errors(
    scripted: NormalizedResponse, error: type[Exception]
) -> None:
    fake = CountingTransport(_resp(), scripted)  # first fetch caches, second fails
    client = _client(fake, cache_ttl=0.05, stale_if_error=True)
    await client.fetch("/quote.ashx")
    await asyncio.sleep(0.08)
    with pytest.raises(error):  # a verdict, never silently replaced by stale data
        await client.fetch("/quote.ashx")
    assert fake.calls == 2


# --- single-flight ------------------------------------------------------------


async def test_concurrent_identical_misses_collapse_to_one_request() -> None:
    slow = SlowTransport()
    client = _client(slow, cache_ttl=60.0)
    results = await asyncio.gather(
        *(client.fetch("/quote.ashx", params={"t": "AAPL"}) for _ in range(8))
    )
    assert slow.calls == 1  # one underlying call, eight waiters
    hashes = {r.response_hash for r in results}
    assert len(hashes) == 1
    assert sum(1 for r in results if r.cache_hit) >= 1  # losers see the winner's entry


async def test_cancelling_one_waiter_does_not_corrupt_the_shared_operation() -> None:
    slow = SlowTransport()
    client = _client(slow, cache_ttl=60.0)
    winner = asyncio.create_task(client.fetch("/quote.ashx"))
    loser = asyncio.create_task(client.fetch("/quote.ashx"))
    await asyncio.sleep(0.01)
    loser.cancel()
    assert (await winner).status_code == 200  # shared operation completed
    with pytest.raises(asyncio.CancelledError):
        await loser
    # The cache still holds a valid entry afterwards.
    again = await client.fetch("/quote.ashx")
    assert slow.calls == 1
    assert again.cache_hit is True


async def test_singleflight_released_after_failure_so_retry_can_succeed() -> None:
    flaky = CountingTransport(BackendError("boom"))
    client = _client(flaky, cache_ttl=60.0)
    with pytest.raises(FinvizTransportError):
        await client.fetch("/quote.ashx")
    ok = await client.fetch("/quote.ashx")  # must not be poisoned by the failed flight
    assert flaky.calls == 2
    assert ok.cache_hit is False
