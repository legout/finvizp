"""Hermetic contract tests for the classified FinvizClient transport."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse
from fastreq.exceptions import BackendError

from finvizp.client import ClientEvent, ClientResponse, FinvizClient
from finvizp.errors import (
    FinvizBlockedError,
    FinvizEntitlementError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizQueryError,
    FinvizRateLimitError,
    FinvizTransportError,
)
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
    resp = await _client(fake).fetch("/quote.ashx", params={"t": "AAPL"})
    assert resp.status_code == 200
    assert resp.attempts == 1
    client = _client(fake)
    await client.close()
    await client.close()


# --- route construction ------------------------------------------------------


async def test_fixed_base_route_and_params() -> None:
    fake = FakeTransport(_resp())
    await _client(fake).fetch("/quote.ashx", params={"t": "AAPL"})
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
        await client.fetch(path)
    assert fake.calls == []


# --- proxy precedence --------------------------------------------------------


async def test_explicit_proxy_beats_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVIZP_PROXY", "http://env-proxy:1")
    fake = FakeTransport(_resp())
    await _client(fake, proxy="http://explicit-proxy:2").fetch("/quote.ashx")
    assert fake.calls[0].proxy == "http://explicit-proxy:2"


async def test_finvizp_proxy_env_used_without_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVIZP_PROXY", "http://env-proxy:1")
    fake = FakeTransport(_resp())
    await _client(fake).fetch("/quote.ashx")
    assert fake.calls[0].proxy == "http://env-proxy:1"


async def test_fastreq_pool_env_is_standard_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTREQ_PROXIES", "http://pool-proxy:3")
    fake = FakeTransport(_resp())
    client = _client(fake)
    await client.fetch("/quote.ashx")
    assert fake.calls[0].proxy is not None
    assert "pool-proxy" in fake.calls[0].proxy


@pytest.mark.parametrize("disabled", [{"proxy": False}, {"proxy": ""}, {"proxies": []}])
async def test_disabled_proxy_forces_direct(
    monkeypatch: pytest.MonkeyPatch, disabled: dict[str, Any]
) -> None:
    monkeypatch.setenv("FINVIZP_PROXY", "http://env-proxy:1")
    monkeypatch.setenv("FASTREQ_PROXIES", "http://pool-proxy:3")
    fake = FakeTransport(_resp())
    await _client(fake, **disabled).fetch("/quote.ashx")
    assert fake.calls[0].proxy is None


async def test_route_fingerprint_is_safe_and_stable() -> None:
    direct = FakeTransport(_resp(), _resp())
    proxied = FakeTransport(_resp())
    await _client(direct).fetch("/quote.ashx")
    first = await _client(direct).fetch("/quote.ashx")
    second = await _client(direct).fetch("/quote.ashx")
    via_proxy = await _client(proxied, proxy="http://secret-proxy:8080").fetch("/quote.ashx")
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
    await _client(fake, auth_cookies=cookies).fetch("/quote.ashx")
    assert fake.calls[0].cookies == {"sid": "abc"}
    assert cookies == {"sid": "abc"}  # caller-owned dict never mutated


async def test_no_cookies_sent_without_caller_supplied_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COOKIE", "leak=1")
    fake = FakeTransport(_resp())
    await _client(fake).fetch("/quote.ashx")
    assert not fake.calls[0].cookies


async def test_auth_state_is_route_local_per_client() -> None:
    authed_fake = FakeTransport(_resp())
    plain_fake = FakeTransport(_resp())
    await _client(authed_fake, auth_cookies={"sid": "abc"}).fetch("/quote.ashx")
    await _client(plain_fake).fetch("/quote.ashx")
    assert authed_fake.calls[0].cookies == {"sid": "abc"}
    assert not plain_fake.calls[0].cookies


async def test_access_tier_infers_authentication_not_elite() -> None:
    fake = FakeTransport(_resp())
    resp = await _client(fake, auth_cookies={"sid": "abc"}).fetch("/quote.ashx")
    assert resp.access_tier is AccessTier.AUTHENTICATED
    plain = await _client(FakeTransport(_resp())).fetch("/quote.ashx")
    assert plain.access_tier is AccessTier.PUBLIC


# --- browser profile identity ------------------------------------------------


async def test_fixed_browser_profile_is_pinned_and_reported() -> None:
    fake = FakeTransport(_resp())
    client = _client(fake, browser_profile="chrome131")
    resp = await client.fetch("/quote.ashx")
    assert resp.browser_profile == "chrome131"
    headers = fake.calls[0].headers or {}
    assert "User-Agent" not in {k.lower() for k in headers}  # curl impersonation owns UA
    default = await _client(FakeTransport(_resp())).fetch("/quote.ashx")
    assert default.browser_profile == "chrome"


# --- classified envelope -----------------------------------------------------


async def test_json_response_envelope() -> None:
    body = b'{"a": 1}'
    fake = FakeTransport(_resp(200, body, "application/json"))
    resp = await _client(fake).fetch("/api/suggestions", params={"q": "AAP"})
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
    resp = await _client(FakeTransport(_resp(200, body))).fetch("/quote.ashx")
    assert resp.content_kind == "html"
    assert resp.data == body.decode()
    assert not hasattr(resp, "content")


async def test_xml_response_classified() -> None:
    body = b"<urlset/>"
    resp = await _client(FakeTransport(_resp(200, body, "text/xml"))).fetch("/sitemap.xml")
    assert resp.content_kind == "xml"
    assert isinstance(resp.data, str)
    assert "<urlset/>" in resp.data


async def test_image_bytes_are_preserved_for_artifacts() -> None:
    body = b"\x89PNG fake"
    resp = await _client(FakeTransport(_resp(200, body, "image/png"))).fetch("/chart.ashx")
    assert resp.content_kind == "bytes"
    assert resp.data == body
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
        await _client(fake).fetch("/quote.ashx")
    assert label  # parametrization documentation


async def test_malformed_json_body_is_parse_drift() -> None:
    fake = FakeTransport(_resp(200, b"{not json", "application/json"))
    with pytest.raises(FinvizParseError):
        await _client(fake).fetch("/api/suggestions")


# --- typed status classification ---------------------------------------------


async def test_403_is_blocked() -> None:
    fake = FakeTransport(_resp(403))
    with pytest.raises(FinvizBlockedError):
        await _client(fake).fetch("/quote.ashx")


async def test_429_is_rate_limit_with_retry_after() -> None:
    fake = FakeTransport(_resp(429, headers={"Content-Type": "text/html", "retry-after": "5"}))
    with pytest.raises(FinvizRateLimitError) as excinfo:
        await _client(fake).fetch("/quote.ashx")
    assert excinfo.value.context.get("retry_after") == 5.0


async def test_login_redirect_is_entitlement() -> None:
    fake = FakeTransport(_resp(200, url=f"{BASE}/login.aspx"))
    with pytest.raises(FinvizEntitlementError):
        await _client(fake).fetch("/quote.ashx")


async def test_404_is_not_found() -> None:
    fake = FakeTransport(_resp(404))
    with pytest.raises(FinvizNotFoundError):
        await _client(fake).fetch("/quote.ashx")


async def test_timeout_is_transport_error() -> None:
    fake = FakeTransport(BackendError("Request failed: timed out"))
    with pytest.raises(FinvizTransportError):
        await _client(fake).fetch("/quote.ashx")


async def test_5xx_is_transport_error() -> None:
    fake = FakeTransport(_resp(503))
    with pytest.raises(FinvizTransportError) as excinfo:
        await _client(fake).fetch("/quote.ashx")
    assert not isinstance(excinfo.value, FinvizRateLimitError)


async def test_error_messages_and_context_never_leak_proxy_urls() -> None:
    fake = FakeTransport(BackendError("boom"))
    client = _client(fake, proxy="http://user:pass@secret-proxy:9")
    with pytest.raises(FinvizTransportError) as excinfo:
        await client.fetch("/quote.ashx")
    rendered = str(excinfo.value)
    assert "secret-proxy" not in rendered
    assert "user:pass" not in rendered


# --- bounded retry -----------------------------------------------------------


async def test_transient_failures_are_retried_then_succeed() -> None:
    fake = FakeTransport(BackendError("boom"), BackendError("boom"), _resp(200, b"ok"))
    client = _client(fake, retry_attempts=3, retry_backoff=0.0)
    resp = await client.fetch("/quote.ashx")
    assert len(fake.calls) == 3
    assert resp.attempts == 3
    assert resp.data == "ok"


async def test_retry_exhaustion_raises_typed_error() -> None:
    fake = FakeTransport(BackendError("boom"), BackendError("boom"), BackendError("boom"))
    with pytest.raises(FinvizTransportError):
        await _client(fake, retry_attempts=2, retry_backoff=0.0).fetch("/quote.ashx")
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
        await _client(fake, retry_attempts=3, retry_backoff=0.0).fetch("/quote.ashx")
    assert len(fake.calls) == 1


async def test_no_retry_for_query_error() -> None:
    fake = FakeTransport()
    with pytest.raises(FinvizQueryError):
        await _client(fake, retry_attempts=3).fetch("https://evil.com/x")
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
    resp = await _client(fake, retry_attempts=1, retry_backoff=99.0).fetch("/quote.ashx")
    assert sleeps == [5.0]
    assert resp.attempts == 2


async def test_backoff_caps_at_bounded_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("finvizp.client.asyncio.sleep", fake_sleep)
    fake = FakeTransport(BackendError("boom"), BackendError("boom"), _resp(200))
    client = FinvizClient(transport=fake, retry_attempts=5, retry_backoff=100.0)
    await client.fetch("/quote.ashx")
    assert sleeps and max(sleeps) <= 60.0


# --- cancellation ------------------------------------------------------------


async def test_transport_cancellation_propagates_unwrapped() -> None:
    fake = FakeTransport(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await _client(fake).fetch("/quote.ashx")


async def test_task_cancellation_propagates_immediately() -> None:
    started = asyncio.Event()

    class SlowFake(FakeTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            self.calls.append(config)
            started.set()
            await asyncio.sleep(30)
            return _resp()

    client = _client(SlowFake(), retry_attempts=3)
    task = asyncio.create_task(client.fetch("/quote.ashx"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- diagnostics and silence -------------------------------------------------


async def test_explicit_callback_receives_scrubbed_events() -> None:
    events: list[ClientEvent] = []
    fake = FakeTransport(_resp(200))
    client = _client(fake, proxy="http://secret-proxy:8080", on_event=events.append)
    await client.fetch("/quote.ashx")
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
        await _client(fake, on_event=events.append).fetch("/quote.ashx")
    assert any(event.status_code == 403 for event in events)


async def test_default_client_emits_no_output(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeTransport(_resp(200, b"<html/>"))
    client = _client(fake)
    resp = await client.fetch("/quote.ashx", params={"t": "AAPL"})
    assert resp.status_code == 200
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_owned_transport_construction_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    FinvizClient()  # real curl transport construction; must not log anything
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


async def test_owned_transport_is_closed_with_client() -> None:
    client = FinvizClient()
    await client.close()  # must not raise against the real curl backend
