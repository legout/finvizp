"""Artifact grammar/origin safety, explicit bounded downloads (Card 0.4-A).

RED-first: every test fails until ``finvizp.artifacts`` exists and
``finvizp.models.Artifact`` grows the download-state fields. Hermetic: the
transport double serves fixture bytes; no live HTTP.

Verified live grammar (2026-08-30 bounded probes, recorded in the task card):
- stock charts: ``/chart.ashx?t=SYM&p=d`` (legacy) and ``/chart?t=SYM&p=d``
  (current) both 30x to ``https://charts2-node.finviz.com/chart?...``;
- group spectra: ``/grp_image?spectrum_<dim>.png`` — same-origin PNG.
``robots.txt`` disallows ``/chart`` and ``/image`` for automation, so the live
download smoke is opt-in (``live_public``), never part of default CI.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastreq.backends.base import Backend, NormalizedResponse

from finvizp.artifacts import (
    CHART_PATH,
    DOWNLOAD_LIMIT,
    build_chart_url,
    build_spectrum_url,
    chart_descriptor,
    download_artifact,
    download_artifact_async,
)
from finvizp.client import FinvizClient
from finvizp.errors import (
    FinvizEntitlementError,
    FinvizParseError,
    FinvizQueryError,
    FinvizTransportError,
)
from finvizp.models import Artifact

FIXTURES = Path(__file__).parent / "fixtures" / "artifacts"

# A minimal structurally valid PNG: 8-byte signature + IHDR chunk skeleton.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000050001"
    "0d0a2db4"
    "0000000049454e44ae426082"
)
(FIXTURES / "sample.png").write_bytes(PNG_BYTES)

PNG_HASH = hashlib.sha256(PNG_BYTES).hexdigest()
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
FINVIZ_URL = "https://finviz.com"

PNG_MEDIA = "image/png"


def _png_response(
    url: str,
    *,
    status: int = 200,
    content: bytes | None = None,
    location: str | None = None,
    declared_length: int | None = None,
) -> NormalizedResponse:
    headers = {"Content-Type": PNG_MEDIA if status == 200 else "text/html; charset=utf-8"}
    if location is not None:
        headers["Location"] = location
    if declared_length is not None:
        headers["Content-Length"] = str(declared_length)
    return NormalizedResponse.from_backend(
        status_code=status,
        headers=headers,
        content=PNG_BYTES if content is None else content,
        url=url,
    )


class ArtifactTransport(Backend):
    """Serves canned per-URL responses; records every request."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes  # str URL -> NormalizedResponse | Exception
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "artifacts-fake"

    async def request(self, config: Any, stream_callback: Any = None) -> NormalizedResponse:
        self.calls.append(config.url)
        route = self.routes.get(config.url)
        if route is None:
            raise AssertionError(f"unexpected request URL {config.url}")
        if isinstance(route, Exception):
            raise route
        return route

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> ArtifactTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def supports_http2(self) -> bool:
        return True


def _client(fake: ArtifactTransport, **kwargs: Any) -> FinvizClient:
    kwargs.setdefault("retry_attempts", 0)
    kwargs.setdefault("retry_backoff", 0.0)
    return FinvizClient(transport=fake, **kwargs)


def _descriptor(url: str, **kwargs: Any) -> Artifact:
    return Artifact(source_url=url, kind="image", media_type=PNG_MEDIA, fetched_at=NOW, **kwargs)


# --- parameter grammar and URL construction --------------------------------------


def test_chart_url_holds_timeframe_and_symbol_grammar() -> None:
    url = build_chart_url("aapl", timeframe="d")
    assert url == f"{FINVIZ_URL}{CHART_PATH}?t=AAPL&p=d"
    # legacy .ashx form stays available; both redirect to the chart node.
    assert build_chart_url("brk.b", timeframe="w", legacy=True).endswith("chart.ashx?t=BRK-B&p=w")


@pytest.mark.parametrize(
    ("symbol", "timeframe"),
    [
        ("../admin", "d"),  # path traversal
        ("AAPL;drop", "d"),  # injection punctuation
        ("AAPL", "decade"),  # unknown timeframe
        ("AAPL", "d;x"),  # timeframe injection
        ("", "d"),  # blank
        ("AAPL", ""),  # blank timeframe
    ],
)
def test_chart_url_rejects_grammar_violations(symbol: str, timeframe: str) -> None:
    with pytest.raises(FinvizQueryError):
        build_chart_url(symbol, timeframe=timeframe)


def test_spectrum_url_holds_dimension_grammar() -> None:
    assert build_spectrum_url("sector") == f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    assert (
        build_spectrum_url("Industry", rev="639237020102203790")
        == f"{FINVIZ_URL}/grp_image?spectrum_industry.png&rev=639237020102203790"
    )


@pytest.mark.parametrize("dimension", ["../etc", "Sector;x", "s p a c e", ""])
def test_spectrum_url_rejects_grammar_violations(dimension: str) -> None:
    with pytest.raises(FinvizQueryError):
        build_spectrum_url(dimension)


def test_descriptor_builder_fills_source_fields() -> None:
    descriptor = chart_descriptor("aapl", timeframe="d", fetched_at=NOW)
    assert isinstance(descriptor, Artifact)
    assert descriptor.symbol == "AAPL"
    assert descriptor.timeframe == "d"
    assert descriptor.kind == "chart"  # charts (not spectra) carry kind="chart"
    assert descriptor.content_hash is None  # descriptor-only: bytes never fetched
    url = build_chart_url("aapl", timeframe="d")
    assert descriptor.source_url == url


def test_artifact_download_state_defaults_are_immutable() -> None:
    descriptor = _descriptor("https://finviz.com/x.png")
    assert descriptor.content is None and descriptor.path is None
    with pytest.raises(FrozenInstanceError):
        descriptor.content = PNG_BYTES  # type: ignore[misc]


# --- descriptor-only discovery versus explicit bytes retrieval --------------------


def test_download_holds_bytes_hash_and_length_under_limit() -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    fake = ArtifactTransport({url: _png_response(url)})
    descriptor = download_artifact(_descriptor(url), client=_client(fake))
    assert fake.calls == [url]
    assert isinstance(descriptor, Artifact)
    assert descriptor.content == PNG_BYTES
    assert descriptor.content_hash == PNG_HASH
    assert descriptor.content_length == len(PNG_BYTES)
    assert descriptor.path is None


def test_descriptor_only_never_touches_transport() -> None:
    fake = ArtifactTransport({})
    descriptor = chart_descriptor("aapl", timeframe="d", fetched_at=NOW)
    assert fake.calls == []  # construction is pure: no request, no bytes
    assert descriptor.content is None


async def test_download_async_writes_bounded_path_atomically(tmp_path: Path) -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    fake = ArtifactTransport({url: _png_response(url)})
    target = tmp_path / "spectrum.png"
    descriptor = await download_artifact_async(_descriptor(url), client=_client(fake), path=target)
    assert descriptor.path == target
    assert target.read_bytes() == PNG_BYTES
    assert descriptor.content_hash == PNG_HASH


def test_download_rejects_too_large_body_before_classification(tmp_path: Path) -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    fake = ArtifactTransport({url: _png_response(url, content=b"x" * (DOWNLOAD_LIMIT + 1))})
    with pytest.raises(FinvizTransportError, match="limit"):
        download_artifact(_descriptor(url), client=_client(fake))
    assert not list(tmp_path.iterdir())  # nothing written


def test_download_default_limit_from_client_cache_budget() -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    budget = 4 * 1024
    fake = ArtifactTransport({url: _png_response(url, content=b"x" * (budget + 1))})
    client = _client(fake, cache_max_bytes=budget)
    with pytest.raises(FinvizTransportError, match="limit"):
        download_artifact(_descriptor(url), client=client)


# --- image versus challenge/wrong-media classification ----------------------------


def test_html_challenge_body_masquerading_as_image_is_drift() -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    page = b"<html><head><title>Just a moment...</title></head><body>challenge</body></html>"
    fake = ArtifactTransport(
        {
            url: NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=page,
                url=url,
            )
        }
    )
    with pytest.raises(FinvizParseError):
        download_artifact(_descriptor(url), client=_client(fake))


def test_image_media_type_mismatch_with_descriptor_is_drift() -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    fake = ArtifactTransport(
        {
            url: NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "image/jpeg"},
                content=b"\xff\xd8\xff\xe0fake-jpeg",
                url=url,
            )
        }
    )
    with pytest.raises(FinvizParseError, match="media"):
        download_artifact(_descriptor(url), client=_client(fake))


def test_magic_byte_sniffing_catches_lying_content_type(tmp_path: Path) -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    # image/* label + non-image bytes: only magic-byte sniffing catches it.
    fake = ArtifactTransport(
        {
            url: NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "image/png"},
                content=b"<html>definitely not an image</html>",
                url=url,
            )
        }
    )
    with pytest.raises(FinvizParseError, match="magic"):
        download_artifact(_descriptor(url), client=_client(fake))


def test_truncated_png_body_is_drift(tmp_path: Path) -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    fake = ArtifactTransport(
        {url: _png_response(url, content=PNG_BYTES[:20], declared_length=len(PNG_BYTES))}
    )
    with pytest.raises(FinvizParseError, match="truncated"):
        download_artifact(_descriptor(url), client=_client(fake))


def test_wrong_media_kind_response_is_drift() -> None:
    # JSON served where an image descriptor points: never fabricate an artifact.
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    fake = ArtifactTransport(
        {
            url: NormalizedResponse.from_backend(
                status_code=200,
                headers={"Content-Type": "application/json"},
                content=json.dumps({"error": "not an image"}).encode(),
                url=url,
            )
        }
    )
    with pytest.raises(FinvizParseError, match="image"):
        download_artifact(_descriptor(url), client=_client(fake))


# --- login/elite redirect classification -----------------------------------------


def test_elite_login_redirect_is_entitlement_error() -> None:
    url = f"{FINVIZ_URL}/chart?t=AAPL&p=d"
    fake = ArtifactTransport(
        {
            url: _png_response(
                "https://finviz.com/login.aspx",
                status=302,
                location="https://finviz.com/login.aspx",
            )
        }
    )
    with pytest.raises(FinvizEntitlementError):
        download_artifact(_descriptor(url), client=_client(fake))


async def test_cross_origin_redirect_is_transport_drift() -> None:
    url = f"{FINVIZ_URL}/chart?t=AAPL&p=d"
    hop = "https://charts2-node.finviz.com/chart?w=466&h=219&t=AAPL&tf=d"
    bounce = "https://evil.example.com/chart?w=466&h=219&t=AAPL"
    fake = ArtifactTransport(
        {
            url: _png_response(hop, status=302, location=hop),
            hop: _png_response(bounce, status=302, location=bounce),
        }
    )
    with pytest.raises(FinvizTransportError, match="origin"):
        await download_artifact_async(_descriptor(url), client=_client(fake))


async def test_same_provider_node_redirect_chain_downloads(tmp_path: Path) -> None:
    # Live-verified chain: /chart 30x -> charts2-node.finviz.com -> PNG bytes.
    url = f"{FINVIZ_URL}/chart?t=AAPL&p=d"
    hop = "https://charts2-node.finviz.com/chart?w=466&h=219&t=AAPL&tf=d"
    fake = ArtifactTransport(
        {url: _png_response(hop, status=302, location=hop), hop: _png_response(hop)}
    )
    descriptor = await download_artifact_async(_descriptor(url), client=_client(fake))
    assert descriptor.content == PNG_BYTES
    assert descriptor.content_hash == PNG_HASH
    assert fake.calls == [url, hop]


async def test_bounded_redirect_loop_surfaces_as_drift() -> None:
    url = f"{FINVIZ_URL}/chart?t=AAPL&p=d"
    hop = "https://charts2-node.finviz.com/chart?w=466&h=219&t=AAPL&tf=d"
    fake = ArtifactTransport(
        {
            url: _png_response(hop, status=302, location=hop),
            hop: _png_response(hop, status=301, location=hop),
        }
    )
    with pytest.raises(FinvizTransportError, match="redirect"):
        await download_artifact_async(_descriptor(url), client=_client(fake))


# --- cache and authenticated-body restrictions ------------------------------------


def test_downloads_never_touch_the_parsed_result_cache() -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    fake = ArtifactTransport({url: _png_response(url)})
    client = _client(fake, cache_ttl=3600.0)
    first = download_artifact(_descriptor(url), client=client)
    second = download_artifact(_descriptor(url), client=client)
    assert client.clear_cache() == 0  # nothing was ever stored
    assert len(fake.calls) == 2  # every download is an explicit fresh request
    assert first.content == second.content


def test_authenticated_body_is_never_cached_or_reused() -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    cookies = {"SCS": "caller-supplied-session"}
    fake = ArtifactTransport({url: _png_response(url)})
    client = _client(fake, auth_cookies=cookies, cache_ttl=3600.0)
    download_artifact(_descriptor(url), client=client)
    download_artifact(_descriptor(url), client=client)
    assert client.clear_cache() == 0
    assert len(fake.calls) == 2
    # cookies ride per request only (transport config), never persisted anywhere.


def test_download_artifact_rejects_non_descriptor_input() -> None:
    fake = ArtifactTransport({})
    with pytest.raises(FinvizQueryError):
        download_artifact("https://finviz.com/grp_image?spectrum_x.png", client=_client(fake))  # type: ignore[arg-type]
    with pytest.raises(FinvizQueryError):
        download_artifact(_descriptor("https://evil.example.com/x.png"), client=_client(fake))


def test_transport_failure_surfaces_typed() -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"
    fake = ArtifactTransport({url: _png_response(url, status=403)})
    with pytest.raises(Exception) as excinfo:
        download_artifact(_descriptor(url), client=_client(fake))
    assert not isinstance(excinfo.value, FinvizParseError)


def test_sync_and_async_agree() -> None:
    url = f"{FINVIZ_URL}/grp_image?spectrum_sector.png"

    class OneShot(ArtifactTransport):
        def __init__(self) -> None:
            super().__init__({url: _png_response(url)})

    a = download_artifact(_descriptor(url), client=_client(OneShot()))
    b = asyncio.run(download_artifact_async(_descriptor(url), client=_client(OneShot())))
    assert a.content_hash == b.content_hash == PNG_HASH


# --- bounded policy-compliant live smoke (opt-in; robots-gated routes) ------------


@pytest.mark.live_public
async def test_live_spectrum_download_bounded() -> None:
    from finvizp import FinvizError

    url = build_spectrum_url("sector")
    try:
        async with FinvizClient() as client:
            descriptor = await download_artifact_async(_descriptor(url), client=client)
    except FinvizError as exc:
        pytest.skip(f"live access unavailable (network/transport): {exc}")
    assert descriptor.content_hash
    assert descriptor.content_length
    assert descriptor.content and descriptor.content[:8] == b"\x89PNG\r\n\x1a\n"
