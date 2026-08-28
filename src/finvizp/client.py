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
from urllib.parse import urljoin, urlsplit

from fastreq.backends.base import Backend, NormalizedResponse, RequestConfig
from fastreq.backends.curl_cffi import CurlCffiBackend
from fastreq.exceptions import BackendError, RetryableResponse
from fastreq.utils.proxies import ProxyPool, ProxyPoolConfig
from fastreq.utils.rate_limiter import AsyncRateLimiter, RateLimitConfig

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
from finvizp.results import AccessTier

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


def _is_valid_proxy_url(value: str) -> bool:
    """Structural check for client-accepted proxy forms.

    Supersedes fastreq's prefix-only check: scheme URLs must carry a real
    authority (host, sane port); bare host:port[/user:pass] stay supported.
    Never returns the input to callers — errors must stay route-free.
    """
    if not value or not isinstance(value, str):
        return False
    parts = urlsplit(value)
    if parts.scheme in ("http", "https"):
        if not parts.hostname:
            return False
        try:
            return parts.port is None or 0 < parts.port <= 65535
        except ValueError:
            return False
    if parts.scheme:
        return False
    pieces = value.split(":")
    return len(pieces) in (2, 4) and bool(pieces[0]) and pieces[1].isdigit()


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
            msg = f"malformed JSON body for {endpoint!r}"
            raise FinvizParseError(msg)
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
        the selection itself surface as transport errors.
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
            return selected if isinstance(selected, str) else None

    def _emit(self, event: ClientEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)

    async def _fetch(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        proxy: str | bool | None = None,
    ) -> ClientResponse:
        """Fetch one explicit same-origin route and return its classified envelope.

        Internal transport primitive for reviewed endpoint modules; there is no
        public arbitrary-request API. ``proxy`` is a per-call override: a URL
        takes precedence over the client proxy (including forced-direct),
        ``False`` forces direct, ``None`` keeps client config.
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
                route_fingerprint=self._route_fingerprint(selected_proxy),
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
