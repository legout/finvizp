"""Hermetic RED tests for client-integrated TTL caching and per-client single-flight."""

from __future__ import annotations

import asyncio
import gc
import json
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import pytest
from fastreq.backends.base import Backend, NormalizedResponse
from fastreq.exceptions import BackendError

from finvizp.cache import CacheEntry, ResultCache
from finvizp.client import ClientResponse, FinvizClient, _payload_size
from finvizp.errors import (
    FinvizEntitlementError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizTransportError,
)
from finvizp.models import QuoteBundle
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

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


class CapturingTransport(CountingTransport):
    """Records each request's proxy to expose the route the transport used."""

    def __init__(self, *scripted: Any) -> None:
        super().__init__(*scripted)
        self.proxies: list[str | None] = []

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.proxies.append(config.proxy)
        return await CountingTransport.request(self, config, stream_callback)


def _client(fake: CountingTransport, **kwargs: Any) -> FinvizClient:
    return FinvizClient(transport=fake, retry_attempts=0, retry_backoff=0.0, **kwargs)


def _meta(response: ClientResponse) -> ResultMetadata:
    """Shared reviewed-parser metadata: provenance carried into the result."""
    return ResultMetadata(
        endpoint=response.endpoint,
        status=ResultStatus.COMPLETE,
        access_tier=response.access_tier,
        fetched_at=response.fetched_at,
        query=dict(response.query),
        attempts=response.attempts,
        response_hash=response.response_hash,
        route_fingerprint=response.route_fingerprint,
    )


def _parsed_quote(response: ClientResponse) -> FetchResult[dict[str, Any]]:
    """Example reviewed endpoint parser: raw envelope in, normalized result out."""
    return FetchResult(
        {"symbols": ["AAPL"], "source_hash": response.response_hash},
        _meta(response),
    )


def _json_only_parser(response: ClientResponse) -> FetchResult[dict[str, Any]]:
    """Reviewed endpoint parser that only accepts structured JSON bodies."""
    if response.content_kind != "json":
        raise FinvizParseError(f"endpoint parses JSON only, got raw {response.content_kind} body")
    return _parsed_quote(response)


def _rows_parser(response: ClientResponse) -> FetchResult[dict[str, Any]]:
    """Reviewed parser for a row payload: extracts provider rows into a list."""
    return FetchResult({"rows": tuple(response.data["rows"])}, _meta(response))


class RecordingAdapter:
    """Documented seam that records every stored cache entry."""

    def __init__(self) -> None:
        self.inner = ResultCache()
        self.entries: list[Any] = []

    def get(self, key: str) -> Any:
        return self.inner.get(key)

    def set(self, key: str, entry: Any) -> None:
        self.entries.append(entry)
        self.inner.set(key, entry)

    def delete(self, key: str) -> bool:
        return self.inner.delete(key)

    def clear(self) -> int:
        return self.inner.clear()

    def stats(self) -> Any:
        return self.inner.stats()

    def make_key(self, **facets: Any) -> str:
        return self.inner.make_key(**facets)


# --- TTL / controls -----------------------------------------------------------


async def test_cache_hit_preserves_facts_and_updates_provenance() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    first = await client._endpoint_op("/quote.ashx", query={"t": "AAPL"}, parse=_parsed_quote)()
    second = await client._endpoint_op("/quote.ashx", query={"t": "AAPL"}, parse=_parsed_quote)()
    assert fake.calls == 1  # one underlying request
    assert isinstance(first, FetchResult) and isinstance(second, FetchResult)
    assert second.metadata.response_hash == first.metadata.response_hash
    assert second.metadata.fetched_at == first.metadata.fetched_at  # original fetch time survives
    assert second.metadata.attempts == first.metadata.attempts
    meta = second.metadata
    assert meta.served_at is not None and meta.served_at >= meta.fetched_at
    assert meta.cache_hit is True and meta.stale is False
    assert first.metadata.cache_hit is False
    # Different query is a different key.
    await client._endpoint_op("/quote.ashx", query={"t": "MSFT"}, parse=_parsed_quote)()
    assert fake.calls == 2


async def test_expired_ttl_refetches() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=0.05)
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    await asyncio.sleep(0.08)
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 2
    second_hit = await client._endpoint_op(
        "/quote.ashx", parse=_parsed_quote
    )()  # re-cached after refetch
    assert second_hit.metadata.cache_hit is True


async def test_default_has_no_ttl_and_cache_false_disables() -> None:
    fake = CountingTransport()
    client = _client(fake)
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 2  # default: no TTL -> no caching
    fresh = _client(fake, cache_ttl=60.0)
    await fresh._cached_fetch("/quote.ashx", cache=False, parse=_parsed_quote)
    await fresh._cached_fetch("/quote.ashx", cache=False, parse=_parsed_quote)
    assert fake.calls == 4


async def test_refresh_bypasses_and_replaces_entry() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    await client._cached_fetch("/quote.ashx", refresh=True, parse=_parsed_quote)
    assert fake.calls == 2
    hit = await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 2  # refresh replaced the entry
    assert hit.metadata.cache_hit is True


async def test_invalidate_and_clear() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client._endpoint_op("/quote.ashx", query={"t": "AAPL"}, parse=_parsed_quote)()
    assert client.invalidate("/quote.ashx", params={"t": "AAPL"}) is True
    await client._endpoint_op("/quote.ashx", query={"t": "AAPL"}, parse=_parsed_quote)()
    assert fake.calls == 2
    assert client.invalidate("/quote.ashx", params={"t": "AAPL"}) is True
    assert client.invalidate("/nope.ashx") is False
    await client._endpoint_op("/quote.ashx", query={"t": "AAPL"}, parse=_parsed_quote)()
    assert fake.calls == 3
    await client._endpoint_op("/quote.ashx", query={"t": "MSFT"}, parse=_parsed_quote)()
    assert client.clear_cache() == 2
    assert client.clear_cache() == 0


async def test_cache_disabled_entirely_by_configuration() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0, cache=False)
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 2
    assert client.clear_cache() == 0


# --- key isolation ------------------------------------------------------------


async def test_cache_keys_isolate_access_tier_route_and_profile() -> None:
    fake = CountingTransport()
    authed = _client(fake, auth_cookies={"sid": "abc"}, cache_ttl=60.0)
    public = _client(fake, cache_ttl=60.0)
    await authed._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    await public._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 2  # auth scope never shares a key with public
    await authed._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 2
    # Different route via client construction (a per-call proxy override on an
    # authenticated client is rejected by route pinning).
    via_pool = _client(fake, proxies=["http://pool-9:1"], cache_ttl=60.0)
    await via_pool._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 3  # different route -> different key
    profiled = _client(fake, browser_profile="chrome131", cache_ttl=60.0)
    await profiled._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 4  # different browser identity -> different key


async def test_shared_cache_isolates_distinct_authenticated_scopes() -> None:
    """Two authenticated clients with different cookies must never share entries."""
    shared: ResultCache = ResultCache()
    fake1 = CountingTransport()
    fake2 = CountingTransport()
    fake3 = CountingTransport()
    first = _client(fake1, auth_cookies={"sid": "aaa"}, cache=shared, cache_ttl=60.0)
    second = _client(fake2, auth_cookies={"sid": "bbb"}, cache=shared, cache_ttl=60.0)
    await first._endpoint_op("/api/quote", parse=_parsed_quote)()
    await second._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake1.calls == 1 and fake2.calls == 1  # distinct scopes: no sharing
    assert shared.stats()["entries"] == 2
    await first._endpoint_op("/api/quote", parse=_parsed_quote)()  # same scope may share
    assert fake1.calls == 1
    same_scope = _client(fake3, auth_cookies={"sid": "aaa"}, cache=shared, cache_ttl=60.0)
    await same_scope._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake3.calls == 0  # identical auth state shares the entry


# --- stale-if-error -----------------------------------------------------------


async def test_stale_if_error_disabled_by_default() -> None:
    fake = CountingTransport(_resp(), BackendError("boom"))
    client = _client(fake, cache_ttl=0.05)
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    await asyncio.sleep(0.08)  # entry is now expired/stale
    with pytest.raises(FinvizTransportError):
        await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 2


async def test_explicit_stale_if_error_serves_stale_on_transport_failure() -> None:
    fake = CountingTransport(_resp(), BackendError("boom"))
    client = _client(fake, cache_ttl=0.05, stale_if_error=True)
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    await asyncio.sleep(0.08)
    stale = await client._endpoint_op(
        "/quote.ashx", parse=_parsed_quote
    )()  # transport failure -> stale fallback
    assert fake.calls == 2  # the failure was still a real underlying attempt
    assert stale.metadata.stale is True and stale.metadata.cache_hit is True
    assert stale.metadata.served_at is not None
    assert stale.metadata.fetched_at < stale.metadata.served_at


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
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    await asyncio.sleep(0.08)
    with pytest.raises(error):  # a verdict, never silently replaced by stale data
        await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 2


# --- single-flight ------------------------------------------------------------


async def test_concurrent_identical_misses_collapse_to_one_request() -> None:
    slow = SlowTransport()
    client = _client(slow, cache_ttl=60.0)
    results = await asyncio.gather(
        *(
            client._endpoint_op("/quote.ashx", query={"t": "AAPL"}, parse=_parsed_quote)()
            for _ in range(8)
        )
    )
    assert slow.calls == 1  # one underlying call, eight waiters
    hashes = {r.metadata.response_hash for r in results}
    assert len(hashes) == 1
    assert sum(1 for r in results if r.metadata.cache_hit) >= 1  # losers see the winner's entry


async def test_cancelling_one_waiter_does_not_corrupt_the_shared_operation() -> None:
    slow = SlowTransport()
    client = _client(slow, cache_ttl=60.0)
    winner = asyncio.create_task(client._endpoint_op("/quote.ashx", parse=_parsed_quote)())
    loser = asyncio.create_task(client._endpoint_op("/quote.ashx", parse=_parsed_quote)())
    await asyncio.sleep(0.01)
    loser.cancel()
    completed = await winner
    assert completed.metadata.cache_hit is False  # shared operation completed
    with pytest.raises(asyncio.CancelledError):
        await loser
    # The cache still holds a valid entry afterwards.
    again = await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert slow.calls == 1
    assert again.metadata.cache_hit is True


async def test_singleflight_released_after_failure_so_retry_can_succeed() -> None:
    flaky = CountingTransport(BackendError("boom"))
    client = _client(flaky, cache_ttl=60.0)
    with pytest.raises(FinvizTransportError):
        await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    ok = await client._endpoint_op(
        "/quote.ashx", parse=_parsed_quote
    )()  # must not be poisoned by the failed flight
    assert flaky.calls == 2
    assert ok.metadata.cache_hit is False


# --- review regressions ---------------------------------------------------------


async def test_creator_cancellation_does_not_spawn_a_second_backend_call() -> None:
    slow = SlowTransport()
    client = _client(slow, cache_ttl=60.0)
    leader = asyncio.create_task(client._endpoint_op("/quote.ashx", parse=_parsed_quote)())
    await asyncio.sleep(0.01)  # leader has registered its flight
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()  # immediately: must join
    await asyncio.sleep(0.1)  # let the shielded miss finish before asserting
    assert slow.calls == 1  # the completed flight still serves the later caller


async def test_fresh_hits_refresh_lru_recency_and_hit_stats() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0, cache_max_entries=2)
    await client._endpoint_op("/quote.ashx", query={"t": "A"}, parse=_parsed_quote)()  # miss
    await client._endpoint_op("/quote.ashx", query={"t": "B"}, parse=_parsed_quote)()  # miss
    await client._endpoint_op("/quote.ashx", query={"t": "A"}, parse=_parsed_quote)()  # hit
    await client._endpoint_op("/quote.ashx", query={"t": "C"}, parse=_parsed_quote)()  # miss
    await client._endpoint_op("/quote.ashx", query={"t": "A"}, parse=_parsed_quote)()  # hit
    assert fake.calls == 3
    assert client._cache is not None
    assert client._cache.stats()["hits"] == 2  # fresh hits counted


async def test_json_responses_are_byte_bounded() -> None:
    payload = json.dumps({"rows": ["x" * 300_000]}).encode()
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
        await client._endpoint_op("/api/quote", query={"t": f"S{i}"}, parse=_rows_parser)()
    assert client._cache is not None
    stats = client._cache.stats()
    assert stats["approx_bytes"] <= 400_000
    assert stats["entries"] < 4  # big parsed entries actually evicted


def test_arrow_tables_are_sized_by_table_nbytes() -> None:
    table = pa.table({"x": pa.array(["y" * 1000] * 10)})  # ~10 KB of Arrow buffers
    assert table.nbytes >= 10_000
    assert _payload_size(table) >= table.nbytes  # not a str() round-trip


def test_compound_bundle_tables_count_toward_payload_size() -> None:
    big = pa.table({"x": pa.array(["y" * 1000] * 10)})
    bundle = QuoteBundle(symbol="AAPL", fetched_at=datetime.now(UTC), snapshot=big)
    assert _payload_size(bundle) >= big.nbytes


def test_mapping_nested_arrow_tables_count_toward_payload_size() -> None:
    big = pa.table({"x": pa.array(["y" * 1000] * 10)})
    assert _payload_size({"summary": big}) >= big.nbytes


def test_sequence_nested_arrow_tables_count_toward_payload_size() -> None:
    big = pa.table({"x": pa.array(["y" * 1000] * 10)})
    assert _payload_size((big, big)) >= 2 * big.nbytes


def test_snapshot_tables_mapping_counts_toward_payload_size() -> None:
    big = pa.table({"x": pa.array(["y" * 1000] * 10)})
    bundle = QuoteBundle(
        symbol="AAPL",
        fetched_at=datetime.now(UTC),
        snapshot_tables={"summary": big},
    )
    assert _payload_size(bundle) >= big.nbytes


async def test_overbudget_nested_table_bundle_is_not_retained() -> None:
    big = pa.table({"x": pa.array(["y" * 1000] * 10)})
    assert big.nbytes >= 10_000

    def _bundle_parser(response: ClientResponse) -> FetchResult[Any]:
        bundle = QuoteBundle(
            symbol="AAPL",
            fetched_at=datetime.now(UTC),
            snapshot_tables={"summary": big},
        )
        return FetchResult(bundle, _meta(response))

    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0, cache_max_bytes=8_000)
    op = client._endpoint_op("/api/quote", query={"t": "AAPL"}, parse=_bundle_parser)
    await op()
    assert client._cache is not None
    stats = client._cache.stats()
    assert stats["approx_bytes"] <= 8_000  # 10 KB nested table must not be resident
    assert stats["entries"] == 0
    assert fake.calls == 1


async def test_overbudget_arrow_results_are_not_retained() -> None:
    table = pa.table({"x": pa.array(["y" * 1000] * 10)})
    assert table.nbytes >= 10_000

    def _arrow_parser(response: ClientResponse) -> FetchResult[Any]:
        return FetchResult(table, _meta(response))

    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0, cache_max_bytes=8_000)
    op = client._endpoint_op("/api/quote", query={"t": "AAPL"}, parse=_arrow_parser)
    await op()
    assert client._cache is not None
    stats = client._cache.stats()
    assert stats["approx_bytes"] <= 8_000  # 10 KB table must not be resident
    assert stats["entries"] == 0
    assert fake.calls == 1


async def test_cached_parsed_payload_is_immutable_across_hits() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    op = client._endpoint_op("/api/quote", query={"t": "AAPL"}, parse=_parsed_quote)
    first = await op()
    with pytest.raises(TypeError):
        first.data["symbols"] = ("mutated",)  # type: ignore[index]
    second = await op()
    assert fake.calls == 1
    # The payload survived the first serve untouched.
    assert dict(second.data) == dict(first.data)
    assert second.data["source_hash"] == first.metadata.response_hash


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
    first = await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    second = await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fake.calls == 1
    assert second.metadata.cache_hit is True
    assert first.metadata.response_hash == second.metadata.response_hash


async def test_provenance_reports_cache_age_on_hit_and_stale() -> None:
    fake = CountingTransport(_resp(), _resp(), BackendError("boom"))
    client = _client(fake, cache_ttl=0.05, stale_if_error=True)
    fresh = await client._endpoint_op("/quote.ashx", parse=_parsed_quote)()
    assert fresh.metadata.cache_hit is False
    assert fresh.metadata.cache_age is None  # a miss has no cache age
    await asyncio.sleep(0.08)  # first entry is now stale
    hit = await client._endpoint_op(
        "/quote.ashx", parse=_parsed_quote
    )()  # miss -> underlying refetch
    assert fake.calls == 2 and hit.metadata.cache_hit is False
    aged = await client._endpoint_op(
        "/quote.ashx", parse=_parsed_quote
    )()  # fresh hit on the second entry
    assert aged.metadata.cache_hit is True
    assert aged.metadata.cache_age is not None
    assert 0.0 <= aged.metadata.cache_age < 0.05
    await asyncio.sleep(0.08)  # second entry is now stale too
    stale = await client._endpoint_op(
        "/quote.ashx", parse=_parsed_quote
    )()  # transport failure -> stale fallback
    assert stale.metadata.stale is True and stale.metadata.cache_hit is True
    assert stale.metadata.cache_age is not None and stale.metadata.cache_age >= 0.08


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
    """Only parsed, structured endpoint results may enter the cache."""
    adapter = RecordingAdapter()
    fake = CountingTransport(_resp(body=b"<html>SESSION-PAGE</html>", content_type="text/html"))
    client = _client(fake, auth_cookies={"sid": "abc"}, cache=adapter, cache_ttl=60.0)
    op = client._endpoint_op("/quote.ashx", query={"t": "AAPL"}, parse=_json_only_parser)
    with pytest.raises(FinvizParseError):
        await op()
    assert adapter.entries == []  # raw HTML body never entered the cache

    # A parsed structured endpoint result is the only cacheable payload shape.
    json_body = json.dumps({"quote": {"t": "AAPL", "price": 100}}).encode()
    fake2 = CountingTransport(
        _resp(body=json_body, content_type="application/json", url=f"{BASE}/api/quote")
    )
    adapter2 = RecordingAdapter()
    client2 = _client(fake2, auth_cookies={"sid": "abc"}, cache=adapter2, cache_ttl=60.0)
    op2 = client2._endpoint_op("/api/quote", query={"t": "AAPL"}, parse=_parsed_quote)
    result = await op2()
    assert fake2.calls == 1
    assert dict(result.data) == {
        "symbols": ("AAPL",),
        "source_hash": result.metadata.response_hash,
    }
    assert adapter2.entries and isinstance(adapter2.entries[0], CacheEntry)
    assert isinstance(adapter2.entries[0].result, FetchResult)
    assert adapter2.entries[0].result.data == result.data


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
    await client._endpoint_op("/api/quote", query={"t": "AAPL"}, parse=_parsed_quote)()
    await asyncio.sleep(0.08)  # entry is now stale

    def _op() -> Coroutine[Any, Any, FetchResult[Any]]:
        return client._endpoint_op("/api/quote", query={"t": "AAPL"}, parse=_parsed_quote)()

    leader = asyncio.ensure_future(_op())
    joiner = asyncio.ensure_future(_op())
    leader_result = await leader
    joiner_result = await joiner
    assert transport.calls == 2  # stale fallback was one real underlying attempt
    assert leader_result.metadata.stale is True and leader_result.metadata.cache_hit is True
    assert leader_result.metadata.cache_age is not None
    assert leader_result.metadata.cache_age >= 0.08
    # Same payload, same provenance facts; only served_at differs.
    assert joiner_result.metadata.stale is True
    assert joiner_result.metadata.cache_hit is True
    assert joiner_result.metadata.cache_age is not None
    assert abs(joiner_result.metadata.cache_age - leader_result.metadata.cache_age) < 1.0
    assert joiner_result.metadata.fetched_at == leader_result.metadata.fetched_at


async def test_invalidate_matches_per_call_proxy_route_keys() -> None:
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client._endpoint_op(
        "/api/quote", query={"t": "AAPL"}, proxy="http://proxy.example:8080", parse=_parsed_quote
    )()
    assert fake.calls == 1
    # Default-route invalidation misses; route-matched invalidation hits.
    assert client.invalidate("/api/quote", params={"t": "AAPL"}) is False
    assert client._cache is not None and client._cache.stats()["entries"] == 1
    assert (
        client.invalidate("/api/quote", params={"t": "AAPL"}, proxy="http://proxy.example:8080")
        is True
    )
    assert client._cache is not None and client._cache.stats()["entries"] == 0


# --- third review round regressions --------------------------------------------


async def test_cache_stores_parsed_fetchresult_never_raw_envelope() -> None:
    """Cache entries and returned values are immutable FetchResults, not envelopes."""
    adapter = RecordingAdapter()
    fake = CountingTransport()
    client = _client(fake, cache=adapter, cache_ttl=60.0)
    op = client._endpoint_op("/api/quote", query={"t": "AAPL"}, parse=_parsed_quote)
    result = await op()
    second = await op()
    assert fake.calls == 1
    assert isinstance(result, FetchResult) and isinstance(second, FetchResult)
    assert not isinstance(result.data, ClientResponse)
    assert len(adapter.entries) == 1
    entry = adapter.entries[0]
    assert isinstance(entry, CacheEntry) and isinstance(entry.result, FetchResult)
    assert not isinstance(entry.result.data, ClientResponse)
    assert dict(entry.result.data) == {
        "symbols": ("AAPL",),
        "source_hash": entry.result.metadata.response_hash,
    }


async def test_parsed_html_endpoint_result_is_cacheable() -> None:
    """A reviewed HTML parser produces the FetchResult; the raw page never does."""
    adapter = RecordingAdapter()
    fake = CountingTransport(
        _resp(
            body=b"<html><body>QUOTE-PAGE</body></html>",
            content_type="text/html",
            url=f"{BASE}/quote.ashx",
        )
    )
    client = _client(fake, cache=adapter, cache_ttl=60.0)
    op = client._endpoint_op("/quote.ashx", query={"t": "AAPL"}, parse=_parsed_quote)
    first = await op()
    second = await op()
    assert fake.calls == 1  # second call was a cache hit on the parsed result
    assert isinstance(first, FetchResult) and isinstance(second, FetchResult)
    assert dict(second.data) == {
        "symbols": ("AAPL",),
        "source_hash": first.metadata.response_hash,
    }
    assert second.metadata.cache_hit is True
    assert adapter.entries and "QUOTE-PAGE" not in str(adapter.entries[0].result.data)


async def test_endpoint_op_controls_disable_and_refresh() -> None:
    """Per-call cache=False and refresh=True are bound on the endpoint seam."""
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    nocache = client._endpoint_op("/api/quote", cache=False, parse=_parsed_quote)
    await nocache()
    await nocache()
    assert fake.calls == 2  # bypassed the store entirely
    assert client.invalidate("/api/quote") is False  # nothing was stored
    await client._endpoint_op("/api/quote", refresh=True, parse=_parsed_quote)()
    assert fake.calls == 3  # refresh fetched a fresh copy...
    hit = await client._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake.calls == 3  # ...and replaced the cached entry
    assert hit.metadata.cache_hit is True


async def test_keys_thread_representation_parser_and_schema_versions() -> None:
    """Distinct representation/parser/schema facets never share endpoint entries."""
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    await client._endpoint_op("/api/quote", representation="default", parse=_parsed_quote)()
    await client._endpoint_op("/api/quote", representation="structured", parse=_parsed_quote)()
    assert fake.calls == 2  # representation differs -> no sharing
    await client._endpoint_op("/api/quote", parser_version="2", parse=_parsed_quote)()
    assert fake.calls == 3  # parser revision differs -> no sharing
    await client._endpoint_op("/api/quote", schema_version=2, parse=_parsed_quote)()
    assert fake.calls == 4  # schema revision differs -> no sharing
    await client._endpoint_op("/api/quote", parse=_parsed_quote)()  # identical facets share
    assert fake.calls == 4


# --- fourth review round regressions --------------------------------------------


async def test_missing_parser_is_rejected_before_any_request() -> None:
    """Unnormalized provider payloads can never be cached: a parser is required."""
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    with pytest.raises(TypeError):
        client._endpoint_op("/api/quote", query={"t": "AAPL"})  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client._cached_fetch("/api/quote", cache=False)  # type: ignore[call-arg]
    assert fake.calls == 0  # nothing was fetched...
    assert client._cache is not None and client._cache.stats()["entries"] == 0  # ...or stored


async def test_pool_route_keys_use_the_resolved_proxy() -> None:
    """Pool entries are keyed by the pinned proxy, so identical calls share one."""
    fake = CountingTransport()
    client = _client(fake, proxies=["http://pool-a:1"], cache_ttl=60.0)
    first = await client._endpoint_op("/api/quote", parse=_parsed_quote)()
    second = await client._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake.calls == 1  # same resolved route -> one underlying call
    assert second.metadata.cache_hit is True
    assert second.data == first.data  # same pinned-route payload
    assert client.invalidate("/api/quote") is True  # the entry stays addressable
    assert client._cache is not None and client._cache.stats()["entries"] == 0


async def test_distinct_pools_sharing_a_cache_never_share_entries() -> None:
    """Different configured pools are different routes even with a shared adapter."""
    shared = ResultCache()
    fake1 = CountingTransport()
    fake2 = CountingTransport()
    a = _client(fake1, proxies=["http://pool-a:1"], cache=shared, cache_ttl=60.0)
    b = _client(fake2, proxies=["http://pool-b:2"], cache=shared, cache_ttl=60.0)
    ra = await a._endpoint_op("/api/quote", parse=_parsed_quote)()
    rb = await b._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake1.calls == 1 and fake2.calls == 1  # no cross-pool sharing
    assert rb.metadata.cache_hit is False
    assert ra.metadata.route_fingerprint != rb.metadata.route_fingerprint
    assert shared.stats()["entries"] == 2
    assert a.invalidate("/api/quote") is True  # each entry addressable by its owner
    assert shared.stats()["entries"] == 1
    assert b.invalidate("/api/quote") is True
    assert shared.stats()["entries"] == 0


async def test_parser_and_schema_facets_reach_result_metadata() -> None:
    """Bound facet facts land in immutable metadata on miss and hit."""
    fake = CountingTransport()
    client = _client(fake, cache_ttl=60.0)
    op = client._endpoint_op(
        "/api/quote", parser_version="7", schema_version=42, parse=_parsed_quote
    )
    miss = await op()
    hit = await op()
    assert fake.calls == 1
    assert miss.metadata.parser_version == "7" and miss.metadata.schema_version == 42
    assert hit.metadata.parser_version == "7" and hit.metadata.schema_version == 42


async def test_proxy_false_routes_direct_through_the_cache() -> None:
    """Cache-enabled proxy=False must reach the transport direct, like cache=False."""
    fake = CapturingTransport()
    client = _client(fake, proxy="http://configured.example:8080", cache_ttl=60.0)
    direct = await client._endpoint_op("/api/quote", proxy=False, parse=_parsed_quote)()
    assert fake.proxies == [None]  # the explicitly requested direct route
    await client._endpoint_op("/api/quote", proxy=False, parse=_parsed_quote)()
    assert fake.calls == 1 and fake.proxies == [None]  # hit keeps the same route
    assert direct.metadata.cache_hit is False
    refresh = await client._endpoint_op(
        "/api/quote", proxy=False, refresh=True, parse=_parsed_quote
    )()
    assert fake.calls == 2 and fake.proxies == [None, None]  # refresh stays direct
    assert refresh.metadata.cache_hit is False
    # A direct-route entry never shares a key with the client-config route.
    default_route = await client._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake.calls == 3 and fake.proxies[-1] == "http://configured.example:8080"
    assert default_route.metadata.cache_hit is False
    await client._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake.calls == 3  # and its own entry is reusable (no 4th call)
    assert client.invalidate("/api/quote", proxy=False) is True


async def test_cache_false_proxy_false_routes_direct() -> None:
    """The uncached control: proxy=False bypasses the configured proxy."""
    fake = CapturingTransport()
    client = _client(fake, proxy="http://configured.example:8080")
    await client._endpoint_op("/api/quote", proxy=False, cache=False, parse=_parsed_quote)()
    assert fake.proxies == [None]


async def test_cached_pool_proxy_false_matches_uncached_direct_route() -> None:
    """Pool client: proxy=False through the cache uses direct, not the pool."""
    fake = CapturingTransport()
    client = _client(fake, proxies=["http://pool-a:1"], cache_ttl=60.0)
    await client._endpoint_op("/api/quote", proxy=False, parse=_parsed_quote)()
    assert fake.proxies == [None]  # explicit False wins over the pool
    await client._endpoint_op("/api/quote", proxy=False, parse=_parsed_quote)()
    assert fake.calls == 1  # and the direct-route entry is reused
    pool_call = await client._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake.calls == 2 and fake.proxies[-1] == "http://pool-a:1"  # pool key distinct
    assert pool_call.metadata.cache_hit is False


async def test_pinned_auth_route_never_leaks_to_direct_route_clients() -> None:
    """Two same-scope clients sharing a cache: the auth pin must gate keys AND transport.

    Client A pins its authenticated route through a per-call proxy; its later
    default calls must be keyed (and transported) on that pinned route, so a
    direct-route client B can never receive A's proxied response as a hit.
    """
    shared: ResultCache = ResultCache()
    fake_a = CapturingTransport()
    fake_b = CapturingTransport()
    client_a = _client(fake_a, auth_cookies={"sid": "aaa"}, cache=shared, cache_ttl=60.0)
    client_b = _client(fake_b, auth_cookies={"sid": "aaa"}, cache=shared, cache_ttl=60.0)
    await client_a._endpoint_op(
        "/api/quote", proxy="http://proxy-a.example:8080", parse=_parsed_quote
    )()
    assert fake_a.proxies == ["http://proxy-a.example:8080"]
    # A's default calls ride the pinned authenticated route (key and transport).
    await client_a._endpoint_op("/api/quote", parse=_parsed_quote)()
    await client_a._endpoint_op("/api/quote", query={"t": "AAPL"}, parse=_parsed_quote)()
    assert fake_a.proxies[-1] == "http://proxy-a.example:8080"
    # B's direct default call must make a real direct transport, never hit
    # A's pinned-route entry through a direct-route key.
    result_b = await client_b._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake_b.calls == 1 and fake_b.proxies == [None]
    assert result_b.metadata.cache_hit is False


async def test_auth_pin_collapses_default_calls_to_one_route_entry() -> None:
    """After the pin, default calls and invalidation address the pinned route."""
    shared: ResultCache = ResultCache()
    fake = CapturingTransport()
    client = _client(fake, auth_cookies={"sid": "aaa"}, cache=shared, cache_ttl=60.0)
    await client._endpoint_op(
        "/api/quote", proxy="http://proxy-a.example:8080", parse=_parsed_quote
    )()
    await client._endpoint_op("/api/quote", parse=_parsed_quote)()  # rides the pin: same entry
    assert fake.calls == 1
    assert shared.stats()["entries"] == 1  # one route, one entry — never a phantom direct key
    assert client.invalidate("/api/quote") is True  # default facets resolve to the pinned route
    assert client.invalidate("/api/quote") is False  # nothing left at that route
    await client._endpoint_op("/api/quote", parse=_parsed_quote)()
    assert fake.calls == 2  # the pinned-route entry was really dropped


async def test_cancelled_leader_orphan_failure_never_reaches_the_loop_handler() -> None:
    """A cancelled creator + failing flight must not warn the loop, but joiners
    still receive the error."""
    contexts: list[Any] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: Any) -> None:
        contexts.append(context)

    loop.set_exception_handler(handler)
    try:
        transport = SlowTransport(
            BackendError("boom"),  # the flight fails only after the leader's cancel
        )
        client = _client(transport, cache_ttl=60.0)
        leader = asyncio.create_task(client._endpoint_op("/api/quote", parse=_parsed_quote)())
        joiner = asyncio.create_task(client._endpoint_op("/api/quote", parse=_parsed_quote)())
        await asyncio.sleep(0.01)
        leader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leader
        with pytest.raises(FinvizTransportError):
            await joiner  # an active joiner still sees the real failure
        await asyncio.sleep(0.05)  # let the done callback run
        del leader, joiner
        gc.collect()  # force any unretrieved-exception reporting right now
    finally:
        loop.set_exception_handler(previous)
    assert contexts == []  # no "exception was never retrieved" loop warning
