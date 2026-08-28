"""Hermetic RED tests for client-integrated TTL caching and per-client single-flight."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse
from fastreq.exceptions import BackendError

from finvizp.cache import ResultCache
from finvizp.client import FinvizClient
from finvizp.errors import (
    FinvizEntitlementError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizQueryError,
    FinvizTransportError,
)

BASE = "https://finviz.com"


def _resp(
    body: bytes | None = None,
    content_type: str = "application/json",
    url: str = f"{BASE}/api/quote",
    status: int = 200,
) -> NormalizedResponse:
    if body is None:
        body = json.dumps({"quote": {"t": "AAPL", "price": 100}}).encode()
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
    first = await client._endpoint_op("/quote.ashx", query={"t": "AAPL"})()
    second = await client._endpoint_op("/quote.ashx", query={"t": "AAPL"})()
    assert fake.calls == 1  # one underlying request
    assert first.response_hash == second.response_hash
    assert second.fetched_at == first.fetched_at  # original fetch time survives
    assert second.attempts == first.attempts
    assert second.served_at is not None and second.served_at >= second.fetched_at
    assert second.cache_hit is True and second.stale is False
    assert first.cache_hit is False
    # Different query is a different key.
    await client._endpoint_op("/quote.ashx", query={"t": "MSFT"})()
    assert fake.calls == 2


async def test_expired_ttl_refetches() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=0.05)
    await client._endpoint_op("/quote.ashx")()
    await asyncio.sleep(0.08)
    await client._endpoint_op("/quote.ashx")()
    assert fake.calls == 2
    second_hit = await client._endpoint_op("/quote.ashx")()  # re-cached after refetch
    assert second_hit.cache_hit is True


async def test_default_has_no_ttl_and_cache_false_disables() -> None:
    fake = CountingTransport()
    client = _client(fake)
    await client._endpoint_op("/quote.ashx")()
    await client._endpoint_op("/quote.ashx")()
    assert fake.calls == 2  # default: no TTL -> no caching
    fresh = _client(fake, cache_ttl=60.0)
    await fresh._cached_fetch("/quote.ashx", cache=False)
    await fresh._cached_fetch("/quote.ashx", cache=False)
    assert fake.calls == 4


async def test_refresh_bypasses_and_replaces_entry() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client._endpoint_op("/quote.ashx")()
    await client._cached_fetch("/quote.ashx", refresh=True)
    assert fake.calls == 2
    hit = await client._endpoint_op("/quote.ashx")()
    assert fake.calls == 2  # refresh replaced the entry
    assert hit.cache_hit is True


async def test_invalidate_and_clear() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client._endpoint_op("/quote.ashx", query={"t": "AAPL"})()
    assert client.invalidate("/quote.ashx", params={"t": "AAPL"}) is True
    await client._endpoint_op("/quote.ashx", query={"t": "AAPL"})()
    assert fake.calls == 2
    assert client.invalidate("/quote.ashx", params={"t": "AAPL"}) is True
    assert client.invalidate("/nope.ashx") is False
    await client._endpoint_op("/quote.ashx", query={"t": "AAPL"})()
    assert fake.calls == 3
    await client._endpoint_op("/quote.ashx", query={"t": "MSFT"})()
    assert client.clear_cache() == 2
    assert client.clear_cache() == 0


async def test_cache_disabled_entirely_by_configuration() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0, cache=False)
    await client._endpoint_op("/quote.ashx")()
    await client._endpoint_op("/quote.ashx")()
    assert fake.calls == 2
    assert client.clear_cache() == 0


# --- key isolation ------------------------------------------------------------


async def test_cache_keys_isolate_access_tier_route_and_profile() -> None:
    fake = CountingTransport()
    authed = _client(fake, auth_cookies={"sid": "abc"}, cache_ttl=60.0)
    public = _client(fake, cache_ttl=60.0)
    await authed._endpoint_op("/quote.ashx")()
    await public._endpoint_op("/quote.ashx")()
    assert fake.calls == 2  # auth scope never shares a key with public
    await authed._endpoint_op("/quote.ashx")()
    assert fake.calls == 2
    # Different route via client construction (a per-call proxy override on an
    # authenticated client is rejected by route pinning).
    via_pool = _client(fake, proxies=["http://pool-9:1"], cache_ttl=60.0)
    await via_pool._endpoint_op("/quote.ashx")()
    assert fake.calls == 3  # different route -> different key
    profiled = _client(fake, browser_profile="chrome131", cache_ttl=60.0)
    await profiled._endpoint_op("/quote.ashx")()
    assert fake.calls == 4  # different browser identity -> different key


# --- stale-if-error -----------------------------------------------------------


async def test_stale_if_error_disabled_by_default() -> None:
    fake = CountingTransport(_resp(), BackendError("boom"))
    client = _client(fake, cache_ttl=0.05)
    await client._endpoint_op("/quote.ashx")()
    await asyncio.sleep(0.08)  # entry is now expired/stale
    with pytest.raises(FinvizTransportError):
        await client._endpoint_op("/quote.ashx")()
    assert fake.calls == 2


async def test_explicit_stale_if_error_serves_stale_on_transport_failure() -> None:
    fake = CountingTransport(_resp(), BackendError("boom"))
    client = _client(fake, cache_ttl=0.05, stale_if_error=True)
    await client._endpoint_op("/quote.ashx")()
    await asyncio.sleep(0.08)
    stale = await client._endpoint_op("/quote.ashx")()  # transport failure -> stale fallback
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
    await client._endpoint_op("/quote.ashx")()
    await asyncio.sleep(0.08)
    with pytest.raises(error):  # a verdict, never silently replaced by stale data
        await client._endpoint_op("/quote.ashx")()
    assert fake.calls == 2


# --- single-flight ------------------------------------------------------------


async def test_concurrent_identical_misses_collapse_to_one_request() -> None:
    slow = SlowTransport()
    client = _client(slow, cache_ttl=60.0)
    results = await asyncio.gather(
        *(client._endpoint_op("/quote.ashx", query={"t": "AAPL"})() for _ in range(8))
    )
    assert slow.calls == 1  # one underlying call, eight waiters
    hashes = {r.response_hash for r in results}
    assert len(hashes) == 1
    assert sum(1 for r in results if r.cache_hit) >= 1  # losers see the winner's entry


async def test_cancelling_one_waiter_does_not_corrupt_the_shared_operation() -> None:
    slow = SlowTransport()
    client = _client(slow, cache_ttl=60.0)
    winner = asyncio.create_task(client._endpoint_op("/quote.ashx")())
    loser = asyncio.create_task(client._endpoint_op("/quote.ashx")())
    await asyncio.sleep(0.01)
    loser.cancel()
    assert (await winner).status_code == 200  # shared operation completed
    with pytest.raises(asyncio.CancelledError):
        await loser
    # The cache still holds a valid entry afterwards.
    again = await client._endpoint_op("/quote.ashx")()
    assert slow.calls == 1
    assert again.cache_hit is True


async def test_singleflight_released_after_failure_so_retry_can_succeed() -> None:
    flaky = CountingTransport(BackendError("boom"))
    client = _client(flaky, cache_ttl=60.0)
    with pytest.raises(FinvizTransportError):
        await client._endpoint_op("/quote.ashx")()
    ok = await client._endpoint_op("/quote.ashx")()  # must not be poisoned by the failed flight
    assert flaky.calls == 2
    assert ok.cache_hit is False


# --- review regressions ---------------------------------------------------------


async def test_creator_cancellation_does_not_spawn_a_second_backend_call() -> None:
    slow = SlowTransport()
    client = _client(slow, cache_ttl=60.0)
    leader = asyncio.create_task(client._endpoint_op("/quote.ashx")())
    await asyncio.sleep(0.01)  # leader has registered its flight
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    await client._endpoint_op("/quote.ashx")()  # immediately: must join the orphaned flight
    await asyncio.sleep(0.1)  # let the shielded miss finish before asserting
    assert slow.calls == 1  # the completed flight still serves the later caller


async def test_fresh_hits_refresh_lru_recency_and_hit_stats() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0, cache_max_entries=2)
    await client._endpoint_op("/quote.ashx", query={"t": "A"})()  # miss
    await client._endpoint_op("/quote.ashx", query={"t": "B"})()  # miss
    await client._endpoint_op("/quote.ashx", query={"t": "A"})()  # hit -> A becomes MRU
    await client._endpoint_op("/quote.ashx", query={"t": "C"})()  # miss -> evicts B, keeps A
    await client._endpoint_op("/quote.ashx", query={"t": "A"})()  # hit: A survived its re-read
    assert fake.calls == 3
    assert client._cache is not None
    assert client._cache.stats()["hits"] == 2  # fresh hits counted


async def test_json_responses_are_byte_bounded() -> None:
    payload = json.dumps({"data": "x" * 300_000}).encode()
    fake = CountingTransport(
        *[
            _resp(
                body=payload,
                content_type="application/json",
                url=f"{BASE}/api/quote",
            )
            for _ in range(4)
        ]
    )
    client = _client(fake, cache_ttl=60.0, cache_max_bytes=400_000)
    for i in range(4):
        await client._endpoint_op("/api/quote", query={"t": f"S{i}"})()
    assert client._cache is not None
    stats = client._cache.stats()
    assert stats["approx_bytes"] <= 400_000
    assert stats["entries"] < 4  # big JSON entries actually evicted


async def test_cached_json_payload_is_immutable_across_hits() -> None:
    payload = {"price": 100, "tags": ["a"]}
    fake = CountingTransport(
        _resp(
            body=json.dumps(payload).encode(),
            content_type="application/json",
            url=f"{BASE}/api/quote",
        )
    )
    client = _client(fake, cache_ttl=60.0)
    first = await client._endpoint_op("/api/quote")()
    first.data["price"] = 999  # type: ignore[index]
    first.data["tags"].append("mutated")  # type: ignore[index]
    second = await client._endpoint_op("/api/quote")()
    assert fake.calls == 1
    assert second.data == {"price": 100, "tags": ["a"]}


async def test_injected_caller_cache_adapter_is_sufficient() -> None:
    class DocumentedAdapter:
        """Implements exactly the documented seam: get/set/delete/clear/stats/make_key."""

        def __init__(self) -> None:
            self.inner = ResultCache()

        def get(self, key: str) -> Any:
            return self.inner.get(key)

        def set(self, key: str, entry: Any) -> None:
            self.inner.set(key, entry)

        def delete(self, key: str) -> bool:
            return self.inner.delete(key)

        def clear(self) -> int:
            return self.inner.clear()

        def stats(self) -> Any:
            return self.inner.stats()

        def make_key(self, **facets: Any) -> str:
            return self.inner.make_key(**facets)

    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0, cache=DocumentedAdapter())
    first = await client._endpoint_op("/quote.ashx")()
    second = await client._endpoint_op("/quote.ashx")()
    assert fake.calls == 1
    assert second.cache_hit is True
    assert first.response_hash == second.response_hash


async def test_provenance_reports_cache_age_on_hit_and_stale() -> None:
    fake = CountingTransport(_resp(), _resp(), BackendError("boom"))
    client = _client(fake, cache_ttl=0.05, stale_if_error=True)
    fresh = await client._endpoint_op("/quote.ashx")()
    assert fresh.cache_hit is False
    assert fresh.cache_age is None  # a miss has no cache age
    await asyncio.sleep(0.08)  # first entry is now stale
    hit = await client._endpoint_op("/quote.ashx")()  # miss -> underlying refetch
    assert fake.calls == 2 and hit.cache_hit is False
    aged = await client._endpoint_op("/quote.ashx")()  # fresh hit on the second entry
    assert aged.cache_hit is True
    assert aged.cache_age is not None and 0.0 <= aged.cache_age < 0.05
    await asyncio.sleep(0.08)  # second entry is now stale too
    stale = await client._endpoint_op("/quote.ashx")()  # transport failure -> stale fallback
    assert stale.stale is True and stale.cache_hit is True
    assert stale.cache_age is not None and stale.cache_age >= 0.08


# --- second review round regressions ------------------------------------------


async def test_no_public_arbitrary_request_method_exists() -> None:
    """Card 0.1-C constraint: no public arbitrary-request method on the client."""
    assert {n for n in dir(FinvizClient) if not n.startswith("_")} == {
        "close",
        "invalidate",
        "clear_cache",
    }
    assert not any(hasattr(FinvizClient, n) for n in ("fetch", "get", "request"))


async def test_cache_adapter_never_receives_raw_authenticated_body() -> None:
    """Only parsed, structured endpoint payloads may enter the cache."""

    class RecordingAdapter:
        """Documented seam that records every stored payload."""

        def __init__(self) -> None:
            self.inner = ResultCache()
            self.stored: list[Any] = []

        def get(self, key: str) -> Any:
            return self.inner.get(key)

        def set(self, key: str, entry: Any) -> None:
            self.stored.append(entry.response.data)
            self.inner.set(key, entry)

        def delete(self, key: str) -> bool:
            return self.inner.delete(key)

        def clear(self) -> int:
            return self.inner.clear()

        def stats(self) -> Any:
            return self.inner.stats()

        def make_key(self, **facets: Any) -> str:
            return self.inner.make_key(**facets)

    adapter = RecordingAdapter()
    fake = CountingTransport(_resp(body=b"<html>SESSION-PAGE</html>"))
    client = _client(fake, auth_cookies={"sid": "abc"}, cache=adapter, cache_ttl=60.0)
    op = client._endpoint_op("/quote.ashx", query={"t": "AAPL"})  # type: ignore[attr-defined]
    with pytest.raises((FinvizParseError, FinvizQueryError)):
        await op()
    assert adapter.stored == []  # raw HTML body never entered the cache

    # A parsed structured endpoint result is the only cacheable payload shape.
    json_body = json.dumps({"quote": {"t": "AAPL", "price": 100}}).encode()
    fake2 = CountingTransport(
        _resp(body=json_body, content_type="application/json", url=f"{BASE}/api/quote")
    )
    adapter2 = RecordingAdapter()
    client2 = _client(fake2, auth_cookies={"sid": "abc"}, cache=adapter2, cache_ttl=60.0)
    op2 = client2._endpoint_op("/api/quote", query={"t": "AAPL"})  # type: ignore[attr-defined]
    result = await op2()
    assert fake2.calls == 1
    assert result.data == {"quote": {"t": "AAPL", "price": 100}}
    assert adapter2.stored and adapter2.stored[0] == result.data


async def test_stale_joiner_preserves_stale_and_age_provenance() -> None:
    """Joiners of a stale-if-error flight carry the leader's stale/age facts."""
    stale_body = json.dumps({"quote": {"t": "AAPL"}}).encode()

    class SlowFailingTransport(CountingTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            await asyncio.sleep(0.05)
            return await CountingTransport.request(self, config, stream_callback)

    transport = SlowFailingTransport(
        _resp(body=stale_body, content_type="application/json", url=f"{BASE}/api/quote"),
        BackendError("boom"),
    )
    client = _client(transport, cache_ttl=0.05, stale_if_error=True)
    await client._endpoint_op("/api/quote", query={"t": "AAPL"})()  # type: ignore[attr-defined]
    await asyncio.sleep(0.08)  # entry is now stale

    def _op() -> Any:
        return client._endpoint_op("/api/quote", query={"t": "AAPL"})()  # type: ignore[attr-defined]

    leader = asyncio.ensure_future(_op())
    joiner = asyncio.ensure_future(_op())
    leader_result = await leader
    joiner_result = await joiner
    assert transport.calls == 2  # stale fallback was one real underlying attempt
    assert leader_result.stale is True and leader_result.cache_hit is True
    assert leader_result.cache_age is not None and leader_result.cache_age >= 0.08
    # Same payload, same provenance facts; only served_at differs.
    assert joiner_result.stale is True
    assert joiner_result.cache_hit is True
    assert joiner_result.cache_age is not None
    assert abs(joiner_result.cache_age - leader_result.cache_age) < 1.0
    assert joiner_result.fetched_at == leader_result.fetched_at


async def test_invalidate_matches_per_call_proxy_route_keys() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client._endpoint_op(  # type: ignore[attr-defined]
        "/api/quote", query={"t": "AAPL"}, proxy="http://proxy.example:8080"
    )()
    assert fake.calls == 1
    # Default-route invalidation misses; route-matched invalidation hits.
    assert client.invalidate("/api/quote", params={"t": "AAPL"}) is False  # type: ignore[attr-defined]
    assert client._cache is not None and client._cache.stats()["entries"] == 1
    assert (  # type: ignore[attr-defined]
        client.invalidate("/api/quote", params={"t": "AAPL"}, proxy="http://proxy.example:8080")
        is True
    )
    assert client._cache is not None and client._cache.stats()["entries"] == 0
