"""Global and publisher news metadata feeds (Card 0.3-C).

Two bounded, one-page public operations over the shared client seam:

- :func:`global_news_async` fetches ``/news`` (the canonical Time view; the
  legacy ``/news.ashx`` route answers 301 to it) once and returns one Arrow
  table per category the page itself names — ``news`` then ``blogs``,
  provider ranking preserved verbatim.
- :func:`publisher_news_async` fetches one explicitly caller-named publisher
  page (``/news/<slug>``) and returns its single ordered table.

Every row carries ``title``, ``url``, ``publisher``, the exact provider
temporal display (``published_at_raw``), the typed instant
(``published_at``), the parse verdict (``published_at_status``:
``anchored``/``relative``/``date_only``), and ``fetched_at``. Clock-time
displays anchor to the response's own date in New York (status
``anchored``); relative displays (``46 min``) resolve against the fetch
instant (status ``relative``); date-only displays (``Aug-29``) keep a null
typed instant (status ``date_only``) — no clock time is ever invented.

The feed is metadata only: article URLs are never requested, publisher pages
are never enumerated (the caller names the slug), and related-ticker badges
are decorative parsed text.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import pyarrow as pa

from finvizp._parsers.news import (
    ANCHORED,
    RELATIVE,
    NewsItem,
    parse_global_page,
    parse_publisher_page,
)
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import FinvizQueryError
from finvizp.results import FetchResult, ResultMetadata, ResultStatus
from finvizp.symbols import _client_or_transient

__all__ = [
    "GLOBAL_PATH",
    "global_news",
    "global_news_async",
    "publisher_news",
    "publisher_news_async",
]

GLOBAL_PATH = "/news"
BASE_URL = "https://finviz.com"
_PARSER_VERSION = "1"
_SCHEMA_VERSION = 1
_EASTERN = ZoneInfo("America/New_York")  # type: ignore[assignment]

# Single-segment lowercase slug: the provider sitemap's publisher pages are
# flat (``t=4`` children); anything else is rejected before any network I/O.
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SLUG_LENGTH = 64

_CLOCK = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?P<meridiem>[AP]M)$", re.I)
_RELATIVE = re.compile(r"(?P<count>\d+)\s+(?P<unit>min|hrs?|hours?|days?)$", re.I)

_TABLE_SCHEMA = pa.schema(
    [
        pa.field("title", pa.string(), nullable=False),
        pa.field("url", pa.string(), nullable=False),
        pa.field("publisher", pa.string()),
        pa.field("published_at", pa.timestamp("us", tz="UTC")),
        pa.field("published_at_raw", pa.string(), nullable=False),
        pa.field("published_at_status", pa.string(), nullable=False),
        pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)


def _validate_slug(slug: str) -> str:
    if (
        not isinstance(slug, str)
        or not slug
        or len(slug) > _MAX_SLUG_LENGTH
        or not _SLUG.fullmatch(slug)
    ):
        valid = "lowercase letters, digits, and single inner dashes"
        msg = f"publisher slug must be {valid} (max {_MAX_SLUG_LENGTH} chars), got {slug!r}"
        raise FinvizQueryError(msg)
    return slug


def _typed_when(
    item: NewsItem, *, fetched_at: dt.datetime, response_date: dt.date
) -> dt.datetime | None:
    """Resolve the parser's verdict into a typed UTC instant (or None)."""
    if item.when_status == ANCHORED:
        match = _CLOCK.search(item.when_raw)
        assert match is not None  # parser guarantees the display classifies
        hour = int(match["hour"]) % 12 + (12 if match["meridiem"].upper() == "PM" else 0)
        local = dt.datetime.combine(
            response_date, dt.time(hour, int(match["minute"])), tzinfo=_EASTERN
        )
        return local.astimezone(dt.UTC)
    if item.when_status == RELATIVE:
        match = _RELATIVE.search(item.when_raw)
        assert match is not None
        count = int(match["count"])
        unit = match["unit"].lower()
        if unit == "min":
            delta = dt.timedelta(minutes=count)
        elif unit.startswith("h"):  # hr/hrs/hour/hours
            delta = dt.timedelta(hours=count)
        else:
            delta = dt.timedelta(days=count)
        return fetched_at - delta
    return None  # date_only: no invented clock time


def _table(
    items: list[NewsItem], *, base_url: str, fetched_at: dt.datetime, response_date: dt.date
) -> Any:
    titles = [item.title for item in items]
    urls = [urljoin(base_url, item.url) for item in items]
    typed = [
        _typed_when(item, fetched_at=fetched_at, response_date=response_date) for item in items
    ]
    arrays = [
        pa.array(titles, type=_TABLE_SCHEMA.field("title").type),
        pa.array(urls, type=_TABLE_SCHEMA.field("url").type),
        pa.array([item.publisher for item in items], type=_TABLE_SCHEMA.field("publisher").type),
        pa.array(typed, type=_TABLE_SCHEMA.field("published_at").type),
        pa.array(
            [item.when_raw for item in items], type=_TABLE_SCHEMA.field("published_at_raw").type
        ),
        pa.array(
            [item.when_status for item in items],
            type=_TABLE_SCHEMA.field("published_at_status").type,
        ),
        pa.array([fetched_at] * len(items), type=_TABLE_SCHEMA.field("fetched_at").type),
    ]
    return pa.Table.from_arrays(arrays, schema=_TABLE_SCHEMA)


def _metadata(
    response: ClientResponse,
    *,
    status: ResultStatus,
) -> ResultMetadata:
    return ResultMetadata(
        endpoint=response.endpoint,
        status=status,
        access_tier=response.access_tier,
        fetched_at=response.fetched_at,
        query=dict(response.query),
        response_hash=response.response_hash,
        attempts=response.attempts,
        requested_units=0 if status is ResultStatus.EMPTY else 1,
        succeeded_units=0 if status is ResultStatus.EMPTY else 1,
        failed_units=0,
    )


def _parse_global(response: ClientResponse) -> FetchResult[Any]:
    """Reviewed endpoint parser: global page -> one Arrow table per category."""
    categories = parse_global_page(response.data)
    response_date = response.fetched_at.astimezone(_EASTERN).date()
    tables = {
        name: _table(
            items,
            base_url=BASE_URL,
            fetched_at=response.fetched_at,
            response_date=response_date,
        )
        for name, items in categories.items()
    }
    if all(table.num_rows == 0 for table in tables.values()):
        status = ResultStatus.EMPTY
    else:
        status = ResultStatus.COMPLETE
    return FetchResult(tables, _metadata(response, status=status))


def _parse_publisher_for(slug: str):
    def parse(response: ClientResponse) -> FetchResult[Any]:
        items = parse_publisher_page(response.data, slug=slug)
        response_date = response.fetched_at.astimezone(_EASTERN).date()
        table = _table(
            items,
            base_url=BASE_URL,
            fetched_at=response.fetched_at,
            response_date=response_date,
        )
        status = ResultStatus.EMPTY if table.num_rows == 0 else ResultStatus.COMPLETE
        return FetchResult(table, _metadata(response, status=status))

    return parse


async def global_news_async(
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Fetch the global news page once; one Arrow table per category table.

    Returns ``{"news": table, "blogs": table}`` (provider ranking preserved
    verbatim). A page whose category tables are all empty is a recognized
    ``EMPTY`` result; structural drift raises :class:`FinvizParseError`.
    ``cache=False`` bypasses the client cache; ``refresh=True`` fetches a
    fresh copy. Exactly one request is ever made — article URLs are never
    requested.
    """
    async with _client_or_transient(client) as op_client:
        return await op_client._endpoint_op(
            GLOBAL_PATH,
            cache=cache,
            refresh=refresh,
            representation="news_global",
            parser_version=_PARSER_VERSION,
            schema_version=_SCHEMA_VERSION,
            parse=_parse_global,
        )


def global_news(
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`global_news_async`; rejects an active event loop."""
    return run_sync(global_news_async(client=client, refresh=refresh, cache=cache))


async def publisher_news_async(
    slug: str,
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Fetch one explicit publisher page (``/news/<slug>``) into one table.

    ``slug`` is validated before any network I/O; an unknown slug surfaces as
    the client's typed :class:`FinvizNotFoundError` after exactly one
    request. Related-ticker badges are decorative text and never requested.
    """
    _validate_slug(slug)
    async with _client_or_transient(client) as op_client:
        return await op_client._endpoint_op(
            f"{GLOBAL_PATH}/{slug}",
            cache=cache,
            refresh=refresh,
            representation="news_publisher",
            parser_version=_PARSER_VERSION,
            schema_version=_SCHEMA_VERSION,
            parse=_parse_publisher_for(slug),
        )


def publisher_news(
    slug: str,
    *,
    client: FinvizClient,
    refresh: bool = False,
    cache: bool = True,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`publisher_news_async`; rejects an active loop."""
    return run_sync(publisher_news_async(slug, client=client, refresh=refresh, cache=cache))
