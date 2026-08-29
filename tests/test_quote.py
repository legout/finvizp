"""RED-first tests for quote operations: routes, batching, strict/partial, sync (Card 0.1-H).

Every test fails until ``finvizp.quote`` exists. Hermetic: the transport double
serves the scrubbed fixture page per requested symbol; no live HTTP.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp.client import FinvizClient
from finvizp.errors import (
    FetchWarning,
    FinvizBlockedError,
    FinvizEntitlementError,
    FinvizNotFoundError,
    FinvizPartialError,
    FinvizQueryError,
    FinvizRateLimitError,
)
from finvizp.models import QuoteBundle
from finvizp.quote import quote, quote_async, snapshot_async
from finvizp.results import FetchResult, ResultStatus

BASE = "https://finviz.com"
FIXTURES = Path(__file__).parent / "fixtures" / "quote"
_PAGE = (FIXTURES / "stock-current.html").read_text("utf-8")

_NOT_FOUND_PAGE = "<html><head><title>Page was not found</title></head><body></body></html>"


def _html_resp(body: str, *, status: int = 200, url: str | None = None) -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content=body.encode(),
        url=url or f"{BASE}/stock",
        is_json=False,
    )


class QuoteTransport(Backend):
    """Symbol-aware page server with per-symbol 404 budgets and per-path overrides."""

    def __init__(
        self,
        *,
        errors: dict[str, int] | None = None,
        delay: float = 0.0,
        path_overrides: dict[str, NormalizedResponse] | None = None,
    ) -> None:
        self.errors = dict(errors or {})
        self.delay = delay
        self.path_overrides = dict(path_overrides or {})
        self.calls: list[Any] = []
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def name(self) -> str:
        return "quote-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls.append(config)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            path = urlsplit(str(config.url)).path
            override = self.path_overrides.get(path)
            if override is not None:
                return override
            symbol = str((config.params or {}).get("t", "AAPL"))
            if self.errors.get(symbol, 0) > 0:
                self.errors[symbol] -= 1
                return _html_resp("not found", status=404, url=str(config.url))
            return _html_resp(_PAGE.replace("AAPL", symbol), url=str(config.url))
        finally:
            self.in_flight -= 1

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> QuoteTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _client(fake: QuoteTransport, **kwargs: Any) -> FinvizClient:
    kwargs.setdefault("retry_attempts", 0)
    kwargs.setdefault("retry_backoff", 0.0)
    return FinvizClient(transport=fake, **kwargs)


def _paths(fake: QuoteTransport) -> list[str]:
    return [urlsplit(str(c.url)).path for c in fake.calls]


# --- canonical/fallback routes -------------------------------------------------


async def test_single_symbol_requests_canonical_stock_route() -> None:
    fake = QuoteTransport()
    result = await quote_async("AAPL", client=_client(fake))
    assert _paths(fake) == ["/stock"]
    assert str(fake.calls[0].params["t"]) == "AAPL"
    assert result.metadata.endpoint == "/stock"
    assert result.metadata.status is ResultStatus.COMPLETE


async def test_not_found_falls_back_to_quote_ashx_once() -> None:
    fake = QuoteTransport(errors={"ZZZZZ": 1})
    result = await quote_async("ZZZZZ", client=_client(fake))
    assert _paths(fake) == ["/stock", "/quote.ashx"]
    assert result.metadata.endpoint == "/quote.ashx"
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.data[0].symbol == "ZZZZZ"


async def test_soft_404_title_falls_back() -> None:
    fake = QuoteTransport(path_overrides={"/stock": _html_resp(_NOT_FOUND_PAGE)})
    result = await quote_async("AAPL", client=_client(fake))
    assert _paths(fake) == ["/stock", "/quote.ashx"]
    assert result.data[0].snapshot is not None


async def test_parse_drift_falls_back() -> None:
    fake = QuoteTransport(path_overrides={"/stock": _html_resp("<html><body>moved</body></html>")})
    result = await quote_async("AAPL", client=_client(fake))
    assert _paths(fake) == ["/stock", "/quote.ashx"]
    assert result.data[0].snapshot is not None


@pytest.mark.parametrize(
    ["override", "expected"],
    [
        (_html_resp("<html></html>", url="https://elite.finviz.com/stock"), FinvizEntitlementError),
        (_html_resp("rate limited", status=429), FinvizRateLimitError),
        (_html_resp("blocked", status=403), FinvizBlockedError),
    ],
)
async def test_no_fallback_for_entitlement_rate_limit_or_blocked(
    override: NormalizedResponse, expected: type[Exception]
) -> None:
    fake = QuoteTransport(path_overrides={"/stock": override})
    client = _client(fake)
    with pytest.raises(expected):
        await quote_async("AAPL", client=client)
    assert _paths(fake) == ["/stock"]


async def test_single_symbol_not_found_raises_not_found() -> None:
    fake = QuoteTransport(errors={"ZZZZZ": 2})
    with pytest.raises(FinvizNotFoundError):
        await quote_async("ZZZZZ", client=_client(fake))
    assert _paths(fake) == ["/stock", "/quote.ashx"]


# --- one fetch, one parse ------------------------------------------------------


async def test_one_page_fetched_and_parsed_into_bundle_result() -> None:
    fake = QuoteTransport()
    result = await quote_async("AAPL", client=_client(fake))
    assert len(fake.calls) == 1
    assert isinstance(result, FetchResult)
    assert isinstance(result.data[0], QuoteBundle)
    assert result.data[0].snapshot is not None
    assert result.data[0].symbol == "AAPL"


async def test_missing_optional_region_returns_recoverable_bundle() -> None:
    # A dropped optional region is recoverable bundle drift (bundle PARTIAL),
    # not a unit failure: the one-symbol envelope stays COMPLETE/1 succeeded,
    # and the drift is visible as missing_region warnings.
    fake = QuoteTransport(
        path_overrides={
            "/stock": _html_resp(_PAGE.replace("js-table-ratings", "js-table-ratings-x", 1))
        }
    )
    result = await quote_async("AAPL", client=_client(fake))
    assert result.data[0].status.name == "PARTIAL"
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.succeeded_units == 1
    assert result.metadata.failed_units == 0
    assert any(
        isinstance(w, FetchWarning) and w.code == "missing_region" for w in result.metadata.warnings
    )


async def test_parser_conversion_warnings_reach_metadata() -> None:
    fake = QuoteTransport(
        path_overrides={
            "/stock": _html_resp(_PAGE.replace("<td>Aug-17-26</td>", "<td>not-a-date</td>", 1))
        }
    )
    result = await quote_async("AAPL", client=_client(fake))
    assert any(w.code == "conversion_failed" for w in result.metadata.warnings)


async def test_repeat_read_hits_cache_and_preserves_original_facts() -> None:
    fake = QuoteTransport()
    client = _client(fake, cache_ttl=60.0)
    first = await quote_async("AAPL", client=client)
    second = await quote_async("AAPL", client=client)
    assert len(fake.calls) == 1
    assert second.metadata.cache_hit is True
    assert second.metadata.fetched_at == first.metadata.fetched_at
    assert second.metadata.response_hash == first.metadata.response_hash


async def test_fallback_bundle_is_reused_with_zero_warm_requests() -> None:
    fake = QuoteTransport(errors={"ZZZZZ": 1})
    client = _client(fake, cache_ttl=60.0)
    await quote_async("ZZZZZ", client=client)  # fallback route served the bundle
    warm = await snapshot_async("ZZZZZ", client=client)
    assert _paths(fake) == ["/stock", "/quote.ashx"]  # no canonical re-probe
    assert warm.metadata.endpoint == "/quote.ashx"
    assert warm.metadata.cache_hit is True
    assert warm.metadata.projected_from == "quote"


async def test_concurrent_identical_requests_share_one_fetch() -> None:
    fake = QuoteTransport(delay=0.03)
    client = _client(fake, cache_ttl=60.0)
    one, two = await asyncio.gather(
        quote_async("AAPL", client=client),
        quote_async("AAPL", client=client),
    )
    assert len(fake.calls) == 1
    assert one.metadata.fetched_at == two.metadata.fetched_at
    assert two.metadata.cache_hit is True


# --- multi-symbol semantics -----------------------------------------------------


async def test_multi_symbol_order_dedupe_and_resolution_metadata() -> None:
    fake = QuoteTransport()
    result = await quote_async(["msft", "AAPL", "aapl", "BRK.B"], client=_client(fake))
    assert [b.symbol for b in result.data] == ["MSFT", "AAPL", "BRK-B"]
    assert [str(c.params["t"]) for c in fake.calls] == ["MSFT", "AAPL", "BRK-B"]
    assert [(r.position, r.requested, r.canonical) for r in result.metadata.symbols] == [
        (0, "msft", "MSFT"),
        (1, "AAPL", "AAPL"),
        (2, "aapl", "AAPL"),
        (3, "BRK.B", "BRK-B"),
    ]
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.requested_units == 3
    assert result.metadata.succeeded_units == 3
    assert result.metadata.failed_units == 0


async def test_duplicate_inputs_fetch_one_page() -> None:
    fake = QuoteTransport()
    result = await quote_async(["AAPL", "aapl", " AAPL "], client=_client(fake))
    assert len(fake.calls) == 1
    assert len(result.data) == 1
    assert len(result.metadata.symbols) == 3


async def test_batch_concurrency_is_bounded_by_the_client() -> None:
    fake = QuoteTransport(delay=0.02)
    client = _client(fake, concurrency=2)
    result = await quote_async(["AAPL", "MSFT", "GOOG", "AMZN"], client=client)
    assert len(result.data) == 4
    assert fake.max_in_flight == 2


async def test_safety_limit_preflight_rejects_before_network() -> None:
    fake = QuoteTransport()
    client = _client(fake)
    with pytest.raises(FinvizQueryError):
        await quote_async(["AAPL", "MSFT", "GOOG"], client=client, max_symbols=2)
    assert fake.calls == []
    with pytest.raises(FinvizQueryError):
        await quote_async([f"S{i:03d}" for i in range(33)], client=client)
    assert fake.calls == []


async def test_route_drift_after_canonical_resolution_still_falls_back() -> None:
    # Once /stock is memoized for the client, a later not-found/parse drift on
    # that route must still probe the fallback (and update the memo), not leak
    # the drift to the caller.
    fake = QuoteTransport()
    client = _client(fake, cache_ttl=0.0)
    await quote_async("AAPL", client=client)  # canonical resolution memoized
    fake.path_overrides["/stock"] = _html_resp(_NOT_FOUND_PAGE)
    result = await quote_async("AAPL", client=client)
    assert _paths(fake) == ["/stock", "/stock", "/quote.ashx"]
    assert result.metadata.endpoint == "/quote.ashx"
    warm = await quote_async("AAPL", client=client)
    assert _paths(fake) == ["/stock", "/stock", "/quote.ashx", "/quote.ashx"]
    assert warm.metadata.endpoint == "/quote.ashx"


async def test_non_iterable_symbols_rejects_typed_before_network() -> None:
    fake = QuoteTransport()
    with pytest.raises(FinvizQueryError):
        await quote_async(123, client=_client(fake))  # type: ignore[arg-type]
    assert fake.calls == []


@pytest.mark.parametrize("bad_limit", [float("nan"), "1", True, 0, -1, 1.5])
async def test_invalid_max_symbols_preflight_rejects(bad_limit: Any) -> None:
    fake = QuoteTransport()
    with pytest.raises(FinvizQueryError):
        await quote_async(["AAPL", "MSFT", "GOOG"], client=_client(fake), max_symbols=bad_limit)
    assert fake.calls == []


# --- strict / partial / all-fail / cancellation ---------------------------------


async def test_strict_mode_raises_partial_error_with_succeeded_units() -> None:
    fake = QuoteTransport(errors={"ZZZZZ": 2})
    client = _client(fake)
    with pytest.raises(FinvizPartialError) as excinfo:
        await quote_async(["AAPL", "ZZZZZ"], client=client)
    partial = excinfo.value.partial_result
    assert isinstance(partial, FetchResult)
    assert len(partial.data) == 1
    assert partial.data[0].symbol == "AAPL"
    assert partial.metadata.status is ResultStatus.PARTIAL
    assert partial.metadata.succeeded_units == 1
    assert partial.metadata.failed_units == 1


async def test_allow_partial_returns_partial_result() -> None:
    fake = QuoteTransport(errors={"ZZZZZ": 2})
    result = await quote_async(["AAPL", "ZZZZZ"], client=_client(fake), allow_partial=True)
    assert result.metadata.status is ResultStatus.PARTIAL
    assert len(result.data) == 1
    assert result.metadata.unit_errors[0].symbol == "ZZZZZ"
    assert result.metadata.requested_units == 2


async def test_all_failed_batch_raises_even_with_allow_partial() -> None:
    fake = QuoteTransport(errors={"ZZZZZ": 2, "YYYYY": 2})
    client = _client(fake)
    with pytest.raises(FinvizNotFoundError):
        await quote_async(["ZZZZZ", "YYYYY"], client=client, allow_partial=True)


async def test_cancellation_propagates_immediately() -> None:
    fake = QuoteTransport(delay=0.3)
    client = _client(fake)
    task = asyncio.ensure_future(quote_async(["AAPL", "MSFT"], client=client))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_child_cancellation_not_swallowed_into_partial() -> None:
    class CancelMSFT(QuoteTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
            if str((config.params or {}).get("t")) == "MSFT":
                await asyncio.sleep(10)
                raise AssertionError("cancelled task must never complete")
            return await super().request(config, stream_callback=stream_callback)

    fake = CancelMSFT()
    client = _client(fake)
    task = asyncio.ensure_future(quote_async(["AAPL", "MSFT"], client=client, allow_partial=True))
    await asyncio.sleep(0.05)
    children = asyncio.all_tasks() - {task, asyncio.current_task()}
    msft = next(t for t in children if not t.done())
    msft.cancel()  # transport cancels the MSFT unit mid-flight
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.max_in_flight <= client._semaphore._value  # siblings not left running


# --- sync wrappers ---------------------------------------------------------------


def test_sync_wrapper_outside_loop() -> None:
    fake = QuoteTransport()
    result = quote("AAPL", client=_client(fake))
    assert result.data[0].symbol == "AAPL"
    assert len(fake.calls) == 1


async def test_sync_wrapper_rejects_active_loop() -> None:
    with pytest.raises(RuntimeError, match="running event loop"):
        quote("AAPL", client=_client(QuoteTransport()))
