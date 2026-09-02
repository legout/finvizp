"""Symbol universe manifest and ranked search: one-request reviewed reads.

``symbols_async()`` reads the single published stock manifest
(``/sitemap.xml?t=0&p=0``) — the one reviewed exception to the
no-sitemap-crawling rule — and returns the registered ``symbol_universe``
Arrow table. It never follows a listed URL or sibling sitemap.
``search_symbols_async()`` calls the bounded public JSON suggestions
endpoint and returns provider-ranked ``symbol_search`` rows; empty input is
rejected before any network and is never treated as a universe source.

Endpoint functions accept a reusable ``client``; if omitted, a transient
client is created, entered, and closed around the call — on success and
failure alike. The caller-owned client is used unchanged and never closed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from finvizp._parsers import symbols as symbols_parser
from finvizp._sync import run_sync
from finvizp.arrow import build_table
from finvizp.client import FinvizClient
from finvizp.errors import FinvizParseError, FinvizQueryError
from finvizp.results import AccessTier, FetchResult, ResultMetadata, ResultStatus

__all__ = ["search_symbols", "search_symbols_async", "symbols", "symbols_async"]

_MANIFEST_PATH = "/sitemap.xml"
_MANIFEST_QUERY = {"t": "0", "p": "0"}
_SEARCH_PATH = "/api/suggestions"
_MAX_QUERY_LENGTH = 64  # bounded input: suggestions are lookup, not enumeration
_PARSER_VERSION = "1"
_SCHEMA_VERSION = 1


def _validate_search_input(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        msg = "search_symbols: query must be a nonblank string"
        raise FinvizQueryError(msg)
    if len(query) > _MAX_QUERY_LENGTH:
        msg = f"search_symbols: query must be at most {_MAX_QUERY_LENGTH} characters"
        raise FinvizQueryError(msg)
    return query


def _envelope(
    *,
    path: str,
    table: Any,
    query: dict[str, Any],
    fetched_at: Any,
    response_hash: str,
    warnings: list[Any],
) -> FetchResult[Any]:
    status = ResultStatus.EMPTY if table.num_rows == 0 else ResultStatus.COMPLETE
    units = 0 if status is ResultStatus.EMPTY else 1
    return FetchResult(
        table,
        ResultMetadata(
            endpoint=path,
            status=status,
            access_tier=AccessTier.PUBLIC,
            fetched_at=fetched_at,
            query=query,
            warnings=tuple(warnings),
            response_hash=response_hash,
            requested_units=units,
            succeeded_units=units,
        ),
    )


@asynccontextmanager
async def _client_or_transient(client: FinvizClient | None) -> AsyncIterator[FinvizClient]:
    """Yield a ready client; own the lifecycle only when we created it.

    A caller-supplied client is entered by the caller and left open; a
    transient one is entered and closed here, on success and error alike.
    """
    if client is not None:
        await client._ensure_entered()
        yield client
        return
    async with FinvizClient() as transient:
        yield transient


def _parse_manifest(response: Any) -> FetchResult[Any]:
    rows, warnings = symbols_parser.parse_sitemap(response.data)
    if not rows and warnings:
        msg = "sitemap contained no canonical stock URLs"
        raise FinvizParseError(msg)
    table = build_table(
        "symbol_universe",
        ({"symbol": symbol} for symbol in rows),
        fetched_at=response.fetched_at,
    )
    return _envelope(
        path=response.endpoint,
        table=table,
        query=dict(response.query),
        fetched_at=response.fetched_at,
        response_hash=response.response_hash,
        warnings=warnings,
    )


def _parse_suggestions(response: Any, params: dict[str, Any]) -> FetchResult[Any]:
    rows = symbols_parser.parse_suggestions(response.data)
    builder_warnings: list[Any] = []
    table = build_table(
        "symbol_search",
        rows,
        fetched_at=response.fetched_at,
        on_warning=builder_warnings.append,
    )
    return _envelope(
        path=response.endpoint,
        table=table,
        query=params,
        fetched_at=response.fetched_at,
        response_hash=response.response_hash,
        warnings=builder_warnings,
    )


async def symbols_async(
    *,
    client: FinvizClient | None = None,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Read the published stock manifest once and return its symbol universe.

    Exactly one request to ``/sitemap.xml?t=0&p=0``; listed URLs and sibling
    sitemaps are never requested. Unexpected URL shapes warn and are skipped;
    a recognized empty manifest is an ``EMPTY`` result, not drift.
    ``cache=False`` bypasses the client cache for this call.
    """
    async with _client_or_transient(client) as op_client:
        return await op_client._endpoint_op(
            _MANIFEST_PATH,
            query=_MANIFEST_QUERY,
            cache=cache,
            refresh=refresh,
            representation="universe",
            parser_version=_PARSER_VERSION,
            schema_version=_SCHEMA_VERSION,
            # One-request contract: a redirect — even same-origin — is
            # transport drift, never a followed second request; a retryable
            # failure is never a retried second request either.
            follow_redirects=False,
            retry=False,
            parse=_parse_manifest,
        )


def symbols(
    *,
    client: FinvizClient | None = None,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`symbols_async` via the run-sync bridge."""
    return run_sync(symbols_async(client=client, cache=cache, refresh=refresh))


async def search_symbols_async(
    query: str,
    *,
    client: FinvizClient | None = None,
    with_indices: bool = False,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Ranked bounded suggestions for one nonblank query.

    Validates nonblank bounded input before any network access, encodes the
    query safely, makes exactly one ``/api/suggestions`` request, and
    preserves provider ranking. Never treats empty input as a universe
    source; a recognized empty result is an ``EMPTY`` result, not drift.
    ``cache=False`` bypasses the client cache for this call.
    """
    text = _validate_search_input(query)
    params: dict[str, Any] = {"input": text}
    if with_indices:
        params["withIndices"] = 1
    async with _client_or_transient(client) as op_client:
        return await op_client._endpoint_op(
            _SEARCH_PATH,
            query=params,
            cache=cache,
            refresh=refresh,
            representation="suggestions",
            parser_version=_PARSER_VERSION,
            schema_version=_SCHEMA_VERSION,
            parse=lambda response: _parse_suggestions(response, params),
        )


def search_symbols(
    query: str,
    *,
    client: FinvizClient | None = None,
    with_indices: bool = False,
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[Any]:
    """Sync wrapper for :func:`search_symbols_async` via the run-sync bridge."""
    return run_sync(
        search_symbols_async(
            query, client=client, with_indices=with_indices, cache=cache, refresh=refresh
        )
    )
