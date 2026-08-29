"""RED-first tests for cache-preserving quote projections (Card 0.1-H).

Projections must reuse the quote cache/bundle: at most one HTTP request for the
whole lifetime, original provenance preserved, ``projected_from="quote"`` set.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import pyarrow as pa
import pytest
from test_quote import _PAGE, QuoteTransport, _client, _html_resp

from finvizp.quote import (
    etf_holders,
    etf_holders_async,
    insider,
    insider_async,
    news,
    news_async,
    peers,
    peers_async,
    quote_async,
    ratings,
    ratings_async,
    snapshot,
    snapshot_async,
)
from finvizp.results import FetchResult

PROJECTED_REGIONS = [
    ("snapshot_async", snapshot_async, "quote_snapshot"),
    ("ratings_async", ratings_async, "quote_ratings"),
    ("news_async", news_async, "quote_news"),
    ("insider_async", insider_async, "quote_insider"),
    ("peers_async", peers_async, "quote_peers"),
    ("etf_holders_async", etf_holders_async, "quote_etf_holders"),
]

_SYNC_PROJECTIONS = [etf_holders, insider, news, peers, ratings, snapshot]


@pytest.fixture()
def cached_client() -> Any:
    fake = QuoteTransport()
    client = _client(fake, cache_ttl=60.0)
    return fake, client


@pytest.mark.parametrize("projection_name,projection,dataset", PROJECTED_REGIONS)
async def test_projection_reuses_cached_bundle_with_single_fetch(
    cached_client: Any, projection_name: str, projection: Any, dataset: str
) -> None:
    fake, client = cached_client
    bundle = await quote_async("AAPL", client=client)
    result = await projection("AAPL", client=client)
    assert len(fake.calls) == 1  # no second HTTP request
    assert isinstance(result, FetchResult)
    assert result.metadata.projected_from == "quote"
    assert result.metadata.response_hash == bundle.metadata.response_hash
    assert result.metadata.fetched_at == bundle.metadata.fetched_at
    assert result.data.schema.names == _registered_fields(dataset)


def _registered_fields(dataset: str) -> list[str]:
    from finvizp import arrow as fa

    return list(fa.dataset_field_names(dataset))


def _registered_schema(dataset: str) -> Any:
    from finvizp import schemas

    return schemas.arrow_schema(dataset)


async def test_projection_metadata_preserves_original_provenance(cached_client: Any) -> None:
    _fake, client = cached_client
    bundle = await quote_async("AAPL", client=client)
    table = await snapshot_async("AAPL", client=client)
    assert table.metadata.response_hash == bundle.metadata.response_hash
    assert table.metadata.fetched_at == bundle.metadata.fetched_at
    assert table.metadata.endpoint == bundle.metadata.endpoint
    assert table.metadata.attempts == bundle.metadata.attempts


async def test_cold_projection_fetches_the_stock_page_once(cached_client: Any) -> None:
    fake, client = cached_client
    table = await snapshot_async("AAPL", client=client)
    assert [urlsplit(str(c.url)).path for c in fake.calls] == ["/stock"]
    assert table.data.num_rows == 1


async def test_projection_after_warm_cache_is_still_a_hit(cached_client: Any) -> None:
    fake, client = cached_client
    first = await snapshot_async("AAPL", client=client)
    second = await news_async("AAPL", client=client)
    assert len(fake.calls) == 1
    assert first.metadata.cache_hit is False  # cold projection derives from the miss
    assert second.metadata.cache_hit is True
    assert second.metadata.projected_from == "quote"


async def test_multi_symbol_projection_concatenates_relations_in_first_symbol_order(
    cached_client: Any,
) -> None:
    _fake, client = cached_client
    result = await snapshot_async(["MSFT", "AAPL", "MSFT"], client=client)
    assert isinstance(result.data, pa.Table)  # one concatenated relation, not a tuple
    assert result.data.column("symbol").to_pylist() == ["MSFT", "AAPL"]
    ratings = await ratings_async(["MSFT", "AAPL"], client=client)
    assert isinstance(ratings.data, pa.Table)
    # Three ratings rows per symbol, concatenated in first-canonical symbol order.
    assert ratings.data.column("symbol").to_pylist() == ["MSFT"] * 3 + ["AAPL"] * 3


async def test_multi_symbol_projection_handles_absent_optional_relation() -> None:
    class DropMSFTRatings(QuoteTransport):
        async def request(self, config: Any, stream_callback: Any = None) -> Any:
            if str((config.params or {}).get("t")) == "MSFT":
                page = _PAGE.replace("js-table-ratings", "js-table-ratings-x", 1)
                return _html_resp(page.replace("AAPL", "MSFT"), url=str(config.url))
            return await super().request(config, stream_callback=stream_callback)

    client = _client(DropMSFTRatings(), cache_ttl=60.0)
    result = await ratings_async(["AAPL", "MSFT"], client=client)
    assert isinstance(result.data, pa.Table)
    # AAPL has its 3 rows; MSFT's absent relation contributes an empty typed table.
    assert result.data.column("symbol").to_pylist() == ["AAPL"] * 3
    assert result.data.schema == _registered_schema("quote_ratings")


# --- sync wrappers ---------------------------------------------------------------


def test_sync_projections_share_one_fetch() -> None:
    fake = QuoteTransport()
    client = _client(fake, cache_ttl=60.0)
    quote_snapshot = snapshot("AAPL", client=client)
    peers_result = peers("AAPL", client=client)
    etf_result = etf_holders("AAPL", client=client)
    news("AAPL", client=client)
    insider("AAPL", client=client)
    ratings_result = ratings("AAPL", client=client)
    assert len(fake.calls) == 1
    assert quote_snapshot.metadata.projected_from == "quote"
    assert peers_result.data.num_rows == 2
    assert etf_result.data.num_rows == 2
    assert ratings_result.data.num_rows == 3
    for projection in _SYNC_PROJECTIONS:
        assert projection.__doc__  # every public callable documents itself


async def test_sync_projection_rejects_active_loop(cached_client: Any) -> None:
    _fake, client = cached_client
    with pytest.raises(RuntimeError, match="running event loop"):
        snapshot("AAPL", client=client)
