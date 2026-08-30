"""Finviz for Python: an async, Arrow-native Finviz client.

The curated public surface of the 0.1/0.2 milestones: typed results and
errors, the classified client, the symbols/statements/quote operations, and
the 0.2 screener (screens, signals, earnings screens). See
``finvizp.capabilities`` for the machine-readable manifest of what is
implemented versus planned.
"""

from __future__ import annotations

__version__ = "0.1.0"

from finvizp.arrow import build_table, dataset_field_names
from finvizp.cache import CacheEntry, ResultCache
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
from finvizp.models import Artifact, QuoteBundle
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
    "QuoteBundle",
    "ResultCache",
    "ResultMetadata",
    "ResultStatus",
    "UnitError",
    "__version__",
    # schemas / arrow
    "arrow_schema",
    "build_table",
    "capabilities",
    "capability",
    "classify_response",
    "dataset",
    "dataset_field_names",
    "dataset_names",
    "earnings",
    "earnings_async",
    "earnings_options",
    "earnings_screen",
    "etf_holders",
    "etf_holders_async",
    "insider",
    "insider_async",
    "news",
    "news_async",
    "peers",
    "peers_async",
    "provisional_defaults",
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
    # statements
    "statements",
    "statements_async",
    "statements_batch",
    "statements_batch_async",
    # symbols
    "symbols",
    "symbols_async",
]
