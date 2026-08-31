"""Capability manifest, curated public exports, and docs integrity (Card 0.1-I).

The manifest is the machine-checkable contract between the frozen public
surface inventory, the audited ``finvizfinance`` capability list, and the
actually importable finvizp operations. Every implemented entry must point at
a real public function; planned entries may not claim one.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

import finvizp
from finvizp import capabilities, capability

REPO_ROOT = Path(__file__).resolve().parents[1]

# The frozen inventory cutoff every observation dates back to.
INVENTORY_DATE = "2026-08-27"

REQUIRED_STATUSES = {"implemented", "planned"}
REQUIRED_ACCESS_TIERS = {"PUBLIC", "AUTHENTICATED", "ELITE", "UNKNOWN"}
REQUIRED_OUTPUT_KINDS = {"arrow_table", "bundle", "structured_data", "artifact", "ui_shell"}
REQUIRED_REPRESENTATIONS = {
    "xml_manifest",
    "json_suggestions",
    "json_statements",
    "html_stock_page",
    "html_tables",
    "embedded_json",
    "image",
    "spa_shell",
    "api_json",
}


# The page/screen/data method surface of audited ``finvizfinance`` 1.4.0
# (pinned at c8d461d): module path for class methods, bare name for the
# module-level table helpers, mapped to the manifest capability that replaces
# it — or None when the method is intentionally not ported. The
# ``finvizfinance.util`` transport/registry plumbing (``fetch``, ``web_scrap``,
# ``set_proxy``, ``get_filters``, …) has no per-method twin: explicit
# ``FinvizClient`` construction options and the checked-in registry replace it
# wholesale (see the migration guide's "Global mutable transport" difference).
LEGACY_METHOD_COVERAGE: dict[str, str | None] = {
    "Quote.get_current": "quote.bundle",
    "finvizfinance.ticker_charts": "charts.descriptor",
    "finvizfinance.ticker_fundament": "quote.snapshot",
    "finvizfinance.ticker_description": "quote.bundle",
    "finvizfinance.ticker_peer": "quote.peers",
    "finvizfinance.ticker_etf_holders": "quote.etf_holders",
    "finvizfinance.ticker_outer_ratings": "quote.ratings",
    "finvizfinance.ticker_news": "quote.news",
    "finvizfinance.ticker_inside_trader": "quote.insider",
    "finvizfinance.ticker_signal": "screener.signals",
    "finvizfinance.ticker_full_info": "quote.bundle",
    "Statements.get_statements": "statements.ia",
    "screener.Overview.screener_view": "screener.views",
    "screener.Valuation.screener_view": "screener.views",
    "screener.Financial.screener_view": "screener.views",
    "screener.Ownership.screener_view": "screener.views",
    "screener.Performance.screener_view": "screener.views",
    "screener.Technical.screener_view": "screener.views",
    "screener.Custom.screener_view": "screener.views",
    "screener.Ticker.screener_view": "screener.views",
    "screener.Base.set_filter": "screener.views",
    "screener.Base.reset": "screener.views",
    "screener.Base.compare": "screener.views",
    "group.Sector.screener_view": "groups.views",
    "group.Industry.screener_view": "groups.views",
    "group.Country.screener_view": "groups.views",
    "group.Capitalization.screener_view": "groups.views",
    "group.Custom.screener_view": "groups.views",
    "group.Spectrum.screener_view": "groups.views",
    "News.get_news": "news.global",
    "Insider.get_insider": "insider.global",
    "Earnings.partition_days": "earnings.screen",
    "Calendar.calendar": "calendar.economic",
    "Forex.performance": "forex.structured",
    "Forex.chart": "forex.structured",
    "Crypto.performance": "crypto.structured",
    "Crypto.chart": "crypto.structured",
    "Future.performance": "futures.tile",
    # Table-internals helpers shipped on the legacy public surface: never ported.
    "image_scrap_function": None,
    "find_table_by_headers": None,
}

# Manifest capabilities backed by a pure local computation: no network I/O, so
# the async/sync pairing rule does not apply.
PURE_LOCAL_CAPABILITIES = {"charts.descriptor", "groups.views"}

# The one legacy-named pair: the earnings module ships ``earnings_async`` next
# to the sync ``earnings_screen`` (the async name predates the 0.2 curated
# export and renaming it would break the public surface).
ASYNC_NAME_OVERRIDES = {"earnings.screen": ("earnings_screen", "earnings_async")}


@dataclass(frozen=True, slots=True)
class Capability:
    """One manifest entry, validated at load time."""

    id: str
    family: str
    operation: str | None
    replaced: str | None
    output_kind: str
    access_tier: str
    representation: str
    schema: tuple[str, ...]
    status: str
    fixture: str | None
    tests: str | None
    docs: str | None
    observation_date: str


def test_manifest_loads_with_unique_stable_ids() -> None:
    entries = capabilities()
    assert len(entries) > 0
    ids = [entry.id for entry in entries]
    assert len(ids) == len(set(ids)), "capability ids must be unique"
    for capability_id in ids:
        assert capability_id == capability_id.strip().lower()
        assert " " not in capability_id


def test_every_entry_has_complete_contract_fields() -> None:
    for entry in capabilities():
        assert entry.family, entry.id
        assert entry.status in REQUIRED_STATUSES, entry.id
        assert entry.access_tier in REQUIRED_ACCESS_TIERS, entry.id
        assert entry.output_kind in REQUIRED_OUTPUT_KINDS, entry.id
        assert entry.representation in REQUIRED_REPRESENTATIONS, entry.id
        assert entry.observation_date == INVENTORY_DATE, entry.id


def test_implemented_entries_point_at_importable_public_operations() -> None:
    exported = set(finvizp.__all__)
    for entry in capabilities():
        if entry.status != "implemented":
            continue
        assert entry.operation is not None, entry.id
        module_name, _, attribute = entry.operation.partition(":")
        module = importlib.import_module(module_name)
        function = getattr(module, attribute)
        assert callable(function), entry.id
        # The curated top-level surface must carry every implemented operation.
        assert attribute in exported or module_name in (
            "finvizp.forex",
            "finvizp.crypto",
        ), entry.id
        assert module_name.startswith("finvizp"), entry.id


def test_implemented_entries_reference_existing_files_and_registered_schemas() -> None:
    from finvizp.schemas import dataset_names

    registered = set(dataset_names())
    for entry in capabilities():
        if entry.status != "implemented":
            continue
        for schema_name in entry.schema:
            assert schema_name in registered, entry.id
        for relative in (entry.fixture, entry.tests, entry.docs):
            assert relative is not None, entry.id
            assert (REPO_ROOT / relative).exists(), f"{entry.id}: {relative}"


def test_planned_entries_claim_no_public_functions_or_files() -> None:
    planned = [entry for entry in capabilities() if entry.status == "planned"]
    assert planned, "later capabilities must be seeded as planned"
    for entry in planned:
        assert entry.operation is None, entry.id
        assert entry.fixture is None and entry.tests is None, entry.id
        # The deferral itself is always documented (matrix or migration guide).
        assert entry.docs is not None, entry.id
        assert (REPO_ROOT / entry.docs).exists(), entry.id


def test_frozen_inventory_families_are_all_represented() -> None:
    families = {entry.family for entry in capabilities()}
    # Every "Include" family of the frozen public-surface inventory must appear
    # (implemented or planned); deferred Elite-only families too.
    expected = {
        "symbol_universe",
        "symbol_search",
        "screener_views",
        "screener_export",
        "quote",
        "statements",
        "charts",
        "maps",
        "groups",
        "group_export",
        "news",
        "publisher_news",
        "insider",
        "fund_manager_insider",
        "earnings",
        "economic_calendar",
        "economic_details",
        "forex",
        "crypto",
        "futures",
        "etf_shell",
        "options",
        "portfolio",
        "elite_extras",
    }
    missing = expected - families
    assert not missing, f"families missing from the manifest: {sorted(missing)}"


def test_verified_0_2_operations_are_the_implemented_ones() -> None:
    implemented_ids = {entry.id for entry in capabilities() if entry.status == "implemented"}
    expected = {
        "symbols.universe",
        "symbols.search",
        "statements.ia",
        "statements.iq",
        "statements.ba",
        "statements.bq",
        "statements.ca",
        "statements.cq",
        "quote.bundle",
        "quote.snapshot",
        "quote.ratings",
        "quote.news",
        "quote.insider",
        "quote.peers",
        "quote.etf_holders",
        # 0.2: the merged screener surface (views+filters, signals, earnings).
        "screener.views",
        "screener.signals",
        "earnings.screen",
        # 0.3: groups, structured maps, news, insider feeds, calendar.
        "groups.views",
        "maps.structured",
        "news.global",
        "news.publisher",
        "insider.global",
        "insider.fund_manager",
        "calendar.economic",
        "calendar.details",
        # 0.4: chart/spectrum artifacts, forex, crypto, current futures tiles.
        "charts.descriptor",
        "forex.structured",
        "crypto.structured",
        "futures.tile",
    }
    assert implemented_ids == expected


def test_charts_capability_entry_links_the_artifact_surface() -> None:
    charts = capability("charts.descriptor")
    assert charts.operation == "finvizp.artifacts:chart_descriptor"
    assert charts.output_kind == "artifact"
    assert charts.representation == "image"
    assert charts.access_tier == "PUBLIC"
    assert charts.fixture == "tests/fixtures/artifacts/sample.png"
    assert charts.tests == "tests/test_artifacts.py"
    assert charts.docs == "docs/reference/markets-and-artifacts.md"

    from finvizp import artifacts as artifacts_module

    assert callable(artifacts_module.build_chart_url)
    assert callable(artifacts_module.build_spectrum_url)
    assert callable(artifacts_module.download_artifact)
    assert callable(artifacts_module.download_artifact_async)


def test_markets_capability_entries_link_the_merged_surface() -> None:
    forex = capability("forex.structured")
    assert forex.operation == "finvizp.forex:tiles"
    assert forex.output_kind == "structured_data"
    assert forex.representation == "embedded_json"
    assert forex.fixture == "tests/fixtures/markets/forex-tiles.html"
    assert forex.tests == "tests/test_forex.py"
    assert forex.docs == "docs/reference/markets-and-artifacts.md"

    crypto = capability("crypto.structured")
    assert crypto.operation == "finvizp.crypto:tiles"
    assert crypto.output_kind == "structured_data"
    assert crypto.representation == "embedded_json"
    assert crypto.fixture == "tests/fixtures/markets/crypto-tiles.html"
    assert crypto.tests == "tests/test_crypto.py"
    assert crypto.docs == "docs/reference/markets-and-artifacts.md"

    # The performance tables and chart galleries ride the same modules.
    from finvizp import crypto as crypto_module
    from finvizp import forex as forex_module

    assert callable(forex_module.performance_async)
    assert callable(forex_module.chart_async)
    assert callable(crypto_module.performance_async)
    assert callable(crypto_module.chart_async)

    futures = capability("futures.tile")
    assert futures.operation == "finvizp.futures:futures"
    assert futures.output_kind == "arrow_table"
    assert futures.representation == "embedded_json"
    # Current embedded tiles, never the legacy empty-table model.
    assert isinstance(futures.replaced, str)
    assert futures.replaced.startswith("finvizfinance futures performance")
    assert futures.schema == ("futures_tiles",)
    assert futures.fixture == "tests/fixtures/futures/current-tiles.html"
    assert futures.tests == "tests/test_futures.py"
    assert futures.docs == "docs/reference/markets-and-artifacts.md"


def test_0_4_markets_surface_exports_and_honest_temporal_semantics() -> None:
    # The curated top-level surface carries the artifact helpers; the market
    # families stay module-level (their names would clash at top level).
    names = set(finvizp.__all__)
    for name in ("Artifact", "chart_descriptor", "download_artifact", "download_artifact_async"):
        assert name in names, name

    from finvizp._parsers.markets import TileRow

    # No history is ever inferred from sparkline points.
    assert TileRow.__dataclass_fields__["sparkline_timestamps"].default is None
    assert TileRow.__dataclass_fields__["sparkline_interval_seconds"].default is None

    from finvizp.schemas import dataset_names

    assert "futures_tiles" in dataset_names()


def test_groups_capability_entries_link_the_merged_surface() -> None:
    views = capability("groups.views")
    assert views.operation == "finvizp.groups:group"
    assert views.access_tier == "PUBLIC"
    assert views.representation == "html_tables"
    assert views.docs == "docs/reference/groups-maps-events.md"
    assert views.fixture == "tests/fixtures/groups/overview.html"
    assert views.tests == "tests/test_groups.py"

    # The typed registry/collector modules are the capability surface; the
    # spectrum descriptor rides the same operation family via `spectrum`.
    from finvizp import groups as groups_module

    assert callable(groups_module.group_async)
    assert callable(groups_module.spectrum_async)

    # Anonymous Elite export stays planned: it is never a public representation.
    elite = capability("groups.export")
    assert elite.status == "planned"
    assert elite.access_tier == "ELITE"


def test_maps_capability_entry_is_structured_only() -> None:
    maps = capability("maps.structured")
    assert maps.operation == "finvizp.maps:map"
    assert maps.output_kind == "structured_data"
    assert maps.representation == "embedded_json"
    assert maps.fixture == "tests/fixtures/maps/sp500-embedded.html"
    assert maps.tests == "tests/test_maps.py"
    assert maps.docs == "docs/reference/groups-maps-events.md"
    # Structured data, never a renderer: no canvas/image output.
    assert maps.output_kind != "artifact"

    from finvizp.models import MapBundle

    assert callable(maps_module_operation(maps))
    assert MapBundle.__module__ == "finvizp.models"


def maps_module_operation(entry: Capability):
    import importlib

    module_name, _, attribute = entry.operation.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def test_news_capability_entries_link_the_merged_surface() -> None:
    global_news = capability("news.global")
    assert global_news.operation == "finvizp.news:global_news"
    assert global_news.representation == "html_tables"
    assert global_news.fixture == "tests/fixtures/news/global.html"
    assert global_news.tests == "tests/test_news.py"

    publisher = capability("news.publisher")
    assert publisher.operation == "finvizp.news:publisher_news"
    assert publisher.family == "publisher_news"
    # Explicit caller identifiers only: the publisher sitemap is never enumerated.
    assert publisher.fixture == "tests/fixtures/news/publisher.html"


def test_insider_capability_entries_link_the_merged_surface() -> None:
    insider_global = capability("insider.global")
    assert insider_global.operation == "finvizp.insider:global_insider"
    assert insider_global.representation == "html_tables"
    # Rows normalize into the registered quote_insider contract.
    assert insider_global.schema == ("quote_insider",)
    assert insider_global.fixture == "tests/fixtures/insider/global.html"
    assert insider_global.tests == "tests/test_insider.py"

    fund_manager = capability("insider.fund_manager")
    assert fund_manager.operation == "finvizp.insider:fund_insider"
    assert fund_manager.family == "fund_manager_insider"
    assert fund_manager.fixture == "tests/fixtures/insider/fund.html"


def test_calendar_capability_entries_link_the_merged_surface() -> None:
    calendar = capability("calendar.economic")
    assert calendar.operation == "finvizp.calendar:calendar"
    assert calendar.representation == "embedded_json"
    assert calendar.schema == ("economic_calendar",)
    assert calendar.fixture == "tests/fixtures/calendar/current-embedded.html"
    assert calendar.tests == "tests/test_calendar.py"

    details = capability("calendar.details")
    assert details.operation == "finvizp.calendar:calendar_detail"
    assert details.representation == "embedded_json"
    assert details.schema == ("economic_details",)
    assert details.fixture == "tests/fixtures/calendar/detail.html"


def test_0_3_datasets_are_registered_and_deterministic() -> None:
    from finvizp.schemas import arrow_schema, dataset, dataset_names

    names = set(dataset_names())
    assert {"economic_calendar", "economic_details"} <= names

    calendar = dataset("economic_calendar")
    assert calendar.field_names[:4] == ("symbol", "event", "category", "release_date")
    assert "release_timestamp_status" in calendar.field_names
    assert arrow_schema("economic_calendar") == arrow_schema("economic_calendar")

    details = dataset("economic_details")
    assert details.field_names[:4] == ("symbol", "event", "category", "release_date")
    assert arrow_schema("economic_details") == arrow_schema("economic_details")


def test_capability_lookup_roundtrip() -> None:
    entry = capability("quote.bundle")
    assert entry.family == "quote"
    with pytest.raises(LookupError):
        capability("does.not.exist")


def test_screener_capability_entries_link_the_merged_surface() -> None:
    views = capability("screener.views")
    assert views.operation == "finvizp.screener:screen"
    assert views.access_tier == "PUBLIC"
    assert views.representation == "html_tables"
    assert views.docs == "docs/reference/screener.md"

    signals = capability("screener.signals")
    assert signals.operation == "finvizp.screener:signal"
    assert signals.family == "screener_views"

    earnings = capability("earnings.screen")
    assert earnings.operation == "finvizp.earnings:earnings_screen"
    assert earnings.schema == ("earnings_screen",)

    # Anonymous Elite export stays planned: it is never a public representation.
    elite = capability("screener.export")
    assert elite.status == "planned"
    assert elite.access_tier == "ELITE"


def test_earnings_screen_dataset_is_registered_and_deterministic() -> None:
    from finvizp.schemas import arrow_schema, dataset, dataset_names

    assert "earnings_screen" in dataset_names()
    contract = dataset("earnings_screen")
    assert contract.field_names == (
        "rank",
        "symbol",
        "earnings_date",
        "earnings_date_raw",
        "earnings_session",
        "extra_fields",
        "fetched_at",
    )
    assert arrow_schema("earnings_screen") == arrow_schema("earnings_screen")


def test_provisional_defaults_match_the_real_client_signature() -> None:
    """Manifest defaults and the FinvizClient signature must never drift."""
    from finvizp.client import FinvizClient

    defaults = finvizp.provisional_defaults()
    signature = inspect.signature(FinvizClient.__init__)
    for name, expected in (
        ("concurrency", 6),
        ("retry_attempts", 2),
        ("retry_backoff", 1.0),
        ("timeout", None),
        ("cache_ttl", None),
        ("stale_if_error", False),
        ("cache_max_bytes", 8 * 1024 * 1024),
        ("cache_max_entries", 256),
    ):
        assert defaults[name] == expected, name
        assert signature.parameters[name].default == expected, name
    assert defaults["rate_limit"] is None
    assert defaults["max_quote_symbols"] == 32
    assert defaults["max_statement_batch_symbols"] == 50
    assert defaults["conservative"] is True
    assert isinstance(defaults["rationale"], str) and defaults["rationale"]


class TestCuratedExports:
    def test_all_exports_resolve(self) -> None:
        for name in finvizp.__all__:
            assert hasattr(finvizp, name), name

    def test_no_private_names_exported(self) -> None:
        assert not [
            name for name in finvizp.__all__ if name.startswith("_") and name != "__version__"
        ]

    def test_async_sync_pairs_are_both_exported(self) -> None:
        names = set(finvizp.__all__)
        async_ops = [name for name in names if name.endswith("_async")]
        assert {
            "symbols_async",
            "search_symbols_async",
            "statements_async",
            "statements_batch_async",
            "quote_async",
            "snapshot_async",
            "ratings_async",
            "news_async",
            "insider_async",
            "peers_async",
            "etf_holders_async",
            "screen_async",
            "signal_async",
            "earnings_async",
            # 0.3
            "group_async",
            "spectrum_async",
            "map_async",
            "global_news_async",
            "publisher_news_async",
            "global_insider_async",
            "fund_insider_async",
            "manager_insider_async",
            "calendar_async",
            "calendar_detail_async",
        } <= set(async_ops)
        for name in async_ops:
            assert name[: -len("_async")] in names, name

    def test_core_contract_types_are_exported(self) -> None:
        names = set(finvizp.__all__)
        for name in (
            "FinvizClient",
            "FetchResult",
            "ResultMetadata",
            "ResultStatus",
            "AccessTier",
            "QuoteBundle",
            "Artifact",
            "FetchWarning",
            "UnitError",
            "FinvizError",
            "FinvizPartialError",
            "capabilities",
            "capability",
            # 0.3 structured contracts
            "MapBundle",
            "MapConstituent",
        ):
            assert name in names, name

    def test_typed_error_hierarchy_is_fully_exported(self) -> None:
        from finvizp import errors

        exported = {name: getattr(finvizp, name) for name in finvizp.__all__}
        for attribute in dir(errors):
            if attribute.startswith("Finviz") and inspect.isclass(getattr(errors, attribute)):
                assert attribute in exported, attribute

    def test_manifest_files_are_packaged(self) -> None:
        from importlib import resources

        for filename in ("capabilities.json", "schema_registry.json"):
            text = resources.files("finvizp").joinpath(filename).read_text("utf-8")
            assert text.strip(), filename


class TestDocs:
    def test_reference_and_howto_pages_exist(self) -> None:
        for relative in (
            "docs/index.md",
            "README.md",
            "docs/reference/results.md",
            "docs/reference/schemas-0.1.md",
            "docs/reference/screener.md",
            "docs/reference/groups-maps-events.md",
            "docs/reference/markets-and-artifacts.md",
            "docs/how-to/proxies-and-cache.md",
        ):
            assert (REPO_ROOT / relative).exists(), relative

    def test_index_links_the_new_pages(self) -> None:
        text = (REPO_ROOT / "docs" / "index.md").read_text("utf-8")
        for fragment in (
            "reference/results.md",
            "reference/schemas-0.1.md",
            "reference/screener.md",
            "reference/groups-maps-events.md",
            "reference/markets-and-artifacts.md",
            "how-to/proxies-and-cache.md",
        ):
            assert fragment in text, fragment

    def test_readme_documents_the_0_1_surface(self) -> None:
        text = (REPO_ROOT / "README.md").read_text("utf-8")
        for fragment in (
            "symbols(",
            "search_symbols(",
            "statements(",
            "quote(",
            "capabilities()",
            "import asyncio",
        ):
            assert fragment in text, fragment


def test_every_legacy_method_maps_to_a_capability_or_explicit_skip() -> None:
    """The finvizfinance method audit is fully covered by the manifest.

    Every legacy method maps to a manifest capability that exists and is
    implemented; methods deliberately not ported must keep their explicit
    None entry here so the matrix can never silently drop one.
    """
    implemented = {e.id for e in capabilities() if e.status == "implemented"}
    for method, capability_id in LEGACY_METHOD_COVERAGE.items():
        if capability_id is None:
            continue
        assert capability_id in implemented, f"{method}: {capability_id}"


def test_legacy_export_helpers_are_documented_not_ported() -> None:
    """The legacy CSV/Excel export helpers have no finvizp twin.

    finvizfinance ships output_csv/output_excel on Earnings (and Excel export
    on screener views). finvizp rejects persistence helpers by design; the
    migration guide must document the Arrow-native caller workflow instead.
    """
    import finvizp

    assert not hasattr(finvizp, "output_csv")
    assert not hasattr(finvizp, "output_excel")
    assert not hasattr(finvizp, "to_csv")
    assert not hasattr(finvizp, "to_excel")

    guide = (REPO_ROOT / "docs" / "how-to" / "migrate-from-finvizfinance.md").read_text("utf-8")
    for fragment in ("to_csv", "to_excel"):
        assert fragment in guide, fragment


def test_capability_matrix_document_is_complete() -> None:
    """docs/reference/capability-matrix.md names every manifest entry."""
    text = (REPO_ROOT / "docs" / "reference" / "capability-matrix.md").read_text("utf-8")
    for entry in capabilities():
        assert entry.id in text, entry.id


def test_migration_guide_covers_every_replaced_capability() -> None:
    """docs/how-to/migrate-from-finvizfinance.md documents every replacement."""
    text = (REPO_ROOT / "docs" / "how-to" / "migrate-from-finvizfinance.md").read_text("utf-8")
    for entry in capabilities():
        if entry.status != "implemented" or entry.replaced is None:
            continue
        assert entry.id in text, entry.id
        operation = entry.operation
        assert operation is not None
        attribute = operation.partition(":")[2]
        assert attribute in text, entry.id


def test_implemented_networked_operations_have_async_and_sync_twins() -> None:
    """Every networked implemented operation ships the async-first pair."""
    for entry in capabilities():
        if entry.status != "implemented" or entry.id in PURE_LOCAL_CAPABILITIES:
            continue
        assert entry.operation is not None, entry.id
        module_name, _, attribute = entry.operation.partition(":")
        module = importlib.import_module(module_name)
        if entry.id in ASYNC_NAME_OVERRIDES:
            sync_name, async_name = ASYNC_NAME_OVERRIDES[entry.id]
        else:
            sync_name, async_name = attribute, f"{attribute.removesuffix('_async')}_async"
        assert callable(getattr(module, sync_name, None)), entry.id
        assert callable(getattr(module, async_name, None)), entry.id


def test_no_always_failing_public_stub_is_exported() -> None:
    """Planned/deferred capabilities are namespaced out of the public surface.

    A caller must never import something that always fails; every planned
    entry stays operation-less and nothing resembling a deferred family is
    exported from the curated top-level surface.
    """
    exported = {name for name in finvizp.__all__ if not name.startswith("_")}
    for entry in capabilities():
        if entry.status != "planned":
            continue
        assert entry.operation is None, entry.id
    for stub in (
        "screener_export",
        "group_export",
        "etf",
        "options",
        "portfolio",
        "alerts",
    ):
        assert stub not in exported, stub
