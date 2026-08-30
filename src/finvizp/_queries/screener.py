"""Typed screener query models and the checked-in provider-code registry.

The checked-in ``screener_registry.json`` maps human-readable filter, signal,
order, view, and custom-column names to Finviz provider codes (audited from the
``finvizfinance`` constants at revision ``c8d461d`` and live observation; see
``docs/research/source-ledger.md``). :class:`ScreenerQuery` validates a fully
typed query before any network I/O, encodes it into screener URL parameters,
and serializes/hashes itself canonically.

The only raw-code surface is :meth:`ScreenerQuery.from_raw`, a reviewed escape
hatch that accepts provider codes from the checked-in registry; its errors
never echo the supplied values.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Protocol

from finvizp.errors import FinvizDataError, FinvizQueryError

__all__ = [
    "CustomColumns",
    "Filter",
    "FilterSpec",
    "NamedSpec",
    "OptionSpec",
    "Order",
    "ScreenerQuery",
    "ScreenerRegistry",
    "Signal",
    "View",
    "screener_registry",
]

# Provider codes are lowercase letters/digits joined by ``-`` or ``_``; option
# codes may also carry ``.`` (decimal thresholds, e.g. ``u0.5``); view and
# custom-column codes are numeric.
_CODE_GRAMMAR = re.compile(r"^[a-z0-9]+(?:[-_.][a-z0-9]+)*$")
_NUMERIC_CODE_GRAMMAR = re.compile(r"^[0-9]+$")
_TICKER_GRAMMAR = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_FILTER_TYPE_VOCABULARY = frozenset({"categorical", "numeric", "date"})
_DEFAULT_VIEW = "overview"


# --- registry record types ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptionSpec:
    """One filter option: human name plus provider code."""

    name: str
    code: str


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """One registered filter with its type and supported options."""

    name: str
    code: str
    type: str
    options: tuple[OptionSpec, ...]


@dataclass(frozen=True, slots=True)
class NamedSpec:
    """One registered named entry: signal, order, view, or custom column."""

    name: str
    code: str
    columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScreenerRegistry:
    """Validated view of the checked-in screener registry."""

    version: int
    observation_date: str
    page_size: int
    filters: dict[str, FilterSpec]
    signals: dict[str, NamedSpec]
    orders: dict[str, NamedSpec]
    views: dict[str, NamedSpec]
    columns: dict[str, NamedSpec]


def _registry_path() -> Any:
    return resources.files("finvizp").joinpath("screener_registry.json")


def _require_str(payload: dict[str, Any], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{context}: {key!r} must be a non-empty string, got {value!r}"
        raise FinvizDataError(msg)
    return value


def _parse_entries(
    payload: dict[str, Any],
    key: str,
    context: str,
) -> list[dict[str, Any]]:
    """Validate shared entry invariants; return raw entry dicts."""
    entries = payload.get(key)
    if not isinstance(entries, list):
        msg = f"{context}: {key!r} must be a list"
        raise FinvizDataError(msg)
    grammar = _NUMERIC_CODE_GRAMMAR if key in {"views", "columns"} else _CODE_GRAMMAR
    seen_names: set[str] = set()
    seen_codes: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            msg = f"{context}: every {key} entry must be an object"
            raise FinvizDataError(msg)
        name = _require_str(raw, "name", context)
        code = _require_str(raw, "code", context)
        if name in seen_names:
            msg = f"{context}: duplicate {key.removesuffix('s')} name {name!r}"
            raise FinvizDataError(msg)
        if code in seen_codes:
            msg = f"{context}: duplicate {key.removesuffix('s')} code {code!r}"
            raise FinvizDataError(msg)
        if not grammar.match(code):
            msg = f"{context}: {key.removesuffix('s')} code {code!r} violates the code grammar"
            raise FinvizDataError(msg)
        seen_names.add(name)
        seen_codes.add(code)
    return entries


def _parse_named(
    payload: dict[str, Any],
    key: str,
    context: str,
    *,
    with_columns: bool = False,
) -> dict[str, NamedSpec]:
    parsed: dict[str, NamedSpec] = {}
    for raw in _parse_entries(payload, key, context):
        entry = NamedSpec(
            name=raw["name"],
            code=raw["code"],
            columns=tuple(raw.get("columns", [])) if with_columns else (),
        )
        if with_columns and any(not isinstance(column, str) for column in entry.columns):
            msg = f"{context}: view {entry.name!r} columns must be a list of names"
            raise FinvizDataError(msg)
        parsed[entry.name] = entry
    return parsed


def _parse_registry(payload: Any) -> ScreenerRegistry:
    if not isinstance(payload, dict):
        msg = "screener registry must be an object"
        raise FinvizDataError(msg)
    context = "screener registry"
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        msg = f"{context}: version must be a positive integer, got {version!r}"
        raise FinvizDataError(msg)
    observation_date = _require_str(payload, "observation_date", context)
    page_size = payload.get("page_size")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        msg = f"{context}: page_size must be a positive integer, got {page_size!r}"
        raise FinvizDataError(msg)

    filters: dict[str, FilterSpec] = {}
    for raw in _parse_entries(payload, "filters", context):
        options_payload = raw.get("options")
        if not isinstance(options_payload, list) or not options_payload:
            msg = f"{context}: filter {raw['name']!r} needs a non-empty options list"
            raise FinvizDataError(msg)
        ftype = raw.get("type")
        if ftype not in _FILTER_TYPE_VOCABULARY:
            msg = f"{context}: filter {raw['name']!r} has unknown type {ftype!r}"
            raise FinvizDataError(msg)
        options: list[OptionSpec] = []
        option_names: set[str] = set()
        option_codes: set[str] = set()
        for raw_option in options_payload:
            if not isinstance(raw_option, dict):
                msg = f"{context}: filter {raw['name']!r} options must be objects"
                raise FinvizDataError(msg)
            option = OptionSpec(
                name=_require_str(raw_option, "name", context),
                code=_require_str(raw_option, "code", context),
            )
            if option.name in option_names or option.code in option_codes:
                msg = f"{context}: filter {raw['name']!r} has duplicate option {option.name!r}"
                raise FinvizDataError(msg)
            if not _CODE_GRAMMAR.match(option.code):
                msg = f"{context}: filter {raw['name']!r} option code violates the code grammar"
                raise FinvizDataError(msg)
            option_names.add(option.name)
            option_codes.add(option.code)
            options.append(option)
        filters[raw["name"]] = FilterSpec(
            name=raw["name"], code=raw["code"], type=ftype, options=tuple(options)
        )

    return ScreenerRegistry(
        version=version,
        observation_date=observation_date,
        page_size=page_size,
        filters=filters,
        signals=_parse_named(payload, "signals", context),
        orders=_parse_named(payload, "orders", context),
        views=_parse_named(payload, "views", context, with_columns=True),
        columns=_parse_named(payload, "columns", context),
    )


@lru_cache(maxsize=1)
def screener_registry() -> ScreenerRegistry:
    """Load and validate the checked-in screener provider-code registry."""
    return _parse_registry(json.loads(_registry_path().read_text("utf-8")))


# --- typed query values --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Filter:
    """One validated filter selection; ``option="Any"`` is a no-op."""

    name: str
    option: str

    @property
    def is_noop(self) -> bool:
        return self.option == "Any"

    @property
    def spec(self) -> FilterSpec:
        return screener_registry().filters[self.name]

    @property
    def option_code(self) -> str:
        if self.is_noop:
            msg = f"filter {self.name!r} option 'Any' has no provider code"
            raise FinvizQueryError(msg)
        for option in self.spec.options:
            if option.name == self.option:
                return option.code
        msg = f"unknown option {self.option!r} for filter {self.name!r}"
        raise FinvizQueryError(msg)

    def __post_init__(self) -> None:
        spec = screener_registry().filters.get(self.name)
        if spec is None:
            msg = f"unknown filter {self.name!r}"
            raise FinvizQueryError(msg)
        if not self.is_noop and all(option.name != self.option for option in spec.options):
            msg = f"unknown option {self.option!r} for filter {self.name!r}"
            raise FinvizQueryError(msg)


@dataclass(frozen=True, slots=True)
class Signal:
    """One validated named signal."""

    name: str

    @property
    def code(self) -> str:
        return screener_registry().signals[self.name].code

    def __post_init__(self) -> None:
        if self.name not in screener_registry().signals:
            msg = f"unknown signal {self.name!r}"
            raise FinvizQueryError(msg)


@dataclass(frozen=True, slots=True)
class Order:
    """One validated order with direction."""

    name: str
    descending: bool = False

    @property
    def code(self) -> str:
        return screener_registry().orders[self.name].code

    def __post_init__(self) -> None:
        if self.name not in screener_registry().orders:
            msg = f"unknown order {self.name!r}"
            raise FinvizQueryError(msg)


@dataclass(frozen=True, slots=True)
class View:
    """One validated named view."""

    name: str

    @property
    def code(self) -> str:
        return screener_registry().views[self.name].code

    @property
    def columns(self) -> tuple[str, ...]:
        return screener_registry().views[self.name].columns

    def __post_init__(self) -> None:
        if self.name not in screener_registry().views:
            msg = f"unknown view {self.name!r}"
            raise FinvizQueryError(msg)


@dataclass(frozen=True, slots=True)
class CustomColumns:
    """An ordered set of registered custom-view column names."""

    names: tuple[str, ...] = ()

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(screener_registry().columns[name].code for name in self.names)

    def __post_init__(self) -> None:
        if isinstance(self.names, str) or not isinstance(self.names, (list, tuple)):
            msg = "custom columns must be a sequence of column names"
            raise FinvizQueryError(msg)
        names = tuple(self.names)
        object.__setattr__(self, "names", names)
        columns = screener_registry().columns
        for name in names:
            if name not in columns:
                msg = f"unknown column {name!r}"
                raise FinvizQueryError(msg)
        if len(set(names)) != len(names):
            msg = "duplicate column"
            raise FinvizQueryError(msg)


class _NamedValue(Protocol):
    """A typed value built from one registered named entry."""

    @property
    def name(self) -> str: ...

    def __init__(self, name: str) -> None: ...


# --- query model ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScreenerQuery:
    """An immutable, validated screener query.

    Values are validated against the checked-in registry at construction time,
    so a successfully constructed query always encodes cleanly and never needs
    the network to find out it is invalid. ``filters`` is order-insensitive
    (sorted by filter name for canonical serialization); ``page`` is 1-based;
    ``max_rows`` is a client-side safety stop, not a provider parameter.
    """

    view: str = _DEFAULT_VIEW
    filters: tuple[Filter, ...] = ()
    signal: Signal | None = None
    order: Order | None = None
    columns: CustomColumns | None = None
    ticker: str | None = None
    page: int = 1
    max_rows: int | None = None

    def __post_init__(self) -> None:
        # Normalize list input into the canonical frozen tuple shape.
        filters = tuple(self.filters)
        seen: set[str] = set()
        for f in filters:
            if f.name in seen:
                msg = f"duplicate filter {f.name!r}"
                raise FinvizQueryError(msg)
            seen.add(f.name)
        object.__setattr__(self, "filters", tuple(f for f in filters if not f.is_noop))

        View(self.view)
        if self.signal is not None and self.view != "custom":
            msg = (
                "incompatible combination: signals require the custom view; "
                f"view {self.view!r} cannot carry a signal"
            )
            raise FinvizQueryError(msg)
        if self.view == "custom" and self.columns is None:
            msg = "the custom view requires custom columns"
            raise FinvizQueryError(msg)
        if self.columns is not None and self.view != "custom":
            msg = "custom columns are only valid with the custom view"
            raise FinvizQueryError(msg)
        if self.ticker is not None:
            ticker = self.ticker.upper()
            if not _TICKER_GRAMMAR.match(ticker):
                msg = "ticker must be 1-10 letters/digits with optional '.' or '-'"
                raise FinvizQueryError(msg)
            object.__setattr__(self, "ticker", ticker)
        if isinstance(self.page, bool) or not isinstance(self.page, int) or self.page < 1:
            msg = "page must be a positive integer"
            raise FinvizQueryError(msg)
        if self.max_rows is not None and (
            isinstance(self.max_rows, bool)
            or not isinstance(self.max_rows, int)
            or self.max_rows < 1
        ):
            msg = "max_rows must be a positive integer or None"
            raise FinvizQueryError(msg)

    # -- canonical serialization ------------------------------------------------

    def to_json(self) -> str:
        """Canonical JSON serialization, independent of input ordering."""
        payload: dict[str, Any] = {"view": self.view}
        if self.filters:
            payload["filters"] = [
                {"name": f.name, "option": f.option}
                for f in sorted(self.filters, key=lambda f: f.name)
            ]
        if self.signal is not None:
            payload["signal"] = self.signal.name
        if self.order is not None:
            payload["order"] = {"name": self.order.name, "descending": self.order.descending}
        if self.columns is not None:
            payload["columns"] = list(self.columns.names)
        if self.ticker is not None:
            payload["ticker"] = self.ticker
        if self.page != 1:
            payload["page"] = self.page
        if self.max_rows is not None:
            payload["max_rows"] = self.max_rows
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_json(cls, text: str) -> ScreenerQuery:
        """Rebuild a query from :meth:`to_json` output."""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = "invalid screener query JSON"
            raise FinvizQueryError(msg) from exc
        if not isinstance(payload, dict):
            msg = "screener query JSON must be an object"
            raise FinvizQueryError(msg)
        columns = payload.get("columns")
        order = payload.get("order")
        filters = payload.get("filters", ())
        return cls(
            view=payload.get("view", _DEFAULT_VIEW),
            filters=tuple(Filter(**entry) for entry in filters),
            signal=Signal(payload["signal"]) if payload.get("signal") else None,
            order=Order(**order) if order else None,
            columns=CustomColumns(names=tuple(columns)) if columns else None,
            ticker=payload.get("ticker"),
            page=payload.get("page", 1),
            max_rows=payload.get("max_rows"),
        )

    def hash(self) -> str:
        """Stable short hash of the canonical serialization."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()[:16]

    # -- provider encoding --------------------------------------------------------

    def provider_params(self) -> dict[str, str]:
        """Encode into Finviz screener URL parameters (no URL, no network)."""
        registry = screener_registry()
        params = {"v": registry.views[self.view].code}
        if self.filters:
            params["f"] = ",".join(f"{f.spec.code}_{f.option_code}" for f in self.filters)
        if self.signal is not None:
            params["s"] = self.signal.code
        if self.order is not None:
            params["o"] = ("-" if self.order.descending else "") + self.order.code
        if self.columns is not None:
            # The provider always renders the rank column first (audited custom.py).
            codes = list(self.columns.codes)
            if not codes or codes[0] != "0":
                codes.insert(0, "0")
            params["c"] = ",".join(codes)
        if self.ticker is not None:
            params["t"] = self.ticker
        if self.page != 1:
            params["r"] = str((self.page - 1) * registry.page_size + 1)
        return params

    # -- reviewed raw-code escape hatch ---------------------------------------------

    @classmethod
    def from_raw(
        cls,
        *,
        raw_filter: str | None = None,
        raw_signal: str | None = None,
        raw_order: str | None = None,
        view: str | None = None,
        columns: CustomColumns | tuple[str, ...] | list[str] | None = None,
        ticker: str | None = None,
        page: int = 1,
        max_rows: int | None = None,
    ) -> ScreenerQuery:
        """Build a query from reviewed provider codes.

        Only codes present in the checked-in registry are accepted; grammar
        violations and unknown codes are rejected without echoing the input.
        """
        registry = screener_registry()
        if view is None:
            view = "custom" if raw_signal is not None else _DEFAULT_VIEW
        View(view)

        filters = tuple(
            _resolve_raw_filter(registry, segment)
            for segment in (raw_filter.split(",") if raw_filter is not None else ())
        )

        signal = _resolve_raw_named(registry.signals, raw_signal, "signal", Signal)
        order = None
        if raw_order is not None:
            descending = raw_order.startswith("-")
            resolved = _resolve_raw_named(registry.orders, raw_order.lstrip("-"), "order", Order)
            order = Order(name=resolved.name, descending=descending)

        custom = None
        if columns is not None:
            custom = (
                columns if isinstance(columns, CustomColumns) else CustomColumns(tuple(columns))
            )
        return cls(
            view=view,
            filters=filters,
            signal=signal,
            order=order,
            columns=custom,
            ticker=ticker,
            page=page,
            max_rows=max_rows,
        )


def _reject(kind: str) -> FinvizQueryError:
    # Deliberately generic: never reflect the supplied raw value.
    return FinvizQueryError(
        f"raw {kind} code is not a reviewed registry code; expected lowercase "
        "provider codes from the checked-in screener registry"
    )


def _check_grammar(kind: str, value: str) -> None:
    if not _CODE_GRAMMAR.match(value):
        raise _reject(kind)


def _resolve_raw_filter(registry: ScreenerRegistry, segment: str) -> Filter:
    kind = "filter"
    _check_grammar(kind, segment)
    # Longest prefix first so ``fa_peg`` wins over ``fa_pe`` where both match.
    for spec in sorted(registry.filters.values(), key=lambda s: -len(s.code)):
        prefix = f"{spec.code}_"
        if segment.startswith(prefix):
            option_code = segment[len(prefix) :]
            for option in spec.options:
                if option.code == option_code:
                    return Filter(name=spec.name, option=option.name)
            break
    raise _reject(kind)


def _resolve_raw_named(
    namespace: dict[str, NamedSpec],
    raw: str | None,
    kind: str,
    factory: type[_NamedValue],
) -> Any:
    if raw is None:
        return None
    _check_grammar(kind, raw)
    for spec in namespace.values():
        if spec.code == raw:
            return factory(name=spec.name)
    raise _reject(kind)
