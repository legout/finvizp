"""Classified Finviz transport client.

Owns one fastreq ``Backend`` (transport seam), explicit same-origin routes,
proxy precedence, caller-supplied auth isolation, SHA-256 hashing, bounded
retries, and typed response classification.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from fastreq.backends.base import Backend, NormalizedResponse, RequestConfig
from fastreq.backends.curl_cffi import CurlCffiBackend
from fastreq.exceptions import BackendError, RetryableResponse
from fastreq.utils.proxies import ProxyPool, ProxyPoolConfig
from fastreq.utils.rate_limiter import AsyncRateLimiter, RateLimitConfig

from finvizp.errors import (
    FinvizBlockedError,
    FinvizEntitlementError,
    FinvizError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizQueryError,
    FinvizRateLimitError,
    FinvizTransportError,
    redact_value,
)
from finvizp.results import AccessTier

__all__ = ["ClientEvent", "ClientResponse", "FinvizClient", "classify_response"]

BASE_URL = "https://finviz.com"
_DEFAULT_BROWSER_PROFILE = "chrome"

# Transient statuses classified as retryable; Retry-After honored.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_UNPINNED = object()  # sentinel: no pool proxy acquired yet
_ROUTE_PREFIX = "finviz-route-v1"
_ELITE_PATH = re.compile(r"(?:^|/)(?:login\.aspx|elite\.aspx)$")

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
        return None if not base else "bytes"
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


def _safe_headers(headers: Mapping[str, str]) -> MappingProxyType[str, str]:
    return MappingProxyType(dict(redact_value(headers)))


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
    ``html``/``xml``, raw bytes (image/chart artifacts) for ``bytes``. Raw
    bytes are hashed then released; text kinds never retain the bytes object.
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

    def __post_init__(self) -> None:
        if not isinstance(self.query, MappingProxyType):
            object.__setattr__(self, "query", MappingProxyType(dict(self.query)))
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
            msg = f"malformed JSON body for {endpoint!r}"
            raise FinvizParseError(msg)
    elif kind == "bytes":
        data = response.content
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
            cookies are never sent anywhere else.
        proxy: explicit proxy URL; ``False``/``""`` forces direct.
        proxies: explicit pool list; ``False``/``[]`` disables all discovery.
        auth_cookies: caller-supplied session state, sent per request only;
            never read from the environment, never persisted.
        browser_profile: fixed browser/TLS identity (``"none"`` disables
            impersonation); no randomization.
        rate_limit: requests per second, or ``None`` for no limit.
        concurrency: maximum in-flight requests.
        timeout: per-request timeout in seconds.
        retry_attempts: bounded retries for transient transport/5xx/429 only.
        retry_backoff: base seconds for exponential backoff (capped at 60s).
        on_event: opt-in diagnostic callback receiving ``ClientEvent`` values.
    """

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
    ) -> None:
        if (normalized := base_url.rstrip("/")) != BASE_URL:
            msg = f"base_url must be {BASE_URL}, got {base_url!r}"
            raise FinvizQueryError(msg)
        self.base_url = normalized
        self.browser_profile = browser_profile
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
        self._force_direct = False
        self._explicit_proxy: str | None = None
        if proxy is False or proxy == "":
            self._force_direct = True
        elif isinstance(proxy, str):
            self._explicit_proxy = proxy
        self._pool: ProxyPool | None = None
        if proxies is False or (isinstance(proxies, (list, tuple)) and not proxies):
            self._force_direct = True
        elif isinstance(proxies, (list, tuple)):
            self._pool = ProxyPool(proxies=list(proxies), config=ProxyPoolConfig())
        if not self._force_direct and self._explicit_proxy is None and self._pool is None:
            import os

            if env_proxy := os.getenv("FINVIZP_PROXY", ""):
                self._explicit_proxy = env_proxy
            else:
                self._pool = ProxyPool.from_env() or None
                if self._pool is not None and self._pool.count() == 0:
                    self._pool = None
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
            return proxy
        msg = "proxy must be a URL, False, or None"
        raise FinvizQueryError(msg)

    async def _acquire_proxy(self, override: str | object | None = _UNPINNED) -> str | None:
        """Select the route's proxy once; the result is pinned for this client.

        Authenticated state (cookies) is bound to one exit route: once a proxy
        is acquired it is reused verbatim for every later request, so a pool
        can never rotate the identity mid-session or after a 429. Failures of
        the selection itself surface as transport errors.
        """
        async with self._route_lock:
            if self._force_direct:
                selected = None
            elif override is not _UNPINNED:
                selected = override
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
            return selected if isinstance(selected, str) else None

    def _emit(self, event: ClientEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)

    async def fetch(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        proxy: str | bool | None = None,
    ) -> ClientResponse:
        """Fetch one explicit same-origin route and return its classified envelope.

        ``proxy`` is a per-call override: a URL takes precedence over the
        client proxy, ``False`` forces direct, ``None`` keeps client config.
        """
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            msg = f"route must be a relative {self.base_url} path, got {path!r}"
            raise FinvizQueryError(msg)
        url = f"{self.base_url}{path}"
        query = dict(params or {})
        per_call_proxy = self._normalize_per_call_proxy(proxy)
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
                    selected_proxy = await self._acquire_proxy(per_call_proxy)
                    route_selected = True
                if self._limiter is not None:
                    await self._limiter.acquire()
                async with self._semaphore:
                    response = await self._backend.request(
                        RequestConfig(
                            url=url,
                            params=query,
                            headers=self._headers(),
                            cookies=dict(self._auth_cookies),
                            timeout=self._timeout,
                            proxy=selected_proxy,
                        )
                    )
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
                route_fingerprint=self._route_fingerprint(),
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
