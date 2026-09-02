"""Artifact URL grammars and explicit bounded byte downloads (Card 0.4-A).

Descriptors are immutable :class:`finvizp.models.Artifact` values; raw bytes
are only ever retrieved through the explicit download helpers here — never as
a side effect of describing an artifact, and never cached (parsed-result
caching is for parsed immutable results, not bodies; authenticated bodies are
restricted the same way).

Safety contracts, live-verified 2026-08-30 (bounded probes, recorded in the
task card):

- chart URLs are built only from a canonical-symbol grammar (the
  ``_symbols`` allowlist) and the provider's timeframe letters; spectrum URLs
  only from the reviewed dimension codes. Arbitrary paths/origins are
  impossible by construction, and a descriptor's ``source_url`` must already
  be at the canonical origin or the same-provider chart node — other origins
  are rejected before any request.
- redirects are followed hop-by-hop only within the Finviz origins (canonical
  ``finviz.com`` plus the provider's own ``charts2-node.finviz.com`` chart
  renderer); an elite/login landing is a typed entitlement error, a
  cross-origin hop is transport drift.
- the response must be an image by media type and by magic bytes, within the
  requested media type family, and never larger than the bounded limit
  (descriptor content length when present, else the client's cache byte
  budget); truncation is detected against the declared Content-Length.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from fastreq.backends.base import NormalizedResponse, RequestConfig

from finvizp._symbols import SymbolInputError, _resolve
from finvizp.client import (
    _MAX_REDIRECTS,
    FinvizClient,
    _is_finviz_location,
)
from finvizp.errors import (
    FinvizEntitlementError,
    FinvizParseError,
    FinvizQueryError,
    FinvizTransportError,
)
from finvizp.models import Artifact

__all__ = [
    "CHART_NODE_ORIGIN",
    "CHART_PATH",
    "DOWNLOAD_LIMIT",
    "SPECTRUM_PATH",
    "TIMEFRAMES",
    "build_chart_url",
    "build_spectrum_url",
    "chart_descriptor",
    "download_artifact",
    "download_artifact_async",
]

BASE_URL = "https://finviz.com"
CHART_PATH = "/chart"
CHART_LEGACY_PATH = "/chart.ashx"
SPECTRUM_PATH = "/grp_image"
# Provider's own chart renderer (live-verified /chart redirect target).
CHART_NODE_ORIGIN = "https://charts2-node.finviz.com"

# Provider timeframe letters (legacy finvizfinance registry + live p= grammar).
TIMEFRAMES: dict[str, str] = {
    "1d": "d",
    "5d": "w",
    "1m": "m1",
    "3m": "m3",
    "6m": "m6",
    "1y": "y",
    "2y": "y2",
    "3y": "y3",
    "5y": "y5",
    "10y": "y10",
}
_TIMEFRAME_GRAMMAR = re.compile(r"^[a-z0-9]{1,3}$")

# Dimension codes are the reviewed groups registry codes (lowercase letters).
_DIMENSION_GRAMMAR = re.compile(r"^[a-z]+$")
_REV_GRAMMAR = re.compile(r"^[0-9]{1,32}$")

# Absolute byte ceiling; the effective limit is min with client/cache budget.
DOWNLOAD_LIMIT = 8 * 1024 * 1024

# PNG: full signature; JPEG: SOI + first marker byte; GIF: header; WEBP: RIFF.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)
_MAGIC_WEBP_RIFF = b"RIFF"
_MAGIC_WEBP_SIZE = 12

_ELITE_PATH = re.compile(r"(?:^|/)(?:login\.aspx|elite\.aspx)$")


def _media_family(media_type: str) -> str:
    return media_type.split("/", 1)[-1].split(";")[0].strip().lower()


def _is_finviz_hop(url: str) -> bool:
    """Same-origin or the provider's own chart-renderer node."""
    if _is_finviz_location(url):
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if parts.hostname == "charts2-node.finviz.com" and parts.port in (None, 443):
        return not _ELITE_PATH.search(parts.path or "")
    return False


def _is_elite_hop(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return bool(
        _ELITE_PATH.search(parts.path or "")
        or parts.hostname == "elite.finviz.com"
        or (parts.hostname or "").endswith(".elite.finviz.com")
    )


def _canonical_symbol(symbol: str) -> str:
    """Validate through the shared symbol grammar; return the canonical form."""
    try:
        (canonical,) = _resolve([symbol])[0]
    except (SymbolInputError, ValueError) as exc:
        msg = f"chart symbol violates the canonical grammar: {symbol!r}"
        raise FinvizQueryError(msg) from exc
    return canonical


def build_chart_url(symbol: str, *, timeframe: str = "d", legacy: bool = False) -> str:
    """Build one stock-chart URL from the reviewed parameter grammar.

    ``timeframe`` takes either a provider letter/code (``d``, ``w``, ``m1``)
    or a friendly key (``1d``, ``5d``); ``legacy=True`` emits the documented
    ``/chart.ashx`` form (both live-verified to reach the same renderer).
    """
    canonical = _canonical_symbol(symbol)
    resolved = TIMEFRAMES.get(timeframe, timeframe)
    if not _TIMEFRAME_GRAMMAR.fullmatch(resolved):
        msg = (
            f"chart timeframe must be one of {sorted(TIMEFRAMES)} or a provider "
            f"code (letters/digits), got {timeframe!r}"
        )
        raise FinvizQueryError(msg)
    path = CHART_LEGACY_PATH if legacy else CHART_PATH
    return f"{BASE_URL}{path}?t={canonical}&p={resolved}"


def build_spectrum_url(dimension: str, *, rev: str | None = None) -> str:
    """Build one group-spectrum image URL from the reviewed dimension grammar."""
    code = dimension.strip().lower() if isinstance(dimension, str) else ""
    if not _DIMENSION_GRAMMAR.fullmatch(code):
        msg = f"spectrum dimension must be a lowercase letters code, got {dimension!r}"
        raise FinvizQueryError(msg)
    url = f"{BASE_URL}{SPECTRUM_PATH}?spectrum_{code}.png"
    if rev is not None:
        if not _REV_GRAMMAR.fullmatch(rev):
            msg = f"spectrum rev must be a numeric provider revision, got {rev!r}"
            raise FinvizQueryError(msg)
        url = f"{url}&rev={rev}"
    return url


def chart_descriptor(
    symbol: str,
    *,
    timeframe: str = "d",
    fetched_at: Any,
) -> Artifact:
    """Describe one stock-chart artifact — pure construction, no bytes."""
    canonical = _canonical_symbol(symbol)
    resolved = TIMEFRAMES.get(timeframe, timeframe)
    if not _TIMEFRAME_GRAMMAR.fullmatch(resolved):
        msg = f"chart timeframe violates the provider grammar: {timeframe!r}"
        raise FinvizQueryError(msg)
    return Artifact(
        source_url=build_chart_url(canonical, timeframe=resolved),
        kind="chart",
        media_type="image/png",
        fetched_at=fetched_at,
        symbol=canonical,
        timeframe=resolved,
        chart_type="candle_stick",
    )


def _resolve_limit(client: FinvizClient, descriptor: Artifact) -> int:
    """Effective byte bound: absolute cap, tightened by the client's cache budget."""
    cache = getattr(client, "_cache", None)
    cache_max = getattr(cache, "_max_bytes", None)
    if not isinstance(cache_max, int) or cache_max <= 0:
        cache_max = DOWNLOAD_LIMIT
    return max(1, min(DOWNLOAD_LIMIT, cache_max))


async def _download_async(
    client: FinvizClient, descriptor: Artifact, *, path: Path | None
) -> Artifact:
    _validate_descriptor(descriptor)
    limit = _resolve_limit(client, descriptor)
    url = descriptor.source_url
    selected_proxy = await client._acquire_proxy(None)

    hop_url = url
    redirects = 0
    while True:
        response = await client._backend.request(
            RequestConfig(
                url=hop_url,
                headers=dict(client._headers()),
                cookies=dict(client._auth_cookies),
                timeout=client._timeout,
                proxy=selected_proxy,
                follow_redirects=False,
            )
        )
        # Server-set cookies never outlive one request (client contract).
        client._purge_backend_cookies(selected_proxy)
        if response.status_code not in (301, 302, 303, 307, 308):
            break
        location = response.headers.get("location", "")
        if not location:
            break  # non-200 provider status; classified below
        next_url = _urljoin(hop_url, location)
        if _is_elite_hop(next_url) or _is_elite_hop(str(response.url or "")):
            msg = "elite/login route reached during artifact download"
            raise FinvizEntitlementError(msg, context={"endpoint": "artifacts"})
        if not _is_finviz_hop(next_url):
            msg = "cross-origin redirect during artifact download"
            raise FinvizTransportError(msg, context={"endpoint": "artifacts"})
        if redirects >= _MAX_REDIRECTS:
            msg = "too many redirects during artifact download"
            raise FinvizTransportError(msg, context={"endpoint": "artifacts"})
        hop_url = next_url
        redirects += 1

    _classify_image_response(descriptor, response, limit=limit)
    content = response.content
    downloaded = replace(
        descriptor,
        content_hash=_sha256(content),
        content_length=len(content),
        content=content,
        path=None,
    )
    if path is not None:
        target = Path(path)
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(content)
        tmp.replace(target)
        return replace(downloaded, content=None, path=target)
    return downloaded


def download_artifact_async(
    descriptor: Artifact,
    *,
    client: FinvizClient,
    path: str | Path | None = None,
) -> Any:
    """Download one artifact descriptor's bytes explicitly and boundedly.

    Returns a derived immutable descriptor with ``content_hash``,
    ``content_length``, and either ``content`` (in-memory bytes) or ``path``
    (the file the bytes were atomically written to; ``content`` is then
    dropped). There is no descriptor-only side effect: constructing or
    describing an artifact never touches the network.
    """

    async def run() -> Any:
        return await _download_async(client, descriptor, path=None if path is None else Path(path))

    return run()


def download_artifact(
    descriptor: Artifact,
    *,
    client: FinvizClient,
    path: str | Path | None = None,
) -> Artifact:
    """Sync wrapper for :func:`download_artifact_async`; rejects an active loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        msg = (
            "download_artifact() cannot be called inside a running event loop; "
            "await download_artifact_async() directly instead"
        )
        raise RuntimeError(msg)
    target = None if path is None else Path(path)
    return asyncio.run(_download_async(client, descriptor, path=target))


def _validate_descriptor(descriptor: Artifact) -> None:
    if not isinstance(descriptor, Artifact):
        msg = f"download_artifact takes an Artifact descriptor, got {type(descriptor).__name__}"
        raise FinvizQueryError(msg)
    if not _is_finviz_hop(descriptor.source_url):
        msg = "descriptor source_url must be a Finviz artifact URL"
        raise FinvizQueryError(msg)


def _classify_image_response(
    descriptor: Artifact, response: NormalizedResponse, *, limit: int
) -> None:
    status = response.status_code
    if status == 403:
        raise FinvizTransportError("access blocked (403)", context={"endpoint": "artifacts"})
    if status == 429:
        raise FinvizTransportError("rate limited (429)", context={"endpoint": "artifacts"})
    if status == 404:
        raise FinvizParseError("artifact not found (404)", context={"endpoint": "artifacts"})
    if status != 200:
        raise FinvizTransportError(
            f"provider returned status {status}", context={"endpoint": "artifacts"}
        )

    content_type = response.headers.get("content-type", "")
    base = content_type.split(";")[0].strip().lower()
    if not base.startswith("image/"):
        # HTML challenge/login bodies served with image-adjacent status codes.
        msg = f"artifact response is not an image (content type {base or 'missing'})"
        raise FinvizParseError(msg, context={"endpoint": "artifacts"})
    descriptor_family = _media_family(descriptor.media_type)
    if descriptor_family and _media_family(base) != descriptor_family:
        # e.g. the descriptor promised PNG and the provider served JPEG.
        msg = f"artifact media type contradicts the descriptor ({base} != {descriptor.media_type})"
        raise FinvizParseError(msg, context={"endpoint": "artifacts"})

    content = response.content
    if len(content) > limit:
        msg = f"artifact exceeds the download limit of {limit} bytes"
        raise FinvizTransportError(msg, context={"endpoint": "artifacts"})

    if not content:
        raise FinvizParseError("artifact response is empty", context={"endpoint": "artifacts"})

    # Magic bytes must confirm a real image; challenge bodies can lie via
    # content-type alone.
    if not _sniff_image(content):
        msg = "artifact magic bytes do not match any image format"
        raise FinvizParseError(msg, context={"endpoint": "artifacts"})

    family = _media_family(base)
    if not _sniff_matches(content, family):
        msg = "artifact magic bytes contradict the media type"
        raise FinvizParseError(msg, context={"endpoint": "artifacts"})

    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) != len(content):
        msg = "artifact body is truncated"
        raise FinvizParseError(msg, context={"endpoint": "artifacts"})

    if len(content) < 8:
        msg = "artifact body is truncated"
        raise FinvizParseError(msg, context={"endpoint": "artifacts"})


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _sniff_image(content: bytes) -> bool:
    for signature, _ in _MAGIC:
        if content.startswith(signature):
            return True
    return (
        content[:4] == _MAGIC_WEBP_RIFF
        and len(content) >= _MAGIC_WEBP_SIZE
        and content[8:12] == b"WEBP"
    )


def _sniff_matches(content: bytes, family: str) -> bool:
    if family == "png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if family in ("jpeg", "jpg"):
        return content.startswith(b"\xff\xd8\xff")
    if family == "gif":
        return content[:6] in (b"GIF87a", b"GIF89a")
    if family == "webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return True  # unrecognized family: magic-byte positivity already held


def _urljoin(base: str, location: str) -> str:
    return urljoin(base, location)
