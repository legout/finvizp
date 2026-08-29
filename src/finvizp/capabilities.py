"""Capability manifest: the audited public surface as a loadable contract.

The checked-in ``capabilities.json`` mirrors the frozen 2026-08-27 public
surface inventory and the ``finvizfinance`` capability audit; this module
validates it at import time so the manifest, the public exports, and the
schema registry can never silently drift apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import import_module, resources
from pathlib import Path
from typing import Any

from finvizp.errors import FinvizDataError

__all__ = ["Capability", "capabilities", "capability", "provisional_defaults"]

_INVENTORY_DATE = "2026-08-27"
_STATUSES = frozenset({"implemented", "planned"})
_ACCESS_TIERS = frozenset({"PUBLIC", "AUTHENTICATED", "ELITE", "UNKNOWN"})
_OUTPUT_KINDS = frozenset({"arrow_table", "bundle", "structured_data", "artifact", "ui_shell"})
_REPRESENTATIONS = frozenset(
    {
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
)


@dataclass(frozen=True, slots=True)
class Capability:
    """One manifest entry: an operation or a deferred later capability."""

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


def _manifest_root() -> Path:
    """Repository root for file references (tests run from the package)."""
    return Path(__file__).resolve().parents[2]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FinvizDataError(message)


def _parse_optional_text(entry_id: str, key: str, value: Any) -> str | None:
    if value is None:
        return None
    _require(
        isinstance(value, str) and value, f"{entry_id}: {key!r} must be a non-empty string or null"
    )
    return value


def _parse_entry(payload: Any) -> Capability:
    _require(isinstance(payload, dict), "capability entry must be an object")
    entry_id = payload.get("id")
    _require(
        isinstance(entry_id, str)
        and entry_id.strip() == entry_id
        and entry_id.lower() == entry_id
        and " " not in entry_id,
        f"capability id must be a lower-case dotted token, got {entry_id!r}",
    )

    def text(key: str) -> str:
        value = payload.get(key)
        _require(
            isinstance(value, str) and bool(value),
            f"{entry_id}: {key!r} must be a non-empty string",
        )
        return value

    status = text("status")
    _require(status in _STATUSES, f"{entry_id}: unknown status {status!r}")
    access_tier = text("access_tier")
    _require(access_tier in _ACCESS_TIERS, f"{entry_id}: unknown access_tier {access_tier!r}")
    output_kind = text("output_kind")
    _require(output_kind in _OUTPUT_KINDS, f"{entry_id}: unknown output_kind {output_kind!r}")
    representation = text("representation")
    _require(
        representation in _REPRESENTATIONS,
        f"{entry_id}: unknown representation {representation!r}",
    )
    schema_values = payload.get("schema")
    _require(
        isinstance(schema_values, list) and all(isinstance(name, str) for name in schema_values),
        f"{entry_id}: schema must be a list of dataset names",
    )
    operation = _parse_optional_text(entry_id, "operation", payload.get("operation"))
    fixture = _parse_optional_text(entry_id, "fixture", payload.get("fixture"))
    tests = _parse_optional_text(entry_id, "tests", payload.get("tests"))
    docs = _parse_optional_text(entry_id, "docs", payload.get("docs"))
    observation_date = text("observation_date")
    _require(
        observation_date == _INVENTORY_DATE,
        f"{entry_id}: observation_date must stay at the frozen inventory date",
    )
    if status == "implemented":
        _require(operation is not None, f"{entry_id}: implemented entries name an operation")
        _require(
            fixture is not None and tests is not None and docs is not None,
            f"{entry_id}: implemented entries reference fixture/tests/docs",
        )
    else:
        _require(
            operation is None and fixture is None and tests is None and docs is None,
            f"{entry_id}: planned entries claim no public surface",
        )
    return Capability(
        id=entry_id,
        family=text("family"),
        operation=operation,
        replaced=_parse_optional_text(entry_id, "replaced", payload.get("replaced")),
        output_kind=output_kind,
        access_tier=access_tier,
        representation=representation,
        schema=tuple(schema_values),
        status=status,
        fixture=fixture,
        tests=tests,
        docs=docs,
        observation_date=observation_date,
    )


def _load() -> tuple[Capability, ...]:
    text = resources.files("finvizp").joinpath("capabilities.json").read_text("utf-8")
    payload = json.loads(text)
    _require(
        isinstance(payload, dict) and isinstance(payload.get("capabilities"), list),
        "capabilities manifest must be an object with a capabilities list",
    )
    seen: set[str] = set()
    entries: list[Capability] = []
    for raw in payload["capabilities"]:
        entry = _parse_entry(raw)
        _require(entry.id not in seen, f"duplicate capability id {entry.id!r}")
        seen.add(entry.id)
        entries.append(entry)
    _require(bool(entries), "capabilities manifest is empty")
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class _OperationRef:
    module: str
    attribute: str


def _operation_ref(operation: str) -> _OperationRef:
    module_name, separator, attribute = operation.partition(":")
    _require(
        bool(separator) and bool(module_name) and bool(attribute),
        f"operation must be 'module:attribute', got {operation!r}",
    )
    return _OperationRef(module=module_name, attribute=attribute)


def capabilities() -> tuple[Capability, ...]:
    """Every manifest entry in manifest order; validated at first load."""
    return _load()


def capability(capability_id: str) -> Capability:
    """One manifest entry by stable id; raises :class:`LookupError` if absent."""
    for entry in _load():
        if entry.id == capability_id:
            return entry
    raise LookupError(f"unknown capability {capability_id!r}")


def provisional_defaults() -> dict[str, Any]:
    """Documented provisional transport/batching defaults for 0.1.

    Deliberately conservative values pending measured live evidence (Card
    0.1-I): no rate limit, no TTL caching, bounded retries and concurrency,
    and bounded quote/statement batch sizes. These mirror the ``FinvizClient``
    signature and endpoint safety limits; the manifest test fails if either
    side changes alone.
    """
    from finvizp.quote import DEFAULT_MAX_SYMBOLS
    from finvizp.statements import MAX_BATCH_SYMBOLS

    return {
        "conservative": True,
        "rationale": (
            "Provisional conservative defaults: caching disabled and no rate "
            "limit until measured, bounded retries/concurrency, and bounded "
            "batch sizes so ad-hoc use stays single-digit requests."
        ),
        "rate_limit": None,
        "concurrency": 6,
        "timeout": None,
        "retry_attempts": 2,
        "retry_backoff": 1.0,
        "cache_ttl": None,
        "stale_if_error": False,
        "cache_max_bytes": 8 * 1024 * 1024,
        "cache_max_entries": 256,
        "max_quote_symbols": DEFAULT_MAX_SYMBOLS,
        "max_statement_batch_symbols": MAX_BATCH_SYMBOLS,
    }


def _self_check() -> None:
    """Import-time integrity: implemented entries must be importable and real."""
    root = _manifest_root()
    for entry in _load():
        if entry.status != "implemented":
            continue
        assert entry.operation is not None  # guaranteed by _parse_entry
        ref = _operation_ref(entry.operation)
        try:
            module = import_module(ref.module)
        except ImportError as exc:
            msg = f"capability {entry.id!r} operation module is not importable: {exc}"
            raise FinvizDataError(msg) from exc
        if not callable(getattr(module, ref.attribute, None)):
            msg = f"capability {entry.id!r} operation {entry.operation!r} is not callable"
            raise FinvizDataError(msg)
        for relative in (entry.fixture, entry.tests, entry.docs):
            if relative is not None and not (root / relative).exists():
                msg = f"capability {entry.id!r} references missing file {relative!r}"
                raise FinvizDataError(msg)


_self_check()
