"""Finviz for Python: an async, Arrow-native Finviz client.

The curated public surface of the 0.1/0.2/0.3/0.4 milestones: typed results
and errors, the classified client, the symbols/statements/quote operations,
the 0.2 screener (screens, signals, earnings screens), the 0.3 groups,
structured maps, global/publisher news, insider feeds, and economic calendar,
and the 0.4 chart/spectrum artifacts with explicit bounded downloads plus the
module-level forex/crypto/futures market families. See
``finvizp.capabilities`` for the machine-readable manifest of what is
implemented versus planned.
"""

from __future__ import annotations

__version__ = "0.1.0"

from finvizp.arrow import build_table, dataset_field_names
from finvizp.artifacts import (
    chart_descriptor,
    download_artifact,
    download_artifact_async,
)
from finvizp.cache import CacheEntry, ResultCache
from finvizp.calendar import calendar, calendar_async, calendar_detail, calendar_detail_async
from finvizp.capabilities import (
    Capability,
    capabilities,
    capability,
    provisional_defaults,
)
from finvizp.client import ClientEvent, ClientResponse, FinvizClient, classify_response
from finvizp.earnings import earnings, earnings_async, earnings_options, earnings_screen
from finvizp.errors import (
    FetchWarning,
    FinvizBatchError,
    FinvizBlockedError,
    FinvizDataError,
    FinvizEntitlementError,
    FinvizError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizPartialError,
    FinvizQueryError,
    FinvizRateLimitError,
    FinvizTransportError,
    UnitError,
)
from finvizp.futures import futures, futures_async
from finvizp.groups import (
    GroupColumn,
    GroupDimension,
    GroupOrder,
    GroupQuery,
    GroupView,
    group,
    group_async,
    spectrum,
    spectrum_async,
)
from finvizp.insider import (
    INSIDER_FEEDS,
    fund_insider,
    fund_insider_async,
    global_insider,
    global_insider_async,
    manager_insider,
    manager_insider_async,
)
from finvizp.maps import map, map_async
from finvizp.models import Artifact, MapBundle, MapConstituent, QuoteBundle
from finvizp.news import (
    global_news,
    global_news_async,
    publisher_news,
    publisher_news_async,
)
from finvizp.quote import (
    etf_holders,
    etf_holders_async,
    insider,
    insider_async,
    news,
    news_async,
    peers,
    peers_async,
    quote,
    quote_async,
    ratings,
    ratings_async,
    snapshot,
    snapshot_async,
)
from finvizp.results import AccessTier, FetchResult, ResultMetadata, ResultStatus
from finvizp.schemas import arrow_schema, dataset, dataset_names
from finvizp.screener import (
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_ROWS,
    SCREEN_PATH,
    screen,
    screen_async,
    signal,
    signal_async,
)
from finvizp.statements import (
    STATEMENT_CODES,
    statements,
    statements_async,
    statements_batch,
    statements_batch_async,
)
from finvizp.symbols import search_symbols, search_symbols_async, symbols, symbols_async

__all__ = [
    "DEFAULT_MAX_PAGES",
    "DEFAULT_MAX_ROWS",
    "INSIDER_FEEDS",
    "SCREEN_PATH",
    "STATEMENT_CODES",
    # contracts
    "AccessTier",
    "Artifact",
    "CacheEntry",
    # capability manifest
    "Capability",
    "ClientEvent",
    "ClientResponse",
    "FetchResult",
    "FetchWarning",
    # errors
    "FinvizBatchError",
    "FinvizBlockedError",
    # client & cache
    "FinvizClient",
    "FinvizDataError",
    "FinvizEntitlementError",
    "FinvizError",
    "FinvizNotFoundError",
    "FinvizParseError",
    "FinvizPartialError",
    "FinvizQueryError",
    "FinvizRateLimitError",
    "FinvizTransportError",
    "GroupColumn",
    "GroupDimension",
    "GroupOrder",
    "GroupQuery",
    "GroupView",
    "MapBundle",
    "MapConstituent",
    "QuoteBundle",
    "ResultCache",
    "ResultMetadata",
    "ResultStatus",
    "UnitError",
    "__version__",
    # schemas / arrow
    "arrow_schema",
    "build_table",
    "calendar",
    "calendar_async",
    "calendar_detail",
    "calendar_detail_async",
    "capabilities",
    "capability",
    "chart_descriptor",
    "classify_response",
    "dataset",
    "dataset_field_names",
    "dataset_names",
    # explicit artifact downloads (0.4)
    "download_artifact",
    "download_artifact_async",
    "earnings",
    "earnings_async",
    "earnings_options",
    "earnings_screen",
    "etf_holders",
    "etf_holders_async",
    "fund_insider",
    "fund_insider_async",
    # current futures tiles (0.4)
    "futures",
    "futures_async",
    "global_insider",
    "global_insider_async",
    "global_news",
    "global_news_async",
    "group",
    "group_async",
    "insider",
    "insider_async",
    "manager_insider",
    "manager_insider_async",
    "map",
    "map_async",
    "news",
    "news_async",
    "peers",
    "peers_async",
    "provisional_defaults",
    "publisher_news",
    "publisher_news_async",
    # quote bundle + projections
    "quote",
    "quote_async",
    "ratings",
    "ratings_async",
    "screen",
    "screen_async",
    "search_symbols",
    "search_symbols_async",
    "signal",
    "signal_async",
    "snapshot",
    "snapshot_async",
    "spectrum",
    "spectrum_async",
    # statements
    "statements",
    "statements_async",
    "statements_batch",
    "statements_batch_async",
    # symbols
    "symbols",
    "symbols_async",
]
