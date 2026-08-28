"""Hermetic tests for the parsed-result cache: seam, key isolation, TTL, eviction."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from finvizp.cache import CacheEntry, ResultCache


def _entry(
    data: object = "payload",
    *,
    expires_at: float | None = None,
    stored_at: float = 0.0,
    approx_bytes: int = 1024,
) -> CacheEntry:
    from datetime import UTC, datetime

    from finvizp.client import ClientResponse
    from finvizp.results import AccessTier

    response = ClientResponse(
        endpoint="/quote.ashx",
        url="https://finviz.com/quote.ashx",
        query=MappingProxyType({"t": "AAPL"}),
        status_code=200,
        headers={"content-type": "text/html"},
        data=data,
        content_kind="html",
        response_hash="0" * 64,
        fetched_at=datetime.now(UTC),
        access_tier=AccessTier.PUBLIC,
        browser_profile="chrome",
        route_fingerprint="finviz-route-v1:direct",
        attempts=1,
    )
    return CacheEntry(
        response=response,
        expires_at=expires_at if expires_at is not None else float("inf"),
        stored_at=stored_at,
        approx_bytes=approx_bytes,
    )


# --- caller-cache seam --------------------------------------------------------


def _key(cache: ResultCache, **overrides: object) -> str:
    facets: dict[str, object] = {
        "endpoint": "/quote.ashx",
        "query": {"t": "AAPL"},
        "access_tier": "PUBLIC",
        "route_fingerprint": "finviz-route-v1:direct",
        "browser_profile": "chrome",
    }
    facets.update(overrides)
    return cache.make_key(**facets)  # type: ignore[arg-type]


def test_seam_get_set_delete_clear_stats() -> None:
    cache = ResultCache()
    key = _key(cache)
    assert cache.get(key) is None  # miss
    entry = _entry()
    cache.set(key, entry)
    assert cache.get(key) is entry
    assert cache.delete(key) is True
    assert cache.delete(key) is False
    assert cache.get(key) is None
    cache.set(key, entry)
    assert cache.clear() == 1
    assert cache.clear() == 0
    assert cache.get(key) is None


def test_stats_are_safe_counts_only() -> None:
    cache = ResultCache()
    cache.set(_key(cache), _entry())
    cache.get(_key(cache))
    cache.get("missing")
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["stores"] == 1
    # Safe statistics: counts and sizes only, never endpoints, queries, or data.
    rendered = repr(stats)
    assert "quote" not in rendered
    assert "AAPL" not in rendered
    assert "payload" not in rendered


# --- key isolation ------------------------------------------------------------


def test_key_changes_with_every_isolation_facet() -> None:
    cache = ResultCache()
    base = _key(cache)
    assert base == _key(cache)  # deterministic
    assert _key(cache, endpoint="/api/suggestions") != base
    assert _key(cache, query={"t": "MSFT"}) != base
    assert _key(cache, access_tier="AUTHENTICATED") != base
    assert _key(cache, route_fingerprint="finviz-route-v1:pool-2") != base
    assert _key(cache, browser_profile="chrome131") != base
    assert _key(cache, representation="structured") != base


def test_key_is_query_order_insensitive() -> None:
    cache = ResultCache()
    assert _key(cache, query={"t": "AAPL", "p": "d"}) == _key(cache, query={"p": "d", "t": "AAPL"})


# --- TTL ----------------------------------------------------------------------


def test_expired_entry_is_a_miss_and_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    import finvizp.cache as cache_module

    now = 1000.0
    monkeypatch.setattr(cache_module, "monotonic", lambda: now)
    cache = ResultCache()
    key = _key(cache)
    cache.set(key, _entry(expires_at=now + 10))
    assert cache.get(key) is not None
    now += 11
    assert cache.get(key) is None  # expired -> miss
    assert cache.stats()["entries"] == 0  # dropped on read
    assert cache.stats()["misses"] == 1  # only the expired read is a miss


# --- eviction -----------------------------------------------------------------


def test_byte_budget_evicts_least_recently_used() -> None:
    cache = ResultCache(max_bytes=2500)
    keys = []
    for i in range(3):
        key = _key(cache, query={"t": f"S{i}"})
        keys.append(key)
        cache.set(key, _entry(data="x" * 1000, approx_bytes=1024))
    assert cache.get(keys[0]) is None  # LRU evicted
    assert cache.get(keys[2]) is not None  # newest survives
    assert cache.stats()["evictions"] >= 1


def test_entry_cap_evicts_oldest() -> None:
    cache = ResultCache(max_entries=2)
    keys = [_key(cache, query={"t": f"S{i}"}) for i in range(3)]
    for key in keys:
        cache.set(key, _entry())
    assert cache.get(keys[0]) is None
    assert cache.get(keys[1]) is not None
    assert cache.get(keys[2]) is not None
    assert cache.stats()["entries"] == 2


def test_peek_returns_entry_without_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import finvizp.cache as cache_module

    now = 1000.0
    monkeypatch.setattr(cache_module, "monotonic", lambda: now)
    cache = ResultCache()
    key = _key(cache)
    cache.set(key, _entry(expires_at=now + 10))
    assert cache.peek(key) is not None
    assert cache.stats()["hits"] == 0 and cache.stats()["misses"] == 0  # no counters
    now += 11  # entry is now expired
    assert cache.peek(key) is not None  # stays visible: stale-if-error fallback
    assert cache.stats()["entries"] == 1  # peek never drops
    assert cache.get(key) is None  # ordinary read still drops expired


def test_access_refreshes_lru_order() -> None:
    cache = ResultCache(max_entries=2)
    keys = [_key(cache, query={"t": f"S{i}"}) for i in range(3)]
    for key in keys[:2]:
        cache.set(key, _entry())
    assert cache.get(keys[0]) is not None  # touch keys[0]; keys[1] is now LRU
    cache.set(keys[2], _entry())
    assert cache.get(keys[0]) is not None  # survived: recently used
    assert cache.get(keys[1]) is None  # evicted: least recently used
