"""Hermetic contract tests for the classified FinvizClient transport."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import MappingProxyType
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse
from fastreq.backends.curl_cffi import CurlCffiBackend
from fastreq.exceptions import BackendError

from finvizp.client import ClientEvent, ClientResponse, FinvizClient
from finvizp.errors import (
    REDACTED,
    FinvizBlockedError,
    FinvizEntitlementError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizQueryError,
    FinvizRateLimitError,
    FinvizTransportError,
)
from finvizp.models import Artifact
from finvizp.results import AccessTier

BASE = "https://finviz.com"


def _resp(
    status: int = 200,
    body: bytes = b"",
    content_type: str = "text/html",
    url: str = f"{BASE}/quote.ashx",
    headers: dict[str, str] | None = None,
) -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=status,
        headers=headers if headers is not None else {"Content-Type": content_type},
        content=body,
        url=url,
        is_json="json" in content_type,
    )


class FakeTransport(Backend):
    """Scripted Backend double: records RequestConfig, replays responses/raises."""

    def __init__(self, *scripted: Any) -> None:
        self.scripted: list[Any] = list(scripted)
        self.calls: list[Any] = []
        self.enters = 0
        self.exits = 0
        self.closed = 0

    @property
    def name(self) -> str:
        return "fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls.append(config)
        item = self.scripted.pop(0) if self.scripted else _resp()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        self.closed += 1

    async def __aenter__(self) -> FakeTransport:
        self.enters += 1
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.exits += 1

    def supports_http2(self) -> bool:
        return True


def _client(fake: FakeTransport, **kwargs: Any) -> FinvizClient:
    kwargs.setdefault("retry_attempts", 0)
    kwargs.setdefault("retry_backoff", 0.0)
    return FinvizClient(transport=fake, **kwargs)


# --- lifecycle and ownership -------------------------------------------------


async def test_context_manager_enters_and_exits_transport() -> None:
    fake = FakeTransport()
    async with _client(fake) as client:
        assert isinstance(client, FinvizClient)
    assert fake.enters == 1
    assert fake.exits == 1


async def test_close_closes_transport_exactly_once() -> None:
    fake = FakeTransport()
    client = _client(fake)
    await client.close()
    await client.close()
    assert fake.closed == 2  # delegated, never doubled internally


async def test_fetch_works_without_context_manager_and_close_is_safe() -> None:
    fake = FakeTransport(_resp(200, b"<html/>"))
    resp = await _client(fake)._fetch("/quote.ashx", params={"t": "AAPL"})
    assert resp.status_code == 200
    assert resp.attempts == 1
    client = _client(fake)
    await client.close()
    await client.close()


# --- route construction ------------------------------------------------------


async def test_fixed_base_route_and_params() -> None:
    fake = FakeTransport(_resp())
    await _client(fake)._fetch("/quote.ashx", params={"t": "AAPL"})
    assert fake.calls[0].url == f"{BASE}/quote.ashx"
    assert fake.calls[0].params == {"t": "AAPL"}


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.com/quote.ashx",
        "http://finviz.com/quote.ashx",
        "//evil.com/quote.ashx",
        "quote.ashx",
        "",
    ],
)
async def test_rejects_absolute_and_cross_origin_routes_before_transport(path: str) -> None:
    fake = FakeTransport()
    client = _client(fake)
    with pytest.raises(FinvizQueryError):
        await client._fetch(path)
    assert fake.calls == []


# --- proxy precedence --------------------------------------------------------


async def test_explicit_proxy_beats_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVIZP_PROXY", "http://env-proxy:1")
    fake = FakeTransport(_resp())
    await _client(fake, proxy="http://explicit-proxy:2")._fetch("/quote.ashx")
    assert fake.calls[0].proxy == "http://explicit-proxy:2"


async def test_finvizp_proxy_env_used_without_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVIZP_PROXY", "http://env-proxy:1")
    fake = FakeTransport(_resp())
    await _client(fake)._fetch("/quote.ashx")
    assert fake.calls[0].proxy == "http://env-proxy:1"


async def test_fastreq_pool_env_is_standard_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTREQ_PROXIES", "http://pool-proxy:3")
    fake = FakeTransport(_resp())
    client = _client(fake)
    await client._fetch("/quote.ashx")
    assert fake.calls[0].proxy is not None
    assert "pool-proxy" in fake.calls[0].proxy


@pytest.mark.parametrize("disabled", [{"proxy": False}, {"proxy": ""}, {"proxies": []}])
async def test_disabled_proxy_forces_direct(
    monkeypatch: pytest.MonkeyPatch, disabled: dict[str, Any]
) -> None:
    monkeypatch.setenv("FINVIZP_PROXY", "http://env-proxy:1")
    monkeypatch.setenv("FASTREQ_PROXIES", "http://pool-proxy:3")
    fake = FakeTransport(_resp())
    await _client(fake, **disabled)._fetch("/quote.ashx")
    assert fake.calls[0].proxy is None


async def test_route_fingerprint_is_safe_and_stable() -> None:
    direct = FakeTransport(_resp(), _resp())
    proxied = FakeTransport(_resp())
    await _client(direct)._fetch("/quote.ashx")
    first = await _client(direct)._fetch("/quote.ashx")
    second = await _client(direct)._fetch("/quote.ashx")
    via_proxy = await _client(proxied, proxy="http://secret-proxy:8080")._fetch("/quote.ashx")
    assert first.route_fingerprint == second.route_fingerprint
    assert via_proxy.route_fingerprint != first.route_fingerprint
    assert "http" not in via_proxy.route_fingerprint
    assert "secret-proxy" not in via_proxy.route_fingerprint


# --- auth isolation ----------------------------------------------------------


async def test_auth_cookies_passed_once_and_never_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINVIZP_COOKIES", "sidecar=leak")
    monkeypatch.setenv("COOKIE", "leak=1")
    cookies = {"sid": "abc"}
    fake = FakeTransport(_resp())
    await _client(fake, auth_cookies=cookies)._fetch("/quote.ashx")
    assert fake.calls[0].cookies == {"sid": "abc"}
    assert cookies == {"sid": "abc"}  # caller-owned dict never mutated


async def test_no_cookies_sent_without_caller_supplied_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COOKIE", "leak=1")
    fake = FakeTransport(_resp())
    await _client(fake)._fetch("/quote.ashx")
    assert not fake.calls[0].cookies


async def test_auth_state_is_route_local_per_client() -> None:
    authed_fake = FakeTransport(_resp())
    plain_fake = FakeTransport(_resp())
    await _client(authed_fake, auth_cookies={"sid": "abc"})._fetch("/quote.ashx")
    await _client(plain_fake)._fetch("/quote.ashx")
    assert authed_fake.calls[0].cookies == {"sid": "abc"}
    assert not plain_fake.calls[0].cookies


async def test_access_tier_infers_authentication_not_elite() -> None:
    fake = FakeTransport(_resp())
    resp = await _client(fake, auth_cookies={"sid": "abc"})._fetch("/quote.ashx")
    assert resp.access_tier is AccessTier.AUTHENTICATED
    plain = await _client(FakeTransport(_resp()))._fetch("/quote.ashx")
    assert plain.access_tier is AccessTier.PUBLIC


# --- browser profile identity ------------------------------------------------


async def test_fixed_browser_profile_is_pinned_and_reported() -> None:
    fake = FakeTransport(_resp())
    client = _client(fake, browser_profile="chrome131")
    resp = await client._fetch("/quote.ashx")
    assert resp.browser_profile == "chrome131"
    headers = fake.calls[0].headers or {}
    assert "User-Agent" not in {k.lower() for k in headers}  # curl impersonation owns UA
    default = await _client(FakeTransport(_resp()))._fetch("/quote.ashx")
    assert default.browser_profile == "chrome"


# --- classified envelope -----------------------------------------------------


async def test_json_response_envelope() -> None:
    body = b'{"a": 1}'
    fake = FakeTransport(_resp(200, body, "application/json"))
    resp = await _client(fake)._fetch("/api/suggestions", params={"q": "AAP"})
    assert isinstance(resp, ClientResponse)
    assert resp.content_kind == "json"
    assert resp.data == {"a": 1}
    assert resp.response_hash == hashlib.sha256(body).hexdigest()
    assert resp.status_code == 200
    assert resp.attempts == 1
    assert resp.endpoint == "/api/suggestions"
    assert resp.query == MappingProxyType({"q": "AAP"})
    assert not hasattr(resp, "content")  # raw bytes never retained
    assert not isinstance(resp.data, bytes)
    assert resp.fetched_at.utcoffset() == timedelta(0)
    assert abs((datetime.now(UTC) - resp.fetched_at).total_seconds()) < 60


async def test_html_response_keeps_text_only() -> None:
    body = b"<html><body>hi</body></html>"
    resp = await _client(FakeTransport(_resp(200, body)))._fetch("/quote.ashx")
    assert resp.content_kind == "html"
    assert resp.data == body.decode()
    assert not hasattr(resp, "content")


async def test_xml_response_classified() -> None:
    body = b"<urlset/>"
    resp = await _client(FakeTransport(_resp(200, body, "text/xml")))._fetch("/sitemap.xml")
    assert resp.content_kind == "xml"
    assert isinstance(resp.data, str)
    assert "<urlset/>" in resp.data


async def test_image_responses_return_descriptor_not_raw_bytes() -> None:
    body = b"\x89PNG fake"
    resp = await _client(FakeTransport(_resp(200, body, "image/png")))._fetch("/chart.ashx")
    assert resp.content_kind == "artifact"
    assert isinstance(resp.data, Artifact)
    assert not isinstance(resp.data, bytes)
    assert resp.data.content_hash == hashlib.sha256(body).hexdigest()
    assert resp.data.content_length == len(body)
    assert resp.data.media_type == "image/png"
    assert resp.data.source_url == f"{BASE}/chart.ashx"
    assert resp.response_hash == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize(
    ("headers", "label"),
    [
        ({"Content-Type": "garbage"}, "garbage"),
        ({}, "missing"),
    ],
)
async def test_malformed_content_type_is_parse_drift(headers: dict[str, str], label: str) -> None:
    fake = FakeTransport(_resp(200, b"x", headers=headers))
    with pytest.raises(FinvizParseError):
        await _client(fake)._fetch("/quote.ashx")
    assert label  # parametrization documentation


async def test_malformed_json_body_is_parse_drift() -> None:
    fake = FakeTransport(_resp(200, b"{not json", "application/json"))
    with pytest.raises(FinvizParseError):
        await _client(fake)._fetch("/api/suggestions")


# --- typed status classification ---------------------------------------------


async def test_403_is_blocked() -> None:
    fake = FakeTransport(_resp(403))
    with pytest.raises(FinvizBlockedError):
        await _client(fake)._fetch("/quote.ashx")


async def test_429_is_rate_limit_with_retry_after() -> None:
    fake = FakeTransport(_resp(429, headers={"Content-Type": "text/html", "retry-after": "5"}))
    with pytest.raises(FinvizRateLimitError) as excinfo:
        await _client(fake)._fetch("/quote.ashx")
    assert excinfo.value.context.get("retry_after") == 5.0


async def test_login_redirect_is_entitlement() -> None:
    fake = FakeTransport(_resp(200, url=f"{BASE}/login.aspx"))
    with pytest.raises(FinvizEntitlementError):
        await _client(fake)._fetch("/quote.ashx")


async def test_404_is_not_found() -> None:
    fake = FakeTransport(_resp(404))
    with pytest.raises(FinvizNotFoundError):
        await _client(fake)._fetch("/quote.ashx")


async def test_timeout_is_transport_error() -> None:
    fake = FakeTransport(BackendError("Request failed: timed out"))
    with pytest.raises(FinvizTransportError):
        await _client(fake)._fetch("/quote.ashx")


async def test_5xx_is_transport_error() -> None:
    fake = FakeTransport(_resp(503))
    with pytest.raises(FinvizTransportError) as excinfo:
        await _client(fake)._fetch("/quote.ashx")
    assert not isinstance(excinfo.value, FinvizRateLimitError)


async def test_error_messages_and_context_never_leak_proxy_urls() -> None:
    fake = FakeTransport(BackendError("boom"))
    client = _client(fake, proxy="http://user:pass@secret-proxy:9")
    with pytest.raises(FinvizTransportError) as excinfo:
        await client._fetch("/quote.ashx")
    rendered = str(excinfo.value)
    assert "secret-proxy" not in rendered
    assert "user:pass" not in rendered


# --- bounded retry -----------------------------------------------------------


async def test_transient_failures_are_retried_then_succeed() -> None:
    fake = FakeTransport(BackendError("boom"), BackendError("boom"), _resp(200, b"ok"))
    client = _client(fake, retry_attempts=3, retry_backoff=0.0)
    resp = await client._fetch("/quote.ashx")
    assert len(fake.calls) == 3
    assert resp.attempts == 3
    assert resp.data == "ok"


async def test_retry_exhaustion_raises_typed_error() -> None:
    fake = FakeTransport(BackendError("boom"), BackendError("boom"), BackendError("boom"))
    with pytest.raises(FinvizTransportError):
        await _client(fake, retry_attempts=2, retry_backoff=0.0)._fetch("/quote.ashx")
    assert len(fake.calls) == 3  # initial attempt + 2 retries


@pytest.mark.parametrize(
    ("scripted", "error"),
    [
        (_resp(403), FinvizBlockedError),
        (_resp(404), FinvizNotFoundError),
        (_resp(200, url=f"{BASE}/login.aspx"), FinvizEntitlementError),
        (_resp(200, b"x", "garbage"), FinvizParseError),
    ],
)
async def test_no_retry_for_blocked_notfound_entitlement_parse(
    scripted: NormalizedResponse, error: type[Exception]
) -> None:
    fake = FakeTransport(scripted, _resp(), _resp())
    with pytest.raises(error):
        await _client(fake, retry_attempts=3, retry_backoff=0.0)._fetch("/quote.ashx")
    assert len(fake.calls) == 1


async def test_no_retry_for_query_error() -> None:
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError):
        await _client(fake, retry_attempts=3)._fetch("https://evil.com/x")
    assert fake.calls == []


async def test_429_retry_after_delay_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("finvizp.client.asyncio.sleep", fake_sleep)
    fake = FakeTransport(
        _resp(429, headers={"Content-Type": "text/html", "retry-after": "5"}),
        _resp(200, b"ok"),
    )
    resp = await _client(fake, retry_attempts=1, retry_backoff=99.0)._fetch("/quote.ashx")
    assert sleeps == [5.0]
    assert resp.attempts == 2


async def test_backoff_caps_at_bounded_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("finvizp.client.asyncio.sleep", fake_sleep)
    fake = FakeTransport(BackendError("boom"), BackendError("boom"), _resp(200))
    client = FinvizClient(transport=fake, retry_attempts=5, retry_backoff=100.0)
    await client._fetch("/quote.ashx")
    assert sleeps and max(sleeps) <= 60.0


# --- cancellation ------------------------------------------------------------


async def test_transport_cancellation_propagates_unwrapped() -> None:
    fake = FakeTransport(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await _client(fake)._fetch("/quote.ashx")


async def test_task_cancellation_propagates_immediately() -> None:
    started = asyncio.Event()

    class SlowFake(FakeTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            self.calls.append(config)
            started.set()
            await asyncio.sleep(30)
            return _resp()

    client = _client(SlowFake(), retry_attempts=3)
    task = asyncio.create_task(client._fetch("/quote.ashx"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- diagnostics and silence -------------------------------------------------


async def test_explicit_callback_receives_scrubbed_events() -> None:
    events: list[ClientEvent] = []
    fake = FakeTransport(_resp(200))
    client = _client(fake, proxy="http://secret-proxy:8080", on_event=events.append)
    await client._fetch("/quote.ashx")
    assert events
    assert all(isinstance(event, ClientEvent) for event in events)
    rendered = repr(events)
    assert "secret-proxy" not in rendered
    assert "http" not in rendered
    assert any(event.route_fingerprint for event in events)


async def test_callback_receives_error_events() -> None:
    events: list[ClientEvent] = []
    fake = FakeTransport(_resp(403))
    with pytest.raises(FinvizBlockedError):
        await _client(fake, on_event=events.append)._fetch("/quote.ashx")
    assert any(event.status_code == 403 for event in events)


async def test_default_client_emits_no_output(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeTransport(_resp(200, b"<html/>"))
    client = _client(fake)
    resp = await client._fetch("/quote.ashx", params={"t": "AAPL"})
    assert resp.status_code == 200
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_owned_transport_construction_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    FinvizClient()  # real curl transport construction; must not log anything
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# --- review regressions -------------------------------------------------------


async def test_base_url_override_is_rejected_before_transport() -> None:
    """Non-Finviz origins are rejected so cookies can never leak cross-origin."""
    fake = FakeTransport()
    cookies = {"sid": "secret"}
    with pytest.raises(FinvizQueryError):
        await _client(fake, base_url="https://example.invalid", auth_cookies=cookies)._fetch("/x")
    assert fake.calls == []  # nothing, least of all cookies, reached a transport


class TrackedFake(FakeTransport):
    """Counts concurrent in-flight requests."""

    def __init__(self, *scripted: Any) -> None:
        super().__init__(*scripted)
        self.in_flight = 0
        self.max_in_flight = 0

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls.append(config)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.02)
        self.in_flight -= 1
        return _resp()


async def test_concurrency_limit_is_client_wide() -> None:
    fake = TrackedFake()
    client = _client(fake, concurrency=1)
    await asyncio.gather(*(client._fetch("/quote.ashx") for _ in range(4)))
    assert fake.max_in_flight == 1


class SwitchingPool:
    """Hands out a different proxy on every acquire to expose route switching."""

    def __init__(self, *proxies: str) -> None:
        self.proxies = list(proxies)
        self.acquires = 0

    async def acquire(self) -> str:
        proxy = self.proxies[min(self.acquires, len(self.proxies) - 1)]
        self.acquires += 1
        return proxy

    async def mark_success(self, proxy: str) -> None:
        pass


async def test_429_retry_keeps_the_first_pool_route() -> None:
    pool = SwitchingPool("http://pool-1:1", "http://pool-2:2")
    fake = FakeTransport(_resp(429), _resp(200, b"ok"))
    client = _client(fake, retry_attempts=1, retry_backoff=0.0)
    client._pool = pool  # injected fake pool; production builds ProxyPool equivalently
    await client._fetch("/quote.ashx")
    assert [call.proxy for call in fake.calls] == ["http://pool-1:1", "http://pool-1:1"]
    assert pool.acquires == 1


async def test_authenticated_calls_pin_one_pool_route() -> None:
    pool = SwitchingPool("http://pool-1:1", "http://pool-2:2")
    fake = FakeTransport(_resp(), _resp())
    client = _client(fake, auth_cookies={"sid": "abc"})
    client._pool = pool
    await client._fetch("/quote.ashx")
    await client._fetch("/quote.ashx")
    assert [call.proxy for call in fake.calls] == ["http://pool-1:1"] * 2
    assert [call.cookies for call in fake.calls] == [{"sid": "abc"}] * 2
    assert pool.acquires == 1


async def test_retry_success_and_exhaustion_emit_no_records(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    touches: list[Any] = []

    def _record(*args: Any, **kwargs: Any) -> Any:
        touches.append(args)
        return 0

    import loguru

    monkeypatch.setattr(loguru.logger, "add", _record)
    monkeypatch.setattr(loguru.logger, "remove", _record)

    ok = FakeTransport(BackendError("boom"), _resp(200, b"ok"))
    resp = await _client(ok, retry_attempts=1, retry_backoff=0.0)._fetch("/quote.ashx")
    assert resp.attempts == 2

    exhausted = FakeTransport(BackendError("boom"), BackendError("boom"))
    with pytest.raises(FinvizTransportError):
        await _client(exhausted, retry_attempts=1, retry_backoff=0.0)._fetch("/quote.ashx")

    assert touches == []  # no log handlers attached; global logging untouched
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""


async def test_200_not_found_body_raises_not_found() -> None:
    body = b"<html><head><title>Oops! Page Not Found</title></head></html>"
    fake = FakeTransport(_resp(200, body))
    with pytest.raises(FinvizNotFoundError):
        await _client(fake)._fetch("/quote.ashx")


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>AAPL quote page with real data</body></html>",
        b"<html><body>search notes: the word was not found in the glossary</body></html>",
    ],
)
async def test_not_found_body_signature_has_no_false_positives(body: bytes) -> None:
    fake = FakeTransport(_resp(200, body))
    resp = await _client(fake)._fetch("/quote.ashx")
    assert resp.status_code == 200


async def test_failure_event_attempts_equal_actual_backend_calls() -> None:
    events: list[ClientEvent] = []
    fake = FakeTransport(BackendError("boom"), BackendError("boom"), BackendError("boom"))
    with pytest.raises(FinvizTransportError):
        await _client(fake, retry_attempts=2, retry_backoff=0.0, on_event=events.append)._fetch(
            "/quote.ashx"
        )
    assert len(fake.calls) == 3
    assert events[-1].attempts == 3  # was 4 before the off-by-one fix


async def test_success_attempts_equal_actual_backend_calls() -> None:
    events: list[ClientEvent] = []
    fake = FakeTransport(BackendError("boom"), _resp(200, b"ok"))
    resp = await _client(fake, retry_attempts=2, retry_backoff=0.0, on_event=events.append)._fetch(
        "/quote.ashx"
    )
    assert len(fake.calls) == 2
    assert resp.attempts == 2
    assert events[-1].attempts == 2


async def test_response_headers_redact_sensitive_values() -> None:
    fake = FakeTransport(
        _resp(
            headers={
                "Content-Type": "text/html",
                "Set-Cookie": "sid=secret",
                "Proxy-Authorization": "Basic c2VjcmV0",
            }
        )
    )
    response = await _client(fake)._fetch("/quote.ashx")
    rendered = repr(response.headers)
    assert "secret" not in rendered
    assert "c2VjcmV0" not in rendered


class SlowSwitchingPool(SwitchingPool):
    async def acquire(self) -> str:
        await asyncio.sleep(0.01)
        return await super().acquire()


async def test_concurrent_authenticated_calls_pin_one_pool_route() -> None:
    pool = SlowSwitchingPool("http://pool-1:1", "http://pool-2:2")
    fake = FakeTransport(_resp(), _resp())
    client = _client(fake, auth_cookies={"sid": "secret"})
    client._pool = pool
    await asyncio.gather(client._fetch("/quote.ashx"), client._fetch("/quote.ashx"))
    assert [call.proxy for call in fake.calls] == ["http://pool-1:1"] * 2
    assert pool.acquires == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://elite.finviz.com/elite.ashx",
        f"{BASE}/login.aspx?next=%2Fquote.ashx",
    ],
)
async def test_elite_locations_raise_entitlement(url: str) -> None:
    with pytest.raises(FinvizEntitlementError):
        await _client(FakeTransport(_resp(200, url=url)))._fetch("/quote.ashx")


async def test_external_redirect_is_not_classified_as_a_finviz_response() -> None:
    fake = FakeTransport(_resp(200, url="https://example.invalid/landing"))
    with pytest.raises(FinvizTransportError):
        await _client(fake)._fetch("/quote.ashx")


async def test_pool_route_fingerprints_are_distinct_and_safe() -> None:
    responses = []
    for proxy in ("http://pool-one:1", "http://pool-two:2"):
        client = _client(FakeTransport(_resp()))
        client._pool = SwitchingPool(proxy)
        responses.append(await client._fetch("/quote.ashx"))
    assert responses[0].route_fingerprint != responses[1].route_fingerprint
    assert all("http" not in response.route_fingerprint for response in responses)
    assert all("pool-" not in response.route_fingerprint for response in responses)


class SlowEnterFake(FakeTransport):
    async def __aenter__(self) -> SlowEnterFake:
        self.enters += 1
        await asyncio.sleep(0.01)
        return self


async def test_concurrent_first_fetch_enters_transport_once() -> None:
    fake = SlowEnterFake(_resp(), _resp())
    client = _client(fake)
    await asyncio.gather(client._fetch("/quote.ashx"), client._fetch("/quote.ashx"))
    assert fake.enters == 1


async def test_per_call_proxy_overrides_client_proxy_and_can_force_direct() -> None:
    fake = FakeTransport(_resp(), _resp())
    client = _client(fake, proxy="http://client-proxy:1")
    await client._fetch("/quote.ashx", proxy="http://call-proxy:2")
    await client._fetch("/quote.ashx", proxy=False)
    assert [call.proxy for call in fake.calls] == ["http://call-proxy:2", None]


async def test_authenticated_per_call_proxy_cannot_switch_the_pinned_route() -> None:
    fake = FakeTransport(_resp(), _resp())
    client = _client(fake, auth_cookies={"sid": "secret"})
    await client._fetch("/quote.ashx", proxy="http://pool-one:1")
    with pytest.raises(FinvizQueryError):
        await client._fetch("/quote.ashx", proxy="http://pool-two:2")
    assert [call.proxy for call in fake.calls] == ["http://pool-one:1"]


async def test_owned_transport_is_closed_with_client() -> None:
    client = FinvizClient()
    await client.close()  # must not raise against the real curl backend


# --- remediation round 3: isolation contract regressions ----------------------


async def test_base_url_mutation_cannot_retarget_the_origin() -> None:
    fake = FakeTransport(_resp())
    client = _client(fake, auth_cookies={"sid": "secret"})
    with pytest.raises((AttributeError, TypeError)):
        client.base_url = "https://example.invalid"  # type: ignore[misc]
    with pytest.raises(FinvizQueryError):
        FinvizClient(transport=fake, base_url="https://example.invalid", auth_cookies={"sid": "x"})
    assert fake.calls == []


async def test_random_browser_profile_is_rejected_before_transport() -> None:
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError):
        _client(fake, browser_profile="random")
    assert fake.enters == 0


async def test_invalid_proxy_is_rejected_before_pool_construction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(FinvizQueryError):
        FinvizClient(proxies=["invalid-proxy"])
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


async def test_per_call_proxy_overrides_forced_direct_client_proxy() -> None:
    fake = FakeTransport()
    client = _client(fake, proxy=False)
    await client._fetch("/quote.ashx", proxy="http://call-proxy:2")
    assert [call.proxy for call in fake.calls] == ["http://call-proxy:2"]


async def test_response_headers_preserve_metadata_and_redact_all_cookie_forms() -> None:
    fake = FakeTransport(
        _resp(
            headers={
                "Content-Type": "text/html",
                "Content-Length": "42",
                "Set-Cookie": "sid=secret",
                "Set-Cookie2": "sid2=secret2",
                "Proxy-Authorization": "Basic c2VjcmV0",
            }
        )
    )
    response = await _client(fake)._fetch("/quote.ashx")
    assert response.headers["content-type"] == "text/html"
    assert response.headers["content-length"] == "42"
    rendered = repr(response.headers)
    assert "secret" not in rendered
    assert "secret2" not in rendered
    assert "c2VjcmV0" not in rendered


async def test_paths_with_query_or_fragment_are_rejected() -> None:
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError):
        await _client(fake)._fetch("/quote.ashx?t=AAPL")
    with pytest.raises(FinvizQueryError):
        await _client(fake)._fetch("/quote.ashx#frag")
    assert fake.calls == []


async def test_request_provenance_never_retains_a_url_query_secret() -> None:
    fake = FakeTransport(_resp(200, b'{"ok": true}', "application/json"))
    resp = await _client(fake)._fetch("/api/suggestions", params={"q": "sup3rs3cret"})
    assert "?" not in resp.url
    rendered = repr(resp)
    assert "?" not in rendered
    events: list[ClientEvent] = []
    await _client(fake, on_event=events.append)._fetch("/api/suggestions", params={"q": "x"})
    assert all("?" not in repr(event) for event in events)


async def test_image_bytes_are_discarded_after_descriptor_creation() -> None:
    body = b"\x89PNG fake" * 8
    fake = FakeTransport(_resp(200, body, "image/png"))
    resp = await _client(fake)._fetch("/chart.ashx")
    assert isinstance(resp.data, Artifact)
    assert all(
        not isinstance(getattr(resp, field_name), bytes)
        for field_name in ClientResponse.__dataclass_fields__
    )
    assert "PNG" not in repr(resp)


async def test_browser_profile_is_read_only() -> None:
    client = _client(FakeTransport())
    with pytest.raises(AttributeError):
        client.browser_profile = "random"  # type: ignore[misc]


# --- real-backend integration: server cookie state must be request-local ------


class _CookieProbingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.saw_cookies.append(self.headers.get("Cookie"))
        body = b"COOKIE-LEAK" if self.headers.get("Cookie") else b"clean"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "tracked=1; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class _LocalRewriteBackend(CurlCffiBackend):
    """Real curl_cffi transport with requests retargeted to a local server."""

    def __init__(self, target: str) -> None:
        super().__init__()
        self._target = target

    async def request(self, config: Any, stream_callback: Any = None) -> Any:
        finviz_url = config.url
        config.url = finviz_url.replace("https://finviz.com", self._target, 1)
        response = await super().request(config, stream_callback)
        response.url = finviz_url  # classify against the Finviz origin
        return response


async def test_real_backend_does_not_persist_server_cookies() -> None:
    probe: list[str | None] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CookieProbingHandler)
    server.saw_cookies = probe  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        backend = _LocalRewriteBackend(f"http://127.0.0.1:{server.server_address[1]}")
        client = FinvizClient(transport=backend, proxy=False)
        first = await client._fetch("/quote.ashx")
        second = await client._fetch("/quote.ashx")
    finally:
        server.shutdown()
        server.server_close()
    assert first.data == "clean"
    assert second.data == "clean"  # no server Set-Cookie replayed on request 2
    assert probe == [None, None]


# --- round-4 remediation: redirect safety, deep header/query redaction, -------


class _RedirectProbeHandler(BaseHTTPRequestHandler):
    """302s to a second server, recording whether cookies crossed origins."""

    def do_GET(self) -> None:
        self.server.finviz_hits.append(self.headers.get("Cookie"))
        self.send_response(302)
        self.send_header("Location", self.server.redirect_target + "/landing")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


class _LandingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.external_hits.append(self.headers.get("Cookie"))
        body = b"external landing page"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class _LocalRedirectBackend(CurlCffiBackend):
    """Real curl_cffi transport; finviz origin rewritten to a local server."""

    def __init__(self, target: str) -> None:
        super().__init__()
        self._target = target

    async def request(self, config: Any, stream_callback: Any = None) -> Any:
        finviz_url = config.url
        config.url = finviz_url.replace("https://finviz.com", self._target, 1)
        response = await super().request(config, stream_callback)
        if response.url.startswith(self._target):
            response.url = finviz_url  # restore Finviz origin for classification
        return response


async def test_credential_bearing_redirects_stay_canonical_before_leaving(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cookies must never reach a cross-origin redirect destination."""
    finviz = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectProbeHandler)
    finviz.finviz_hits = []  # type: ignore[attr-defined]
    external = ThreadingHTTPServer(("127.0.0.1", 0), _LandingHandler)
    external.external_hits = []  # type: ignore[attr-defined]
    threading.Thread(target=finviz.serve_forever, daemon=True).start()
    threading.Thread(target=external.serve_forever, daemon=True).start()
    try:
        backend = _LocalRedirectBackend(f"http://127.0.0.1:{finviz.server_address[1]}")
        finviz.redirect_target = f"http://127.0.0.1:{external.server_address[1]}"  # type: ignore[attr-defined]
        client = FinvizClient(
            transport=backend, proxy=False, auth_cookies={"sid": "redirect-secret"}
        )
        with pytest.raises(FinvizTransportError):
            await client._fetch("/quote.ashx")
        captured = capsys.readouterr()
    finally:
        finviz.shutdown()
        finviz.server_close()
        external.shutdown()
        external.server_close()
    assert captured.out == "" and captured.err == ""
    assert external.external_hits == []  # the external server saw NO request at all
    # caller cookies were sent to the (rewritten) canonical origin only
    assert finviz.finviz_hits == ["sid=redirect-secret"]


async def test_safe_headers_cover_central_sensitive_labels_and_scrub_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeTransport(
        _resp(
            headers={
                "Content-Type": "text/html",
                "Content-Length": "42",
                "Set-Cookie": "sid=cookie-secret",
                "Set-Cookie2": "sid2=cookie2-secret",
                "Authorization": "Bearer bearer-secret",
                "Proxy-Authorization": "Basic c2VjcmV0",
                "X-Auth-Token": "xauth-secret",
                "X-Api-Key": "xapikey-secret",
                "X-Secret": "xsecret-secret",
                "Location": f"{BASE}/quote.ashx?token=query-secret",
                "Referer": f"{BASE}/screener.ashx?v=111&ticker=AAPL",
            }
        )
    )
    response = await _client(fake)._fetch("/quote.ashx")
    headers = response.headers
    assert headers["content-type"] == "text/html"
    assert headers["content-length"] == "42"
    assert headers["referer"] == f"{BASE}/screener.ashx?v=111&ticker=AAPL"
    assert headers["location"] == f"{BASE}/quote.ashx?token={REDACTED}"
    assert headers["x-auth-token"] == REDACTED
    assert headers["x-api-key"] == REDACTED
    assert headers["x-secret"] == REDACTED
    rendered = repr(headers)
    for secret in (
        "cookie-secret",
        "cookie2-secret",
        "bearer-secret",
        "c2VjcmV0",
        "xauth-secret",
        "xapikey-secret",
        "xsecret-secret",
        "query-secret",
    ):
        assert secret not in rendered
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


async def test_params_provenance_redacts_sensitive_values_keeps_ordinary_keys() -> None:
    fake = FakeTransport(_resp(200, b'{"ok": true}', "application/json"))
    client = _client(fake)
    resp = await client._fetch(
        "/api/suggestions", params={"t": "AAPL", "q": "AAP", "token": "query-secret"}
    )
    assert resp.query["t"] == "AAPL"
    assert resp.query["q"] == "AAP"
    assert resp.query["token"] == REDACTED
    assert "query-secret" not in repr(resp)


async def test_sensitive_params_never_reach_event_or_error_renderings() -> None:
    events: list[ClientEvent] = []
    fake = FakeTransport(BackendError("boom"), BackendError("boom"), BackendError("boom"))
    client = _client(fake, retry_attempts=2, retry_backoff=0.0, on_event=events.append)
    with pytest.raises(FinvizTransportError):
        await client._fetch("/quote.ashx", params={"token": "query-secret"})
    rendered = repr(events) + str(events[-1])
    assert "query-secret" not in rendered


@pytest.mark.parametrize("bad", [{"proxy": True}, {"proxies": True}])
async def test_boolean_proxy_values_are_rejected_without_transport(bad: dict[str, Any]) -> None:
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError):
        FinvizClient(transport=fake, **bad)
    assert fake.calls == []


@pytest.mark.parametrize("bad", [{"proxies": "http://not-a-list:1"}, {"proxies": 7}])
async def test_non_list_pool_values_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(FinvizQueryError):
        FinvizClient(transport=FakeTransport(), **bad)


async def test_per_call_proxy_must_be_url_false_or_none() -> None:
    fake = FakeTransport()
    client = _client(fake)
    with pytest.raises(FinvizQueryError):
        await client._fetch("/quote.ashx", proxy="invalid-proxy")
    with pytest.raises(FinvizQueryError):
        await client._fetch("/quote.ashx", proxy=123)  # type: ignore[arg-type]
    assert fake.calls == []  # invalid input never reaches the transport


def test_public_surface_exposes_no_raw_request_method() -> None:
    """fetch is the only request entry: route-gated, never an arbitrary-URL hatch."""
    assert {n for n in dir(FinvizClient) if not n.startswith("_")} == {
        "close",
        "fetch",
        "invalidate",
        "clear_cache",
    }


@pytest.mark.parametrize("bad", [{"proxy": 123}, {"proxy": ["http://p:1"]}, {"proxy": object()}])
def test_non_url_proxy_values_are_rejected_before_transport(bad: dict[str, Any]) -> None:
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError):
        FinvizClient(transport=fake, **bad)  # type: ignore[arg-type]
    assert fake.calls == []  # rejected before any transport/pool use


@pytest.mark.parametrize(
    "bad", [{"proxies": ("http://proxy.example:8080",)}, {"proxies": {"http://p:1"}}]
)
def test_non_list_pool_containers_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(FinvizQueryError):
        FinvizClient(transport=FakeTransport(), **bad)


# --- round 5: errors never echo proxy routes/credentials; structural URL check


_PROXY_WITH_SECRET = "socks5://u:pwsecret@exit-proxy.example:1080"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"proxy": _PROXY_WITH_SECRET},
        {"proxies": [_PROXY_WITH_SECRET]},
        {"proxy": "http://u:pwsecret@"},
        {"proxies": ["http://u:pwsecret@"]},
    ],
)
def test_invalid_proxy_errors_never_contain_route_or_credentials(
    kwargs: dict[str, Any],
) -> None:
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError) as excinfo:
        FinvizClient(transport=fake, **kwargs)  # type: ignore[arg-type]
    rendered = str(excinfo.value) + repr(excinfo.value)
    assert "exit-proxy.example" not in rendered
    assert "pwsecret" not in rendered
    assert fake.calls == []  # rejected before any transport/pool use


@pytest.mark.parametrize(
    "bad",
    [
        {"proxy": "http://"},
        {"proxy": "https://"},
        {"proxy": "http://:8080"},
        {"proxies": ["http://"]},
        {"proxies": ["http://good.example:1", "http://:1"]},
    ],
)
def test_scheme_only_proxy_urls_are_rejected_before_transport(bad: dict[str, Any]) -> None:
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError):
        FinvizClient(transport=fake, **bad)  # type: ignore[arg-type]
    assert fake.calls == []


@pytest.mark.parametrize("bad", [{"proxy": "http://"}, {"proxy": _PROXY_WITH_SECRET}])
async def test_invalid_per_call_proxy_rejected_without_leaking_route(
    bad: dict[str, Any],
) -> None:
    fake = FakeTransport()
    client = _client(fake)
    with pytest.raises(FinvizQueryError) as excinfo:
        await client._fetch("/quote.ashx", **bad)  # type: ignore[arg-type]
    rendered = str(excinfo.value) + repr(excinfo.value)
    assert "exit-proxy.example" not in rendered
    assert "pwsecret" not in rendered
    assert fake.calls == []


@pytest.mark.parametrize(
    "env_val",
    ["http://", _PROXY_WITH_SECRET],
)
def test_invalid_env_proxy_is_rejected_without_leaking_route(
    env_val: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINVIZP_PROXY", env_val.replace("pwsecret", "envsecret"))
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError) as excinfo:
        FinvizClient(transport=fake)
    rendered = str(excinfo.value) + repr(excinfo.value)
    assert "exit-proxy.example" not in rendered
    assert "envsecret" not in rendered
    assert fake.calls == []


# --- round 6: bare-form proxy validation, +json parsing, exhaustion fingerprint


@pytest.mark.parametrize("good", ["10.0.0.1:8080", "10.0.0.1:8080:user:pass", "localhost:3128"])
async def test_bare_host_port_proxies_are_accepted(good: str) -> None:
    fake = FakeTransport(_resp())
    await _client(fake, proxy=good)._fetch("/quote.ashx")
    assert fake.calls[0].proxy == good


@pytest.mark.parametrize("bad", ["127.0.0.1:99999", "127.0.0.1:0", "host:port", ":8080", "h:1:u"])
@pytest.mark.parametrize("via", ["proxy", "proxies", "per_call", "env"])
async def test_malformed_bare_proxies_are_rejected_before_transport(
    bad: str, via: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTransport()
    if via == "env":
        monkeypatch.setenv("FINVIZP_PROXY", bad)
        with pytest.raises(FinvizQueryError):
            FinvizClient(transport=fake)
    elif via == "proxies":
        with pytest.raises(FinvizQueryError):
            FinvizClient(transport=fake, proxies=[bad])
    elif via == "proxy":
        with pytest.raises(FinvizQueryError):
            FinvizClient(transport=fake, proxy=bad)
    else:
        with pytest.raises(FinvizQueryError):
            await _client(fake)._fetch("/quote.ashx", proxy=bad)
    assert fake.calls == []  # no transport, no pool, no log output


def _suffix_json_resp(body: bytes) -> NormalizedResponse:
    """Mirror the real backend: ``+json`` media types are NOT pre-parsed."""
    return NormalizedResponse.from_backend(
        status_code=200,
        headers={"Content-Type": "application/vnd.api+json"},
        content=body,
        url=f"{BASE}/api/suggestions",
        is_json=False,
    )


async def test_json_suffix_media_type_is_parsed_locally() -> None:
    fake = FakeTransport(_suffix_json_resp(b'{"a": 1}'))
    resp = await _client(fake)._fetch("/api/suggestions")
    assert resp.content_kind == "json"
    assert resp.data == {"a": 1}


async def test_malformed_json_suffix_media_type_is_parse_drift() -> None:
    fake = FakeTransport(_suffix_json_resp(b"{not json"))
    with pytest.raises(FinvizParseError):
        await _client(fake)._fetch("/api/suggestions")


async def test_retry_exhaustion_event_fingerprints_the_selected_route() -> None:
    events: list[ClientEvent] = []
    proxy = "http://exit-proxy.example:9"
    exhausted = FakeTransport(BackendError("boom"), BackendError("boom"))
    with pytest.raises(FinvizTransportError):
        await _client(
            exhausted,
            proxy=proxy,
            retry_attempts=1,
            retry_backoff=0.0,
            on_event=events.append,
        )._fetch("/quote.ashx")
    success = await _client(FakeTransport(_resp()), proxy=proxy)._fetch("/quote.ashx")
    assert events[-1].route_fingerprint == success.route_fingerprint
    direct = await _client(FakeTransport(_resp()))._fetch("/quote.ashx")
    assert events[-1].route_fingerprint != direct.route_fingerprint


async def test_retry_exhaustion_before_route_selection_fingerprints_the_pool() -> None:
    events: list[ClientEvent] = []

    class ExplodingPool:
        async def acquire(self) -> str:
            raise BackendError("no exit available")

    fake = FakeTransport(BackendError("boom"), BackendError("boom"))
    client = _client(fake, retry_attempts=1, retry_backoff=0.0, on_event=events.append)
    client._pool = ExplodingPool()
    with pytest.raises(FinvizTransportError):
        await client._fetch("/quote.ashx")
    direct = await _client(FakeTransport(_resp()))._fetch("/quote.ashx")
    assert events[-1].route_fingerprint != direct.route_fingerprint
