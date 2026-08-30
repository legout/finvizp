"""Tests for global and publisher news metadata feeds (Card 0.3-C).

RED-first: every test fails until ``finvizp.news`` exists. Hermetic: the
transport double serves the scrubbed fixture pages; no live HTTP.

Live evidence (bounded one-request probes, 2026-08-30): ``/news.ashx`` answers
301 -> ``/news``; the global page carries one News table and one Blogs table
under ``div#news``; ``/news/<slug>`` serves one publisher table; unknown slugs
answer 404. Provider ranking is preserved verbatim; the client never fetches
article URLs and never enumerates the publisher sitemap.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import zoneinfo
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

# ``finvizp.quote`` owns the flat ``news`` ticker projection, so the feed
# module is imported explicitly (import_module is shadowing-proof).
news_api = import_module("finvizp.news")

from finvizp.client import FinvizClient  # noqa: E402
from finvizp.errors import FinvizNotFoundError, FinvizQueryError  # noqa: E402
from finvizp.results import ResultStatus  # noqa: E402

BASE = "https://finviz.com"
FIXTURES = Path(__file__).parent / "fixtures" / "news"
GLOBAL = (FIXTURES / "global.html").read_text("utf-8")
PUBLISHER = (FIXTURES / "publisher.html").read_text("utf-8")
NOT_FOUND = "<html><head><title>Page was not found</title></head><body></body></html>"

EASTERN = zoneinfo.ZoneInfo("America/New_York")


def _resp(body: str, url: str, *, status: int = 200) -> NormalizedResponse:
    return NormalizedResponse.from_backend(
        status_code=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content=body.encode(),
        url=url,
        is_json=False,
    )


class NewsTransport(Backend):
    """Route-aware page server: /news for the global feed, /news/<slug> pages."""

    def __init__(
        self,
        *,
        global_body: str = GLOBAL,
        publisher_bodies: dict[str, str] | None = None,
        missing_slugs: set[str] | None = None,
    ) -> None:
        self.global_body = global_body
        self.publisher_bodies = dict(publisher_bodies or {})
        self.missing_slugs = set(missing_slugs or set())
        self.urls: list[str] = []

    @property
    def name(self) -> str:
        return "news-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        url = str(config.url)
        self.urls.append(url)
        path = urlsplit(url).path
        if path in ("/news", "/news.ashx"):
            return _resp(self.global_body, url)
        slug = path.removeprefix("/news/")
        if slug in self.missing_slugs:
            return _resp(NOT_FOUND, url, status=404)
        body = self.publisher_bodies.get(slug)
        if body is None:
            return _resp(NOT_FOUND, url, status=404)
        return _resp(body, url)

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> NewsTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _client(fake: NewsTransport, **kwargs: Any) -> FinvizClient:
    kwargs.setdefault("retry_attempts", 0)
    kwargs.setdefault("retry_backoff", 0.0)
    return FinvizClient(transport=fake, **kwargs)


def _paths(fake: NewsTransport) -> list[str]:
    return [urlsplit(url).path for url in fake.urls]


# --- global feed: routes, categories, order, schema -----------------------------


async def test_global_makes_one_canonical_news_request() -> None:
    fake = NewsTransport()
    result = await news_api.global_news_async(client=_client(fake))
    assert _paths(fake) == ["/news"]
    assert result.metadata.endpoint == "/news"
    assert result.metadata.status is ResultStatus.COMPLETE
    assert result.metadata.cache_hit is False


async def test_global_tables_preserve_categories_and_provider_order() -> None:
    fake = NewsTransport()
    result = await news_api.global_news_async(client=_client(fake))
    tables = result.data
    assert tuple(tables) == ("news", "blogs")
    news_table = tables["news"]
    assert news_table.num_rows == 3
    # Provider ranking is preserved verbatim, News before Blogs.
    assert news_table.column("title").to_pylist() == [
        "Markets brace for a quiet week",
        "Sample headline number two",
        "Wire item from yesterday",
    ]
    assert news_table.column("publisher").to_pylist() == ["bloomberg", "wsj", "reuters"]
    assert tables["blogs"].column("publisher").to_pylist() == ["zero-hedge", "the-bear-cave"]


async def test_global_rows_carry_urls_and_fetched_at() -> None:
    fake = NewsTransport()
    tables = (await news_api.global_news_async(client=_client(fake))).data
    row = tables["news"].to_pylist()[0]
    assert (
        row["url"] == "https://www.bloomberg.com/news/articles/2026-08-30/markets-brace-quiet-week"
    )
    stamps = tables["news"].column("fetched_at").to_pylist()
    assert all(ts.tzinfo is not None for ts in stamps)


async def test_global_temporal_parsing_exact_anchored_date_only() -> None:
    # ``09:20AM`` anchors to the response's own date in New York; ``Aug-29``
    # stays a date-only display (no invented clock time); raw companions keep
    # the exact provider displays; statuses name the parse.
    fake = NewsTransport()
    tables = (await news_api.global_news_async(client=_client(fake))).data
    news_table = tables["news"]
    published = news_table.column("published_at").to_pylist()
    assert published[0] is not None
    local = published[0].astimezone(EASTERN)
    assert (local.hour, local.minute) == (9, 20)
    assert published[2] is None  # date-only: no invented instant
    assert news_table.column("published_at_raw").to_pylist() == ["09:20AM", "12:21AM", "Aug-29"]
    statuses = news_table.column("published_at_status").to_pylist()
    assert statuses[0] == "anchored"
    assert statuses[2] == "date_only"


async def test_global_relative_display_parses_against_fetched_at() -> None:
    fake = NewsTransport(
        publisher_bodies={"zacks": PUBLISHER},
    )
    tables = (await news_api.publisher_news_async("zacks", client=_client(fake))).data
    published = tables.column("published_at").to_pylist()
    local = published[0].astimezone(EASTERN)
    # Synthetic fixtures are stable; ``46 min`` must land near the fetch time.
    now = dt.datetime.now(EASTERN)
    assert abs((now - local).total_seconds() - 46 * 60) < 120


# --- publisher feed: validation, not-found, order --------------------------------


async def test_publisher_requests_explicit_slug_route() -> None:
    fake = NewsTransport(publisher_bodies={"zacks": PUBLISHER})
    await news_api.publisher_news_async("zacks", client=_client(fake))
    assert _paths(fake) == ["/news/zacks"]


async def test_publisher_slug_is_validated_before_network() -> None:
    fake = NewsTransport()
    for bad in ("", "../etc", "Zacks", "two/segments", "x" * 65):
        with pytest.raises(FinvizQueryError):
            await news_api.publisher_news_async(bad, client=_client(fake))
    assert fake.urls == []  # rejected before any request


async def test_publisher_unknown_slug_is_typed_not_found() -> None:
    fake = NewsTransport(missing_slugs={"nope"})
    with pytest.raises(FinvizNotFoundError):
        await news_api.publisher_news_async("nope", client=_client(fake))
    assert _paths(fake) == ["/news/nope"]  # exactly one explicit request


async def test_publisher_rows_preserve_order_and_provenance() -> None:
    fake = NewsTransport(publisher_bodies={"zacks": PUBLISHER})
    result = await news_api.publisher_news_async("zacks", client=_client(fake))
    table = result.data
    assert result.metadata.endpoint == "/news/zacks"
    assert table.num_rows == 3
    first = table.to_pylist()[0]
    assert first["title"] == "Fresh wire item"
    assert first["url"] == BASE + "/news/386695/fresh-wire-item"
    assert first["publisher"] == "zacks"
    assert table.column("title").to_pylist()[1:] == [
        "Value stocks for your watch list",
        "Second date-only item",
    ]


async def test_publisher_date_only_display_stays_date_only() -> None:
    fake = NewsTransport(publisher_bodies={"zacks": PUBLISHER})
    table = (await news_api.publisher_news_async("zacks", client=_client(fake))).data
    published = table.column("published_at").to_pylist()
    assert published[1] is None and published[2] is None
    assert table.column("published_at_raw").to_pylist() == ["46 min", "Aug-28", "Aug-28"]
    statuses = table.column("published_at_status").to_pylist()
    assert statuses[0] == "relative"
    assert statuses[1] == "date_only"


async def test_publisher_never_requests_related_ticker_badges() -> None:
    # The badge anchors (related tickers) are decorative drift; the feed is
    # metadata only: the transport must see exactly the one page request.
    fake = NewsTransport(publisher_bodies={"zacks": PUBLISHER})
    await news_api.publisher_news_async("zacks", client=_client(fake))
    assert len(fake.urls) == 1


# --- recognized empty, drift, and the shared client surface ----------------------


async def test_global_empty_categories_are_recognized_empty() -> None:
    fake = NewsTransport(
        global_body=(
            '<html><body><div id="news"><table class="news_time-table" width="100%">'
            '<tr><td><span class="news-calendar_heading">News</span></td>'
            '<td><span class="news-calendar_heading mb-0">Blogs</span></td></tr>'
            '<tr><td><table class="styled-table-new table-fixed"></table></td>'
            '<td><table class="styled-table-new table-fixed"></table></td></tr>'
            "</table></div></body></html>"
        )
    )
    result = await news_api.global_news_async(client=_client(fake))
    assert result.metadata.status is ResultStatus.EMPTY
    assert result.data["news"].num_rows == 0
    assert result.data["blogs"].num_rows == 0


async def test_global_missing_category_structure_is_parse_drift() -> None:
    from finvizp.errors import FinvizParseError

    fake = NewsTransport(global_body="<html><body><p>no news here</p></body></html>")
    with pytest.raises(FinvizParseError):
        await news_api.global_news_async(client=_client(fake))


async def test_global_cache_hit_replays_parsed_result() -> None:
    fake = NewsTransport(publisher_bodies={"zacks": PUBLISHER})
    client = _client(fake, cache_ttl=60.0)
    first = await news_api.global_news_async(client=client)
    second = await news_api.global_news_async(client=client)
    assert len(fake.urls) == 1
    assert second.metadata.cache_hit is True
    assert second.metadata.fetched_at == first.metadata.fetched_at
    third = await news_api.global_news_async(client=client, refresh=True)
    assert len(fake.urls) == 2
    assert third.metadata.cache_hit is False
    await client.close()


async def test_sync_wrappers_run_outside_loop() -> None:
    fake = NewsTransport(publisher_bodies={"zacks": PUBLISHER})
    client = _client(fake)

    def call_sync() -> Any:
        return (
            news_api.global_news(client=client),
            news_api.publisher_news("zacks", client=client),
        )

    global_result, publisher_result = await asyncio.to_thread(call_sync)
    assert global_result.data["news"].num_rows == 3
    assert publisher_result.data.num_rows == 3


async def test_sync_wrapper_rejects_active_loop() -> None:
    fake = NewsTransport()

    async def inside() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            news_api.global_news(client=_client(fake))

    await inside()
    assert fake.urls == []


# --- one-page smoke (opt-in, bounded) ---------------------------------------------


@pytest.mark.live_public
async def test_live_global_news_smoke() -> None:
    # Env-var opt-in on top of the marker: avoids a duplicate live request
    # when both the news suite and tests/live run in one invocation.
    import os

    if not os.environ.get("FINVIZP_LIVE_SMOKE"):
        pytest.skip("FINVIZP_LIVE_SMOKE not set")
    async with FinvizClient() as client:
        result = await news_api.global_news_async(client=client)
    tables = result.data
    assert result.metadata.status in (ResultStatus.COMPLETE, ResultStatus.EMPTY)
    assert tables["news"].num_rows >= 0
