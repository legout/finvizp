"""Classified Finviz transport client.

Owns one fastreq ``Backend`` (transport seam), explicit same-origin routes,
proxy precedence, caller-supplied auth isolation, SHA-256 hashing, bounded
retries, typed response classification, and the parsed-result cache /
single-flight layer. ``cache.py`` owns the bounded LRU store of parsed
immutable results; this module owns the fetch integration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from time import monotonic
from types import MappingProxyType
from typing import Any
from urllib.parse import urljoin, urlsplit

from fastreq.backends.base import Backend, NormalizedResponse, RequestConfig
from fastreq.backends.curl_cffi import CurlCffiBackend
from fastreq.exceptions import BackendError, RetryableResponse
from fastreq.utils.proxies import ProxyPool, ProxyPoolConfig
from fastreq.utils.rate_limiter import AsyncRateLimiter, RateLimitConfig

# cache.py stores parsed FetchResult values and no longer imports this
# module, so the import is ordinary and cycle-free.
from finvizp.cache import CacheEntry, ResultCache
from finvizp.errors import (
    _SENSITIVE_KEY,
    REDACTED,
    FinvizBlockedError,
    FinvizEntitlementError,
    FinvizError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizQueryError,
    FinvizRateLimitError,
    FinvizTransportError,
    _redact_text,
    redact_value,
)
from finvizp.models import Artifact
from finvizp.results import _ARROW_TABLE_TYPES, AccessTier, FetchResult

__all__ = ["ClientEvent", "ClientResponse", "FinvizClient", "classify_response"]

BASE_URL = "https://finviz.com"
_DEFAULT_BROWSER_PROFILE = "chrome"

# Transient statuses classified as retryable; Retry-After honored.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_UNPINNED = object()  # sentinel: no pool proxy acquired yet
_ROUTE_PREFIX = "finviz-route-v1"
_ELITE_PATH = re.compile(r"(?:^|/)(?:login\.aspx|elite\.aspx)$")
# Bounded client-side redirect loop; curl auto-follow is disabled so every
# hop is origin-checked before credentials are ever re-sent.
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_DEFAULT_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_FALLBACK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Typed finvizp failures are verdicts, not transient transport faults.
_NEVER_RETRY: tuple[type[FinvizError], ...] = (
    FinvizQueryError,
    FinvizBlockedError,
    FinvizEntitlementError,
    FinvizNotFoundError,
    FinvizParseError,
)

# Transient failures stale-if-error may paper over (opt-in only); typed
# verdicts — query, parse, entitlement, challenge, not-found — are excluded.
FINVIZ_TRANSPORT_ERRORS: tuple[type[FinvizError], ...] = (FinvizTransportError,)


@dataclass(frozen=True, slots=True)
class _CacheFacets:
    """Route + key facets bound to one reviewed endpoint operation."""

    path: str
    query: dict[str, Any]
    proxy: str | bool | None
    representation: str
    parser_version: str
    schema_version: int
    # Endpoint-scoped transport control (default preserves client behavior);
    # it alters which response a route yields, so ops that change it (the
    # one-request manifest) are also distinguished by their representation.
    follow_redirects: bool = True


def _facets_parse(
    parse: Callable[[ClientResponse], FetchResult[Any]],
    parser_version: str,
    schema_version: int,
) -> Callable[[ClientResponse], FetchResult[Any]]:
    """Bind parser/schema facet facts onto every parsed result.

    Foundation metadata carries the parser/schema revisions that produced a
    result; stamping here covers misses, refreshes, cache stores, hits, and
    joiners through one choke point.
    """

    def parse_with_facets(response: ClientResponse) -> FetchResult[Any]:
        result = parse(response)
        return replace(
            result,
            metadata=replace(
                result.metadata,
                parser_version=parser_version,
                schema_version=schema_version,
            ),
        )

    return parse_with_facets


def _parse_retry_after(value: str | float | None) -> float | None:
    """Parse numeric, integer-seconds, or HTTP-date Retry-After values."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value.strip())
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0.0, (dt - datetime.now(UTC)).total_seconds())


def _normalize_kind(content_type: str) -> str | None:
    """Map a Content-Type header value to one classified content kind."""
    base = content_type.split(";")[0].strip().lower()
    if base in ("", "application/octet-stream") or base.startswith("image/"):
        return None if not base else "artifact"
    if base == "application/json" or base.endswith("+json"):
        return "json"
    if "html" in base:
        return "html"
    if "xml" in base:
        return "xml"
    if base.startswith("text/"):
        return "html"
    return None


def _proxy_seed(proxy: str | None) -> str:
    digest = hashlib.sha256((proxy or "direct").encode()).hexdigest()[:12]
    return f"{digest}-direct" if proxy is None else digest


def _payload_size(data: Any) -> int:
    """Approximate cached size for any payload shape, in bytes.

    ponytail: JSON payloads are sized via serialized bytes (exact but O(n) per
    store); a cheap recursive estimator wins if stores show up in profiles.
    Arrow tables are sized by their buffer accounting (nbytes); other typed
    objects fall back to str(), so any future payload type must be added here
    if its str() is not proportional to memory.
    """
    if isinstance(data, str):
        return len(data.encode())
    if isinstance(data, bytes):
        return len(data)
    if isinstance(data, _ARROW_TABLE_TYPES):
        return int(data.nbytes)
    if isinstance(data, Mapping):
        # Nested containers (e.g. QuoteBundle.snapshot_tables): tables inside
        # mappings must be charged by nbytes, not their str() rendering.
        return sum(_payload_size(k) + _payload_size(v) for k, v in data.items())
    if isinstance(data, (list, tuple, set, frozenset)):
        return sum(_payload_size(item) for item in data)
    if is_dataclass(data) and not isinstance(data, type):
        # Compound bundles: sum typed fields (tables dominate payload size).
        return sum(_payload_size(getattr(data, f.name)) for f in fields(data))
    return len(json.dumps(data, default=str, separators=(",", ":")).encode())


def _is_valid_proxy_url(value: str) -> bool:
    """Structural check for client-accepted proxy forms.

    Supersedes fastreq's prefix-only check: scheme URLs must carry a real
    authority (host, sane port); bare host:port[/user:pass] stay supported.
    Bare forms are checked textually because ``urlsplit('host:8080')`` parses
    the host as the scheme. Never returns the input — errors stay route-free.
    """
    if not value or not isinstance(value, str):
        return False
    # Bare host:port[/user:pass] (no scheme): checked textually because
    # urlsplit('host:8080') parses the host as the scheme.
    if "://" not in value:
        pieces = value.split(":")
        if len(pieces) in (2, 4):
            return bool(pieces[0]) and pieces[1].isdigit() and 0 < int(pieces[1]) <= 65535
        return False
    parts = urlsplit(value)
    if parts.scheme in ("http", "https"):
        if not parts.hostname:
            return False
        try:
            return parts.port is None or 0 < parts.port <= 65535
        except ValueError:
            return False
    return False  # only http/https scheme URLs are client-supported


# Header-specific sanitization: redact credential-bearing headers by label
# (Set-Cookie*, Cookie, Authorization, Proxy-Authorization) while preserving
# safe protocol metadata such as content-type/content-length.
_SAFE_HEADER = re.compile(r"^(?:set-)?cookie\d*$|^(?:proxy-)?authorization$", re.I)


def _safe_headers(headers: Mapping[str, str]) -> MappingProxyType[str, str]:
    """Header sanitizer: credential-bearing labels lose their value entirely;
    every retained value is scrubbed for embedded secrets (query tokens,
    proxy URLs) while protocol metadata such as content-type survives."""
    safe: dict[str, str] = {}
    for key, value in headers.items():
        if _SAFE_HEADER.match(key) or _SENSITIVE_KEY.search(key.replace("-", "_")):
            safe[key] = REDACTED
        else:
            safe[key] = _redact_text(value)
    return MappingProxyType(safe)


def _is_elite_location(final_url: str) -> bool:
    try:
        parts = urlsplit(final_url)
    except ValueError:
        return False
    return bool(
        _ELITE_PATH.search(parts.path)
        or parts.hostname == "elite.finviz.com"
        or (parts.hostname or "").endswith(".elite.finviz.com")
    )


def _is_finviz_location(final_url: str) -> bool:
    """Return whether a followed redirect remains at the canonical origin."""
    try:
        parts = urlsplit(final_url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname == "finviz.com"
        and port in (None, 443)
        and parts.username is None
        and parts.password is None
    )


def _is_timeout_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text


_NOT_FOUND_TITLE = re.compile(
    r"<title>[^<]*(?:page (?:was )?not found|404 error)[^<]*</title>", re.I
)


@dataclass(frozen=True, slots=True)
class ClientResponse:
    """Immutable, classified envelope for one route fetch.

    ``data`` is the typed payload: parsed JSON for ``json``, decoded text for
    ``html``/``xml``, an ``Artifact`` descriptor (hash + length, never bytes)
    for ``artifact``. Raw bytes are hashed then discarded; the envelope never
    retains the body.
    """

    endpoint: str
    url: str
    query: Mapping[str, Any]
    status_code: int
    headers: Mapping[str, str]
    data: Any
    content_kind: str
    response_hash: str
    fetched_at: datetime
    access_tier: AccessTier
    browser_profile: str
    route_fingerprint: str
    attempts: int
    served_at: datetime | None = None
    cache_hit: bool = False
    stale: bool = False
    cache_age: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, MappingProxyType):
            # Request provenance is public metadata: sensitive params (tokens,
            # secrets, credentials) are redacted; ordinary keys pass through.
            object.__setattr__(self, "query", MappingProxyType(redact_value(dict(self.query))))
        object.__setattr__(self, "headers", _safe_headers(self.headers))


@dataclass(frozen=True, slots=True)
class ClientEvent:
    """Explicit opt-in diagnostic event; scrubbed facts only, never URLs."""

    endpoint: str
    ok: bool
    status_code: int | None = None
    content_kind: str | None = None
    attempts: int = 1
    route_fingerprint: str | None = None


def classify_response(
    endpoint: str,
    *,
    url: str,
    query: Mapping[str, Any],
    response: NormalizedResponse,
    response_hash: str,
    fetched_at: datetime,
    access_tier: AccessTier,
    browser_profile: str,
    route_fingerprint: str,
    attempts: int,
) -> ClientResponse:
    """Classify one 200 ``NormalizedResponse`` into a typed envelope."""
    content_type = response.headers.get("content-type", "")
    kind = _normalize_kind(content_type)
    if kind is None:
        msg = f"unclassified content type for {endpoint!r}"
        raise FinvizParseError(msg, context={"content_type": content_type or None})
    if kind == "json":
        data: Any = response.json_data
        if data is None:
            # The real backend pre-parses only exact ``application/json``;
            # ``*+json`` suffix media types arrive unparsed, so decode here.
            try:
                data = json.loads(response.text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                msg = f"malformed JSON body for {endpoint!r}"
                raise FinvizParseError(msg) from None
    elif kind == "artifact":
        # Image/chart bodies are hashed for identity; the envelope keeps only
        # a descriptor, so raw provider bytes are never retained by callers.
        data: Any = Artifact(
            source_url=url,
            kind="image",
            media_type=content_type.split(";")[0].strip().lower(),
            fetched_at=fetched_at,
            content_hash=hashlib.sha256(response.content).hexdigest(),
            content_length=len(response.content),
        )
    else:
        data = response.text
    return ClientResponse(
        endpoint=endpoint,
        url=url,
        query=query,
        status_code=response.status_code,
        headers=response.headers,
        data=data,
        content_kind=kind,
        response_hash=response_hash,
        fetched_at=fetched_at,
        access_tier=access_tier,
        browser_profile=browser_profile,
        route_fingerprint=route_fingerprint,
        attempts=attempts,
    )


class FinvizClient:
    """One fixed-identity, route-explicit Finviz transport.

    Args:
        transport: pre-built fastreq ``Backend`` (hermetic tests); it is
            driven by this client but never replaced by it.
        base_url: must be the canonical ``https://finviz.com`` origin; caller
            cookies are never sent anywhere else. Read-only after construction.
        proxy: explicit proxy URL; ``False``/``""`` forces direct.
        proxies: explicit pool list; ``False``/``[]`` disables all discovery.
        auth_cookies: caller-supplied session state, sent per request only;
            never read from the environment, never persisted.
        browser_profile: fixed browser/TLS identity (``"none"`` disables
            impersonation); no randomization. Read-only after construction.
        rate_limit: requests per second, or ``None`` for no limit.
        concurrency: maximum in-flight requests.
        timeout: per-request timeout in seconds.
        retry_attempts: bounded retries for transient transport/5xx/429 only.
        retry_backoff: base seconds for exponential backoff (capped at 60s).
        on_event: opt-in diagnostic callback receiving ``ClientEvent`` values.
        cache_ttl: seconds a parsed result stays fresh; ``None`` (the
            default) disables caching entirely.
        cache: bounded LRU store of parsed immutable ``FetchResult`` values;
            one per client is created when caching is first used. Callers may
            inject any object with ``get``/``set``/``delete``/``clear``/
            ``stats``/``make_key``.
        cache_max_bytes: approximate byte budget for the built-in cache.
        cache_max_entries: entry cap for the built-in cache.
        stale_if_error: opt-in; serve an expired cached response when an
            eligible transport failure occurs. Never masks typed verdicts.
    """

    base_url: str
    browser_profile: str

    _FROZEN_ATTRS = frozenset({"base_url", "browser_profile"})

    def __setattr__(self, name: str, value: Any) -> None:
        # The canonical origin and TLS identity are fixed for the client's
        # lifetime; a mutable base_url would let caller cookies be retargeted.
        if name in self._FROZEN_ATTRS and name in vars(self):
            msg = f"{name} is fixed for the lifetime of the client"
            raise AttributeError(msg)
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        transport: Backend | None = None,
        base_url: str = BASE_URL,
        proxy: str | bool | None = None,
        proxies: list[str] | bool | None = None,
        auth_cookies: Mapping[str, str] | None = None,
        browser_profile: str = _DEFAULT_BROWSER_PROFILE,
        rate_limit: float | None = None,
        concurrency: int = 6,
        timeout: float | None = None,
        retry_attempts: int = 2,
        retry_backoff: float = 1.0,
        on_event: Callable[[ClientEvent], Any] | None = None,
        cache_ttl: float | None = None,
        cache: ResultCache | bool | None = None,
        cache_max_bytes: int = 8 * 1024 * 1024,
        cache_max_entries: int = 256,
        stale_if_error: bool = False,
    ) -> None:
        if (normalized := base_url.rstrip("/")) != BASE_URL:
            msg = f"base_url must be {BASE_URL}, got {base_url!r}"
            raise FinvizQueryError(msg)
        # Canonical origin only: caller cookies must never be retargetable to
        # another host, so the origin is fixed at construction and read-only.
        object.__setattr__(self, "base_url", normalized)
        if browser_profile == "random":
            msg = "browser_profile='random' violates the fixed-identity contract"
            raise FinvizQueryError(msg)
        object.__setattr__(self, "browser_profile", browser_profile)
        self._auth_cookies: Mapping[str, str] = (
            MappingProxyType(dict(auth_cookies)) if auth_cookies else MappingProxyType({})
        )
        self._on_event = on_event
        self._concurrency = max(1, int(concurrency))
        self._rate_limit = rate_limit
        self._timeout = timeout
        self._retry_attempts = max(0, int(retry_attempts))
        self._retry_backoff = max(0.0, float(retry_backoff))

        # Proxy precedence: explicit > FINVIZP_PROXY > fastreq env pool > direct.
        # Type and URL are validated before any pool is constructed, so invalid
        # input fails fast here instead of surfacing fastreq log output.
        self._force_direct = False
        self._explicit_proxy: str | None = None
        if proxy is False or proxy == "":
            self._force_direct = True
        elif proxy is None or isinstance(proxy, str):
            if isinstance(proxy, str) and not _is_valid_proxy_url(proxy):
                msg = "invalid proxy URL"
                raise FinvizQueryError(msg)
            self._explicit_proxy = proxy
        else:
            msg = "proxy must be a URL, False, or None and proxies a list of URLs/False/None"
            raise FinvizQueryError(msg)
        if proxies is not None and not (
            proxies is False
            or (isinstance(proxies, list) and all(isinstance(p, str) for p in proxies))
        ):
            msg = "proxies must be a list of URLs, False, or None"
            raise FinvizQueryError(msg)
        self._pool: ProxyPool | None = None
        if proxies is False or (isinstance(proxies, list) and not proxies):
            self._force_direct = True
        elif isinstance(proxies, list):
            invalid = [p for p in proxies if not _is_valid_proxy_url(p)]
            if invalid:
                msg = "invalid proxy URL in proxies list"
                raise FinvizQueryError(msg)
            self._pool = ProxyPool(proxies=list(proxies), config=ProxyPoolConfig())
        if not self._force_direct and self._explicit_proxy is None and self._pool is None:
            import os

            if env_proxy := os.getenv("FINVIZP_PROXY", ""):
                if not _is_valid_proxy_url(env_proxy):
                    msg = "invalid proxy URL in FINVIZP_PROXY"
                    raise FinvizQueryError(msg)
                self._explicit_proxy = env_proxy
            else:
                env_entries = [
                    p.strip() for p in os.getenv("FASTREQ_PROXIES", "").split(",") if p.strip()
                ]
                env_entries = [p for p in env_entries if _is_valid_proxy_url(p)]
                if env_entries:
                    self._pool = ProxyPool(proxies=env_entries, config=ProxyPoolConfig())
        self._pinned_proxy: Any = _UNPINNED
        self._auth_route: Any = _UNPINNED

        self._backend: Backend = (
            transport
            if transport is not None
            else CurlCffiBackend(impersonate=None if browser_profile == "none" else browser_profile)
        )
        self._limiter = (
            AsyncRateLimiter(RateLimitConfig(requests_per_second=rate_limit, burst=5))
            if rate_limit is not None
            else None
        )
        self._semaphore = asyncio.Semaphore(max(1, int(concurrency)))
        self._lifecycle_lock = asyncio.Lock()
        self._route_lock = asyncio.Lock()
        self._entered = False
        self._cache_ttl = None if cache_ttl is None else max(0.0, float(cache_ttl))

        # ``cache=False`` is a hard kill switch; ``cache=None``/``True`` use
        # the built-in bounded LRU; a store instance is injected as-is.
        effective_cache: ResultCache | None
        if cache is False:
            effective_cache = None
        elif cache is True or cache is None:
            effective_cache = ResultCache(max_bytes=cache_max_bytes, max_entries=cache_max_entries)
        else:
            effective_cache = cache
        self._cache: ResultCache | None = effective_cache
        self._stale_if_error = bool(stale_if_error)
        self._inflight: dict[str, asyncio.Task[FetchResult[Any] | FinvizError]] = {}
        # Opaque, non-secret per-auth-state scope: cookie VALUES are hashed
        # with the browser identity into a fingerprint, so distinct sessions
        # never share cache entries while nothing secret enters any key.
        scope = "\x1f".join([f"{k}={self._auth_cookies[k]}" for k in sorted(self._auth_cookies)])
        digest = hashlib.sha256(f"{browser_profile}\x1f{scope}".encode()).hexdigest()
        self._auth_scope = "public" if not self._auth_cookies else f"auth:{digest}"

    # --- lifecycle ---------------------------------------------------------

    async def _ensure_entered(self) -> None:
        if self._entered:
            return
        async with self._lifecycle_lock:
            if not self._entered:
                await self._backend.__aenter__()
                self._entered = True

    async def __aenter__(self) -> FinvizClient:
        await self._ensure_entered()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the transport session; safe to call repeatedly."""
        async with self._lifecycle_lock:
            if self._entered:
                self._entered = False
                await self._backend.__aexit__(None, None, None)
            else:
                await self._backend.close()

    # --- request path --------------------------------------------------------

    def _route_fingerprint(self, proxy: str | object | None = _UNPINNED) -> str:
        if proxy is _UNPINNED:
            # The authenticated route pin wins over every configured default:
            # once auth state exists it is bound to one exit route, so key
            # identities must name that route, never the pre-pin default.
            if self._auth_cookies and self._auth_route is not _UNPINNED:
                proxy = self._auth_route
            else:
                proxy = self._explicit_proxy
                if proxy is None and self._pinned_proxy is not _UNPINNED:
                    proxy = self._pinned_proxy
                elif proxy is None and self._pool is not None:
                    proxy = "pool"
        return f"{_ROUTE_PREFIX}:{_proxy_seed(proxy if isinstance(proxy, str) else None)}"

    @staticmethod
    def _normalize_per_call_proxy(proxy: str | bool | None) -> str | object | None:
        if proxy is None:
            return _UNPINNED
        if proxy is False or proxy == "":
            return None
        if isinstance(proxy, str):
            if not _is_valid_proxy_url(proxy):
                msg = "invalid proxy URL"
                raise FinvizQueryError(msg)
            return proxy
        msg = "proxy must be a URL, False, or None"
        raise FinvizQueryError(msg)

    async def _acquire_proxy(self, override: str | object | None = _UNPINNED) -> str | None:
        """Select the route's proxy once; the result is pinned for this client.

        Authenticated state (cookies) is bound to one exit route: once a proxy
        is acquired it is reused verbatim for every later request, so a pool
        can never rotate the identity mid-session or after a 429. Failures of
        the selection itself surface as transport errors. The cache path calls
        this same method, so keys, transport, and invalidation all resolve one
        auth-aware route.
        """
        async with self._route_lock:
            if override is not _UNPINNED:
                selected = override
            elif self._force_direct:
                selected = None
            elif self._explicit_proxy is not None:
                selected = self._explicit_proxy
            else:
                if self._pinned_proxy is _UNPINNED:
                    self._pinned_proxy = (
                        await self._pool.acquire() if self._pool is not None else None
                    )
                selected = self._pinned_proxy

            if self._auth_cookies:
                if self._auth_route is _UNPINNED:
                    self._auth_route = selected
                elif override is not _UNPINNED and selected != self._auth_route:
                    raise FinvizQueryError("authenticated client route is already pinned")
                else:
                    selected = self._auth_route
            if selected is not None and not isinstance(selected, str):
                msg = "invalid proxy URL"
                raise FinvizQueryError(msg)  # e.g. a per-call int that typed validation missed
            return selected

    def _emit(self, event: ClientEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)

    async def _cached_fetch(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        proxy: str | bool | None = None,
        cache: bool = True,
        refresh: bool = False,
        representation: str = "default",
        parser_version: str = "1",
        schema_version: int = 1,
        follow_redirects: bool = True,
        parse: Callable[[ClientResponse], FetchResult[Any]],
    ) -> FetchResult[Any]:
        """Cache + single-flight wrapper around ``_fetch``.

        Internal seam for reviewed endpoint operations; there is no public
        arbitrary-request method. Only enabled when the client was constructed
        with ``cache_ttl``. ``cache=False`` bypasses the cache entirely;
        ``refresh=True`` fetches a fresh copy and replaces any cached entry.
        Identical concurrent misses share one underlying request.

        ``follow_redirects=False`` enforces a one-request transport contract:
        a redirect response surfaces as transport drift instead of being
        followed. ``parse`` is required: only reviewed endpoint parsers
        produce the normalized immutable ``FetchResult`` values that may ever
        be stored. Parser/schema facet facts are stamped into every result's
        metadata.
        """
        effective_query = dict(params or {})
        store = self._cache
        # Facets ride with every parse so metadata always carries them, while
        # the route identity for keys/transport is resolved exactly once.
        parse = _facets_parse(parse, parser_version, schema_version)
        if not cache or self._cache_ttl is None or store is None:
            return parse(
                await self._fetch(
                    path, params=effective_query, proxy=proxy, follow_redirects=follow_redirects
                )
            )
        # The auth-aware resolver is shared with the transport, so keys and
        # requests can never disagree about the route (review round 6).
        route = await self._acquire_proxy(self._normalize_per_call_proxy(proxy))
        facets = _CacheFacets(
            path=path,
            query=effective_query,
            # Per-call False/URL overrides ride raw: they are self-describing
            # route inputs, so both the key and the transport see the caller's
            # explicit route (False must never degrade to client config).
            # None carries no per-call intent, so the resolved route
            # (explicit/pinned/force-direct) stands in for both.
            proxy=proxy if proxy is not None else route,
            representation=representation,
            parser_version=parser_version,
            schema_version=schema_version,
            follow_redirects=follow_redirects,
        )
        key = self._cache_key(facets)
        if refresh:
            result = parse(
                await self._fetch(
                    facets.path,
                    params=facets.query,
                    proxy=facets.proxy,
                    follow_redirects=facets.follow_redirects,
                )
            )
            self._store(facets, key, result)
            return result
        entry = store.get(key)
        if entry is not None and monotonic() < entry.expires_at:
            return self._serve(entry, cache_hit=True, stale=False)
        return await self._single_flight(facets, key, parse)

    async def _single_flight(
        self, facets: _CacheFacets, key: str, parse: Callable[[ClientResponse], FetchResult[Any]]
    ) -> FetchResult[Any]:
        """Coalesce identical concurrent misses onto one underlying request."""
        existing = self._inflight.get(key)
        if existing is not None:
            # Losers re-raise the winner's typed failure or report the entry
            # as a cache hit on the leader's result.
            outcome = await asyncio.shield(existing)
            if isinstance(outcome, FinvizError):
                raise outcome
            return self._serve_joiner(outcome)
        task: asyncio.Task[FetchResult[Any] | FinvizError] = asyncio.ensure_future(
            self._miss(facets, key, parse)
        )

        # The mapping is released by the shared task's own done callback, not
        # by the leader's await: if the creator is cancelled, later identical
        # callers must still be able to join the already-running flight. The
        # callback also retrieves any residual outcome, so a completed flight
        # never reports "exception was never retrieved" through the loop.
        def _release(f: asyncio.Task[FetchResult[Any] | FinvizError]) -> None:
            if self._inflight.get(key) is task:
                del self._inflight[key]
            if not f.cancelled():
                f.exception()

        task.add_done_callback(_release)
        self._inflight[key] = task
        outcome = await asyncio.shield(task)
        if isinstance(outcome, FinvizError):
            raise outcome
        return outcome

    async def _miss(
        self,
        facets: _CacheFacets,
        key: str,
        parse: Callable[[ClientResponse], FetchResult[Any]],
    ) -> FetchResult[Any] | FinvizError:
        """One cache miss: fetch, parse, store on success.

        Typed failures are returned as values, not raised: the shared
        single-flight task then always completes successfully, so an orphaned
        flight (every awaiter cancelled) can never reach the loop's exception
        handler, while each awaiter still re-raises the real typed failure.
        """
        cache = self._cache
        assert cache is not None  # only reachable when caching is enabled
        try:
            result = parse(
                await self._fetch(
                    facets.path,
                    params=facets.query,
                    proxy=facets.proxy,
                    follow_redirects=facets.follow_redirects,
                )
            )
        except FinvizError as exc:
            if self._stale_if_error and isinstance(exc, FINVIZ_TRANSPORT_ERRORS):
                entry = cache.get(key)
                if entry is not None:
                    return self._serve(entry, cache_hit=True, stale=True)
            return exc
        self._store(facets, key, result)
        return result

    def _cache_key(self, facets: _CacheFacets) -> str:
        cache = self._cache
        assert cache is not None  # only reachable when caching is enabled
        return cache.make_key(
            endpoint=facets.path,
            query=facets.query,
            access_tier=AccessTier.AUTHENTICATED if self._auth_cookies else AccessTier.PUBLIC,
            auth_scope=self._auth_scope,
            route_fingerprint=self._route_fingerprint(self._normalize_per_call_proxy(facets.proxy)),
            browser_profile=self.browser_profile,
            representation=facets.representation,
            parser_version=facets.parser_version,
            schema_version=facets.schema_version,
        )

    def _store(self, facets: _CacheFacets, key: str, result: FetchResult[Any]) -> None:
        now = monotonic()

        cache = self._cache
        assert cache is not None  # only reachable when caching is enabled
        cache.set(
            key,
            CacheEntry(
                result=self._isolated(result),
                expires_at=now + (self._cache_ttl or 0.0),
                stored_at=now,
                approx_bytes=max(1, _payload_size(result.data)),
            ),
        )

    @staticmethod
    def _isolated(result: FetchResult[Any]) -> FetchResult[Any]:
        """Snapshot the parsed result so no caller mutation can reach the cache."""
        return replace(
            result,
            metadata=replace(result.metadata, cache_hit=False, stale=False, cache_age=None),
        )

    @staticmethod
    def _serve(entry: Any, *, cache_hit: bool, stale: bool) -> FetchResult[Any]:
        """Return a cached parsed result with this serve's provenance facts."""
        result: FetchResult[Any] = entry.result
        age = (datetime.now(UTC) - result.metadata.fetched_at).total_seconds()
        return replace(
            result,
            metadata=replace(
                result.metadata,
                served_at=datetime.now(UTC),
                cache_hit=cache_hit,
                stale=stale,
                cache_age=age,
            ),
        )

    @staticmethod
    def _serve_joiner(result: FetchResult[Any]) -> FetchResult[Any]:
        """Provenance for a caller that joined an in-flight miss.

        The joined payload is the leader's, so its freshness facts carry over:
        a stale-fallback leader yields stale/aged joiner results too.
        """
        return replace(
            result,
            metadata=replace(
                result.metadata,
                served_at=datetime.now(UTC),
                cache_hit=True,
            ),
        )

    def invalidate(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        proxy: str | bool | None = None,
        representation: str = "default",
        parser_version: str = "1",
        schema_version: int = 1,
    ) -> bool:
        """Drop one cached route entry; ``True`` when an entry was held.

        Takes the same route identity inputs as the cached operation, so
        per-call-proxy routes are addressable.
        """
        store = self._cache
        if store is None:
            return False
        return store.delete(
            self._cache_key(
                _CacheFacets(
                    path=path,
                    query=dict(params or {}),
                    proxy=proxy,
                    representation=representation,
                    parser_version=parser_version,
                    schema_version=schema_version,
                )
            )
        )

    def clear_cache(self) -> int:
        """Drop every cached entry; returns how many were held."""
        store = self._cache
        if store is None:
            return 0
        return store.clear()

    def _endpoint_op(
        self,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        proxy: str | bool | None = None,
        cache: bool = True,
        refresh: bool = False,
        representation: str = "default",
        parser_version: str = "1",
        schema_version: int = 1,
        follow_redirects: bool = True,
        parse: Callable[[ClientResponse], FetchResult[Any]],
    ) -> Callable[[], Coroutine[Any, Any, FetchResult[Any]]]:
        """Bind one reviewed endpoint operation to its route and cache inputs.

        Returns a zero-argument coroutine function; endpoint modules expose
        named operations built on this seam instead of a generic request
        method. ``parse`` is required — the reviewed endpoint parser that
        turns the classified transport envelope into the endpoint's immutable
        ``FetchResult``; only that parsed value is ever cached. Per-call
        ``cache=False``/``refresh=True``, the strict ``follow_redirects=False``
        one-request contract, and the representation/parser/schema key facets
        are bound here so endpoint modules can expose the required controls
        without a generic-request hatch.
        """
        params = dict(query or {})

        async def op() -> FetchResult[Any]:
            return await self._cached_fetch(
                path,
                params=params,
                proxy=proxy,
                cache=cache,
                refresh=refresh,
                representation=representation,
                parser_version=parser_version,
                schema_version=schema_version,
                follow_redirects=follow_redirects,
                parse=parse,
            )

        return op

    async def _fetch(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        proxy: str | bool | None = None,
        follow_redirects: bool = True,
    ) -> ClientResponse:
        """Fetch one explicit same-origin route and return its classified envelope.

        Internal transport primitive for reviewed endpoint modules; there is no
        public arbitrary-request API. ``proxy`` is a per-call override: a URL
        takes precedence over the client proxy (including forced-direct),
        ``False`` forces direct, ``None`` keeps client config.
        ``follow_redirects=False`` enforces a one-request contract: any
        redirect response surfaces as transport drift instead of being
        followed, regardless of the client's global safety checking.
        """
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            msg = f"route must be a relative {self.base_url} path, got {path!r}"
            raise FinvizQueryError(msg)
        if "?" in path or "#" in path:
            msg = (
                "route must not carry a query or fragment; pass parameters "
                f"through the params mapping instead, got {path!r}"
            )
            raise FinvizQueryError(msg)
        url = f"{self.base_url}{path}"
        query = dict(params or {})
        per_call_proxy = self._normalize_per_call_proxy(proxy)
        # A per-call URL wins over forced-direct; a per-call False/None on a
        # forced-direct client keeps direct. Conflicts surface via
        # _acquire_proxy's authenticated-route pin.
        selected = None if self._force_direct and per_call_proxy is _UNPINNED else per_call_proxy
        await self._ensure_entered()
        attempts = 0
        last_error: Exception | None = None
        selected_proxy: str | None = None
        route_selected = False

        # Client-local retry loop: silent by design (no log records), pins one
        # route for every attempt, and never retries typed finvizp verdicts.
        # ponytail: re-implements fastreq RetryStrategy's 20-line loop so its
        # loguru logging stays off; revisit if retry policy grows policies.
        while True:
            attempts += 1
            try:
                if not route_selected:
                    selected_proxy = await self._acquire_proxy(selected)
                    route_selected = True
                if self._limiter is not None:
                    await self._limiter.acquire()
                async with self._semaphore:
                    # Auto-follow is off: every Location is validated against
                    # the canonical origin BEFORE the next request is issued,
                    # so caller cookies can never reach another host.
                    # Endpoint ops may tighten this to a strict one-request
                    # contract: then any redirect is transport drift, never a
                    # second request.
                    hop_url = url
                    redirects = 0
                    while True:
                        response = await self._backend.request(
                            RequestConfig(
                                url=hop_url,
                                params=query if hop_url == url else None,
                                headers=self._headers(),
                                cookies=dict(self._auth_cookies),
                                timeout=self._timeout,
                                proxy=selected_proxy,
                                follow_redirects=False,
                            )
                        )
                        # The backend caches proxy-scoped sessions, and
                        # curl_cffi sessions persist server Set-Cookie state
                        # across calls. Caller authentication is per-request
                        # and never retained: strip everything the server may
                        # have planted before the session is reused.
                        self._purge_backend_cookies(selected_proxy)
                        if response.status_code not in _REDIRECT_STATUSES:
                            break
                        if not follow_redirects:
                            # One-request contract (e.g. the symbols manifest):
                            # even a same-origin Location is never a second
                            # request; it surfaces as transport drift.
                            raise FinvizTransportError(
                                "unexpected redirect (no-follow route)",
                                context={"endpoint": path},
                            )
                        location = response.headers.get("location")
                        if not location:
                            break  # classified as a non-200 provider status
                        next_url = urljoin(hop_url, location)
                        if not _is_finviz_location(next_url):
                            raise FinvizTransportError(
                                "cross-origin redirect", context={"endpoint": path}
                            )
                        if redirects >= _MAX_REDIRECTS:
                            msg = f"too many redirects for {path!r}"
                            raise FinvizTransportError(msg)
                        hop_url = next_url
                        redirects += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                retryable = not isinstance(exc, _NEVER_RETRY) and isinstance(
                    exc, (BackendError, RetryableResponse)
                )
                if not retryable or attempts > self._retry_attempts:
                    break
                delay = _parse_retry_after(getattr(exc, "retry_after", None))
                if delay is None:
                    delay = min(60.0, self._retry_backoff * (2 ** (attempts - 1)))
                await asyncio.sleep(delay)
            else:
                if response.status_code in _RETRYABLE_STATUSES:
                    last_error = RetryableResponse(
                        f"retryable status {response.status_code} from {url}",
                        status_code=response.status_code,
                        retry_after=_parse_retry_after(response.headers.get("retry-after")),
                        url=url,
                    )
                    if attempts > self._retry_attempts:
                        break
                    delay = _parse_retry_after(getattr(last_error, "retry_after", None))
                    if delay is None:
                        delay = min(60.0, self._retry_backoff * (2 ** (attempts - 1)))
                    await asyncio.sleep(delay)
                    continue
                if (
                    selected_proxy is not None
                    and self._pool is not None
                    and selected_proxy == self._pinned_proxy
                ):
                    await self._pool.mark_success(selected_proxy)
                return self._finish(
                    endpoint=path,
                    url=url,
                    query=query,
                    response=response,
                    proxy=selected_proxy,
                    attempts=attempts,
                )

        self._emit(
            ClientEvent(
                endpoint=path,
                ok=False,
                attempts=attempts,
                # Fingerprint the route this call actually used or tried to
                # select: only a completed selection may name it directly,
                # otherwise resolve the configured route (pinned/explicit/
                # pool) instead of silently claiming direct.
                route_fingerprint=self._route_fingerprint(
                    selected_proxy if route_selected else _UNPINNED
                ),
            )
        )
        raise self._classify_failure(
            last_error
            if isinstance(last_error, Exception)
            else FinvizTransportError("fetch failed")
        )

    def _headers(self) -> dict[str, str]:
        headers = dict(_DEFAULT_HEADERS)
        if self.browser_profile == "none":
            headers["User-Agent"] = _FALLBACK_UA
        return headers

    def _purge_backend_cookies(self, proxy: str | None) -> None:
        """Clear server-set cookies on the backend session used for ``proxy``.

        Only CurlCffiBackend caches cookie-bearing sessions; other backends
        (including test doubles) simply have no cookie state to purge.
        """
        backend = self._backend
        sessions = getattr(backend, "_sessions", None)
        if not isinstance(sessions, dict):
            return
        from fastreq.backends.base import TransportKey

        for key, session in sessions.items():
            if isinstance(key, TransportKey) and key.proxy == proxy:
                jar = getattr(session, "cookies", None)
                if jar is not None:
                    try:
                        jar.clear()
                    except Exception:
                        session.cookies = {}

    def _finish(
        self,
        *,
        endpoint: str,
        url: str,
        query: Mapping[str, Any],
        response: NormalizedResponse,
        proxy: str | None,
        attempts: int,
    ) -> ClientResponse:
        status = response.status_code
        final_url = response.url
        error: FinvizError | None = None
        if status == 200 and _is_elite_location(final_url):
            error = FinvizEntitlementError(
                "elite/login route reached",
                context={"endpoint": endpoint},
            )
        elif not _is_finviz_location(final_url):
            error = FinvizTransportError("cross-origin redirect", context={"endpoint": endpoint})
        elif status == 403:
            error = FinvizBlockedError("access blocked (403)", context={"endpoint": endpoint})
        elif status == 429:
            error = FinvizRateLimitError(
                "rate limited (429)",
                context={
                    "endpoint": endpoint,
                    "retry_after": _parse_retry_after(response.headers.get("retry-after")),
                },
            )
        elif status == 404:
            error = FinvizNotFoundError("resource not found (404)", context={"endpoint": endpoint})
        elif status == 200 and _NOT_FOUND_TITLE.search(response.text):
            # Finviz serves soft-404s as 200 pages titled "Page was not found".
            error = FinvizNotFoundError(
                "resource not found (200 not-found page)",
                context={"endpoint": endpoint},
            )
        elif status != 200:
            error = FinvizTransportError(
                f"provider returned status {status}", context={"endpoint": endpoint}
            )
        if error is not None:
            self._emit(
                ClientEvent(
                    endpoint=endpoint,
                    ok=False,
                    status_code=status,
                    attempts=attempts,
                    route_fingerprint=self._route_fingerprint(proxy),
                )
            )
            raise error
        classified = classify_response(
            endpoint,
            url=url,
            query=query,
            response=response,
            response_hash=hashlib.sha256(response.content).hexdigest(),
            fetched_at=datetime.now(UTC),
            access_tier=AccessTier.AUTHENTICATED if self._auth_cookies else AccessTier.PUBLIC,
            browser_profile=self.browser_profile,
            route_fingerprint=self._route_fingerprint(proxy),
            attempts=attempts,
        )
        self._emit(
            ClientEvent(
                endpoint=endpoint,
                ok=True,
                status_code=status,
                content_kind=classified.content_kind,
                attempts=attempts,
                route_fingerprint=self._route_fingerprint(proxy),
            )
        )
        # Raw bytes were hashed and classified; nothing retains them past here.
        del response
        return classified

    @staticmethod
    def _classify_failure(exc: Exception) -> FinvizError:
        if isinstance(exc, FinvizError):
            return exc
        if isinstance(exc, RetryableResponse):
            if exc.status_code == 429:
                return FinvizRateLimitError(
                    "rate limited (429)", context={"retry_after": exc.retry_after}
                )
            if exc.status_code == 403:
                return FinvizBlockedError("access blocked (403)")
            if exc.status_code == 404:
                return FinvizNotFoundError("resource not found (404)")
            return FinvizTransportError(f"provider server error ({exc.status_code})")
        if isinstance(exc, BackendError):
            return FinvizTransportError(
                "transport failure", context={"timeout": _is_timeout_error(exc)}
            )
        return FinvizTransportError("transport failure", context={"timeout": False})
