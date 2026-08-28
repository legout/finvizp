"""Parsed-result cache: caller-cache seam, bounded LRU eviction, TTL, safe stats.

Caches immutable classified ``ClientResponse`` values only — never raw
authenticated bodies. Expiry decisions use monotonic time; wall-clock facts
stay on the cached envelope.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from types import MappingProxyType
from typing import Any

from finvizp.client import ClientResponse

__all__ = ["CacheEntry", "ResultCache"]

# Bumped when the meaning of a cached envelope changes; part of the key.
_PARSER_VERSION = "1"
_SCHEMA_VERSION = "1"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One cached classified response with expiry and byte accounting."""

    response: ClientResponse
    expires_at: float
    stored_at: float
    approx_bytes: int


class ResultCache:
    """Approximate byte-bounded LRU cache of immutable classified responses.

    One client runs in one event loop and every mutating method is
    synchronous, so no lock is needed.
    """

    def __init__(
        self,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        max_entries: int = 256,
        default_ttl: float | None = None,
    ) -> None:
        self._max_bytes = max(0, int(max_bytes))
        self._max_entries = max(0, int(max_entries))
        self._default_ttl = None if default_ttl is None else max(0.0, float(default_ttl))
        self._entries: dict[str, CacheEntry] = {}
        self._approx_bytes = 0
        self._hits = 0
        self._misses = 0
        self._stores = 0
        self._evictions = 0

    def make_key(
        self,
        *,
        endpoint: str,
        query: Mapping[str, Any],
        access_tier: str,
        route_fingerprint: str,
        browser_profile: str,
        representation: str = "default",
    ) -> str:
        """Deterministic key isolating endpoint/query, auth scope, route, and identity."""
        parts = (
            _PARSER_VERSION,
            _SCHEMA_VERSION,
            endpoint,
            _canonical(dict(query)),
            str(access_tier),
            route_fingerprint,
            browser_profile,
            representation,
        )
        digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
        return f"finviz-cache-v1:{digest}"

    def get(self, key: str) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        if monotonic() >= entry.expires_at:
            self._drop(key)
            self._evictions += 1
            self._misses += 1
            return None
        del self._entries[key]  # re-insert to refresh LRU recency
        self._entries[key] = entry
        self._hits += 1
        return entry

    def peek(self, key: str) -> CacheEntry | None:
        """Side-effect-free read: never touches counters, order, or expiry.

        Stale-if-error looks past expiry, so expired entries must stay
        retrievable here even though ordinary ``get`` drops them.
        """
        return self._entries.get(key)

    def set(self, key: str, entry: CacheEntry) -> None:
        self._drop(key)
        self._entries[key] = entry
        self._approx_bytes += entry.approx_bytes
        self._stores += 1
        self._evict()

    def delete(self, key: str) -> bool:
        return self._drop(key)

    def clear(self) -> int:
        """Drop every entry; return how many were held."""
        count = len(self._entries)
        self._entries.clear()
        self._approx_bytes = 0
        return count

    def stats(self) -> Mapping[str, int]:
        """Safe counters only: no endpoints, queries, routes, or payloads."""
        return MappingProxyType(
            {
                "entries": len(self._entries),
                "approx_bytes": self._approx_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "stores": self._stores,
                "evictions": self._evictions,
            }
        )

    def _drop(self, key: str) -> bool:
        entry = self._entries.pop(key, None)
        if entry is None:
            return False
        self._approx_bytes -= entry.approx_bytes
        return True

    def _evict(self) -> None:
        while self._entries:
            over_entries = self._max_entries and len(self._entries) > self._max_entries
            over_bytes = self._max_bytes and self._approx_bytes > self._max_bytes
            if not over_entries and not over_bytes:
                return
            oldest = next(iter(self._entries))  # dict order == LRU order
            self._drop(oldest)
            self._evictions += 1
