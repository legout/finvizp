"""Structured public map bundles: the two-request data contract, no renderer.

Endpoint module in the foundation architecture: the public ``/map.ashx`` page
embeds the ``initialPerf`` performance payload and preloads the hierarchy data
asset itself (``data-chunk-id="map_base_sec"``); this module fetches exactly
those two documents — the asset URL always taken from the page's own preload
link, never constructed locally — and joins them into one immutable
:class:`finvizp.models.MapBundle`. No JavaScript is executed and the canvas
renderer is not reproduced (decision register: maps return data, not a
renderer). Access is public and delayed; the page's own delay statement is
carried as provenance on every bundle.

Caching: each document is a cache unit under its own route (the page through
the reviewed endpoint-op seam, the asset verbatim from the page's preload
link), so a warm call serves two cache hits and performs zero requests. The
join is a deterministic pure function of the two cached documents, and the
cached page metadata carries the original fetch provenance verbatim. A
recognized empty page (no embedded data script, no preload link) is itself
the cached parse verdict: a typed EMPTY bundle, replayed without the second
request.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from finvizp._parsers.maps import HierarchyNode, parse_hierarchy_asset, parse_map_page
from finvizp._sync import run_sync
from finvizp.client import ClientResponse, FinvizClient
from finvizp.errors import FinvizQueryError
from finvizp.models import MapBundle, MapConstituent
from finvizp.results import FetchResult, ResultMetadata, ResultStatus

__all__ = [
    "MAP_PATH",
    "MapBundle",
    "map",
    "map_async",
]

MAP_PATH = "/map.ashx"
MAP_SYMBOL = "SP500"
SCHEMA_VERSION = 1
PARSER_VERSION = "1"


def _constituents(root: HierarchyNode) -> tuple[MapConstituent, ...]:
    """Flatten the hierarchy tree: one row per symbol leaf, hierarchy order."""
    rows: list[MapConstituent] = []
    for sector in root.children:
        for industry in sector.children:
            for leaf in industry.children:
                rows.append(
                    MapConstituent(
                        symbol=leaf.name,
                        sector=sector.name,
                        industry=industry.name,
                        description=leaf.description,
                        value=leaf.value if leaf.value is not None else 0.0,
                        perf=leaf.perf,
                    )
                )
    return tuple(rows)


def _join_perf(root: HierarchyNode, perf: dict[str, float]) -> tuple[str, ...]:
    """Attach perf to symbol leaves in place; return perf-only symbols.

    Perf symbols the hierarchy does not carry are the verified share-class
    drift (FOX/GOOG/NWS): reported as ``unmapped_perf``, never invented into
    a hierarchy placement.
    """
    mapped: set[str] = set()
    stack: list[HierarchyNode] = [root]
    while stack:
        node = stack.pop()
        if node.children:
            stack.extend(node.children)
        elif node.name in perf:
            object.__setattr__(node, "perf", perf[node.name])
            mapped.add(node.name)
    return tuple(sorted(set(perf) - mapped))


def _build_bundle(
    page_text: str,
    asset_text: str,
    hierarchy_url: str,
    *,
    fetched_at: Any,
    access_tier: Any,
) -> MapBundle:
    """Join page + asset into the immutable bundle (perf joined onto leaves)."""
    page = parse_map_page(page_text)
    root = parse_hierarchy_asset(asset_text)
    unmapped = _join_perf(root, page.perf)
    return MapBundle(
        symbol=MAP_SYMBOL,
        fetched_at=fetched_at,
        root=root,
        constituents=_constituents(root),
        perf=page.perf,
        unmapped_perf=unmapped,
        subtype=page.subtype,
        version=page.version,
        payload_hash=page.payload_hash,
        hierarchy_url=hierarchy_url,
        delay_minutes=page.delay_minutes,
        access_tier=access_tier,
    )


def _metadata(response: ClientResponse, status: ResultStatus) -> ResultMetadata:
    return ResultMetadata(
        endpoint=response.endpoint,
        status=status,
        access_tier=response.access_tier,
        fetched_at=response.fetched_at,
        served_at=response.served_at,
        query=dict(response.query),
        attempts=response.attempts,
        response_hash=response.response_hash,
        route_fingerprint=response.route_fingerprint,
        parser_version=PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        requested_units=0 if status is ResultStatus.EMPTY else 1,
        succeeded_units=0 if status is ResultStatus.EMPTY else 1,
    )


def _parse_page(response: ClientResponse) -> FetchResult[Any]:
    """Reviewed page parser: either the EMPTY verdict or the join carrier.

    The carrier is ``(hierarchy_url, page_text)``; its metadata keeps the
    original fetch provenance through the cache, so the joined bundle can be
    rebuilt identically on warm calls.
    """
    page = parse_map_page(response.data)
    if page.hierarchy_url is None:
        # Recognized empty page: no embedded perf and no asset to fetch —
        # positively typed EMPTY, cached as the page's own verdict.
        bundle = MapBundle(
            symbol=MAP_SYMBOL,
            fetched_at=response.fetched_at,
            perf={},
            hierarchy_url=None,
            delay_minutes=page.delay_minutes,
            access_tier=response.access_tier,
        )
        return FetchResult(bundle, _metadata(response, ResultStatus.EMPTY))
    return FetchResult(
        (page.hierarchy_url, response.data),
        _metadata(response, ResultStatus.COMPLETE),
    )


def _parse_asset(response: ClientResponse) -> FetchResult[str]:
    """Reviewed asset parser: decoded module text; drift is raised by the join."""
    return FetchResult(str(response.data), _metadata(response, ResultStatus.COMPLETE))


def _asset_op(client: FinvizClient, path: str, *, cache: bool = True, refresh: bool = False):
    """Bind the preloaded asset fetch under its own route facet."""
    from finvizp._parsers.maps import _HIERARCHY_CHUNK_ID

    return client._endpoint_op(
        path,
        query={},
        cache=cache,
        refresh=refresh,
        representation=_HIERARCHY_CHUNK_ID,
        parser_version=PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        parse=_parse_asset,
    )


async def map_async(
    *,
    client: FinvizClient,
    map: str = "sp500",
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[MapBundle]:
    """Fetch the public map into one structured :class:`MapBundle`.

    Exactly two requests on a cold call: ``/map.ashx`` (embedded perf
    payload + the page's own hierarchy preload link) and the preloaded asset
    route taken verbatim from that link — never constructed locally. Both
    documents cache under their own route, so a warm call replays the joined
    bundle with zero requests; ``cache=False`` bypasses and
    ``refresh=True`` replaces. A recognized empty page (no embedded data
    script) yields a :class:`ResultStatus.EMPTY` result whose bundle still
    carries the page's delay provenance.
    """
    if not isinstance(map, str) or map.lower() != "sp500":
        msg = f"map: only the public 'sp500' map is available, got {map!r}"
        raise FinvizQueryError(msg)
    page_result: FetchResult[Any] = await client._endpoint_op(
        MAP_PATH,
        query={},
        cache=cache,
        refresh=refresh,
        representation="bundle",
        parser_version=PARSER_VERSION,
        schema_version=SCHEMA_VERSION,
        parse=_parse_page,
    )()
    if not isinstance(page_result.data, tuple):
        return page_result  # the cached EMPTY verdict, replayed as-is
    hierarchy_url, page_text = page_result.data
    asset_result = await _asset_op(client, hierarchy_url, cache=cache, refresh=refresh)()
    bundle = _build_bundle(
        page_text,
        asset_result.data,
        hierarchy_url,
        fetched_at=page_result.metadata.fetched_at,
        access_tier=page_result.metadata.access_tier,
    )
    return FetchResult(
        bundle,
        replace(page_result.metadata, cache_hit=asset_result.metadata.cache_hit),
    )


def map(
    *,
    client: FinvizClient,
    map: str = "sp500",
    cache: bool = True,
    refresh: bool = False,
) -> FetchResult[MapBundle]:
    """Sync wrapper for :func:`map_async`; rejects an active event loop."""
    return run_sync(map_async(client=client, map=map, cache=cache, refresh=refresh))
