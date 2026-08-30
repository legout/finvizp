"""Typed groups query models and the checked-in provider-code registry.

The registry data was verified against the live ``/groups`` surface on
2026-08-30 (source ledger + bounded probes): dimension codes (``g``),
sub-industry codes (``sg``, identical to the screener registry's Sector
options), view codes (``v``), order codes (``o``, ``-`` prefix descending),
and custom-column codes (``c``, harvested one code at a time from the
``v=152`` custom view).

:class:`GroupQuery` validates a fully typed query before any network I/O and
encodes it into groups URL parameters. The legacy ``finvizfinance`` defect —
treating the order mapping as an object with an ``order_dict`` attribute — is
structurally impossible here: orders are typed values, never a dict attribute.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from finvizp.errors import FinvizQueryError

__all__ = [
    "GroupColumn",
    "GroupDimension",
    "GroupOrder",
    "GroupQuery",
    "GroupRegistry",
    "GroupView",
    "groups_registry",
]

# Provider code grammar: lowercase letters/digits joined by ``-``/``_``; view
# codes are numeric; order codes may carry a leading ``-`` (descending).
_CODE_GRAMMAR = r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"
_NUMERIC_CODE_GRAMMAR = r"^[0-9]+$"

# Checked-in registry content: live-verified 2026-08-30 (bounded probes of
# /groups.ashx view/dimension/order selectors and the v=152 custom columns).
_OBSERVATION_DATE = "2026-08-30"
_DIMENSIONS: dict[str, str] = {
    "Sector": "sector",
    "Industry": "industry",
    "Country": "country",
    "Capitalization": "capitalization",
}
# Sub-industry pages (g=industry&sg=<sector>) reuse the screener registry's
# Sector option codes, verified identical on the live 2026-08-30 page.
_SUB_INDUSTRIES: dict[str, str] = {
    "Basic Materials": "basicmaterials",
    "Communication Services": "communicationservices",
    "Consumer Cyclical": "consumercyclical",
    "Consumer Defensive": "consumerdefensive",
    "Energy": "energy",
    "Financial": "financial",
    "Healthcare": "healthcare",
    "Industrials": "industrials",
    "Real Estate": "realestate",
    "Technology": "technology",
    "Utilities": "utilities",
}
_VIEWS: dict[str, str] = {
    "overview": "110",
    "valuation": "120",
    "performance": "140",
    "custom": "152",
    "spectrum": "310",
}
_ORDERS: dict[str, str] = {
    "Name": "name",
    "Market Capitalization": "marketcap",
    "Price/Earnings": "pe",
    "Forward Price/Earnings": "forwardpe",
    "PEG": "peg",
    "Price/Sales": "ps",
    "Price/Book": "pb",
    "Price/Cash": "pc",
    "Price/Free Cash Flow": "pfcf",
    "Enterprise Value": "enterprisevalue",
    "EV/EBITDA": "evebitda",
    "EV/Sales": "evsales",
    "Dividend Yield": "dividendyield",
    "EPS growth past 3 years": "eps3years",
    "EPS growth past 5 years": "eps5years",
    "EPS growth next 5 years": "estltgrowth",
    "Sales growth past 3 years": "sales3years",
    "Sales growth past 5 years": "sales5years",
    "Short Interest Share": "shortinterestshare",
    "Return on Assets": "roa",
    "Return on Equity": "roe",
    "Return on Invested Capital": "roi",
    "Current Ratio": "curratio",
    "Quick Ratio": "quickratio",
    "LT Debt/Equity": "ltdebteq",
    "Total Debt/Equity": "debteq",
    "Gross Margin": "grossmargin",
    "Operating Margin": "opermargin",
    "Net Profit Margin": "netmargin",
    "Analyst Recommendation": "recom",
    "Performance (Week)": "perf1w",
    "Performance (Month)": "perf4w",
    "Performance (Quarter)": "perf13w",
    "Performance (Half Year)": "perf26w",
    "Performance (Year)": "perf52w",
    "Performance (Year To Date)": "perfytd",
    "Average Volume (3 Month)": "averagevolume",
    "Relative Volume": "relativevolume",
    "Change %": "change",
    "Volume": "volume",
    "Number of Stocks": "count",
    "Employees": "employees",
}
# Custom-view column codes (c=), harvested one code per request from the live
# v=152 custom view on 2026-08-30; code 0 (No.) and 1 (Name) are implicit.
_COLUMNS: dict[str, str] = {
    "Market Cap": "2",
    "P/E": "3",
    "Fwd P/E": "4",
    "PEG": "5",
    "P/S": "6",
    "P/B": "7",
    "P/C": "8",
    "P/FCF": "9",
    "Dividend": "10",
    "EPS past 5Y": "11",
    "EPS next 5Y": "12",
    "Sales past 5Y": "13",
    "Float Short": "14",
    "Perf Week": "15",
    "Perf Month": "16",
    "Perf Quart": "17",
    "Perf Half": "18",
    "Perf Year": "19",
    "Perf YTD": "20",
    "Recom": "21",
    "Avg Volume": "22",
    "Rel Volume": "23",
    "Change %": "24",
    "Volume": "25",
    "Stocks": "26",
    "LTDebt/Eq": "27",
    "Debt/Eq": "28",
    "ROA": "29",
    "ROE": "30",
    "ROIC": "31",
}


@dataclass(frozen=True, slots=True)
class GroupRegistry:
    """Validated view of the checked-in groups provider-code registry."""

    version: int
    observation_date: str
    dimensions: dict[str, Any]
    sub_industries: dict[str, str]
    views: dict[str, str]
    orders: dict[str, str]
    columns: dict[str, str]


def _check_grammar(codes: dict[str, str], grammar: str, context: str) -> None:
    import re

    for _name, code in codes.items():
        if not re.match(grammar, code):
            msg = f"groups registry: {context} code {code!r} violates the code grammar"
            raise FinvizQueryError(msg)


@dataclass(frozen=True, slots=True)
class _DimensionSpec:
    name: str
    code: str


@dataclass(frozen=True, slots=True)
class GroupDimension:
    """One validated group dimension, optionally scoped to one sector."""

    name: str
    sub_industry: str | None = None

    SECTOR: ClassVar[GroupDimension]
    INDUSTRY: ClassVar[GroupDimension]
    COUNTRY: ClassVar[GroupDimension]
    CAPITALIZATION: ClassVar[GroupDimension]

    @property
    def spec(self) -> _DimensionSpec:
        registry = groups_registry()
        code = registry.dimensions[self.name]
        return _DimensionSpec(name=self.name, code=code)

    def __post_init__(self) -> None:
        registry = groups_registry()
        if self.name not in registry.dimensions:
            msg = f"unknown dimension {self.name!r}"
            raise FinvizQueryError(msg)
        if self.sub_industry is not None:
            if self.name != "Industry":
                msg = f"sub-industry requires the Industry dimension, got {self.name!r}"
                raise FinvizQueryError(msg)
            if self.sub_industry not in registry.sub_industries:
                msg = f"unknown sub-industry sector {self.sub_industry!r}"
                raise FinvizQueryError(msg)

    @property
    def code(self) -> str:
        return self.spec.code

    @property
    def sub_industry_code(self) -> str | None:
        if self.sub_industry is None:
            return None
        return groups_registry().sub_industries[self.sub_industry]


@dataclass(frozen=True, slots=True)
class GroupView:
    """One validated named group view."""

    name: str

    OVERVIEW: ClassVar[GroupView]
    VALUATION: ClassVar[GroupView]
    PERFORMANCE: ClassVar[GroupView]
    CUSTOM: ClassVar[GroupView]
    SPECTRUM: ClassVar[GroupView]

    @property
    def code(self) -> str:
        return groups_registry().views[self.name]

    def __post_init__(self) -> None:
        if self.name not in groups_registry().views:
            msg = f"unknown view {self.name!r}"
            raise FinvizQueryError(msg)


@dataclass(frozen=True, slots=True)
class GroupOrder:
    """One validated order with direction."""

    name: str
    descending: bool = False

    @property
    def code(self) -> str:
        return groups_registry().orders[self.name]

    def __post_init__(self) -> None:
        if self.name not in groups_registry().orders:
            msg = f"unknown order {self.name!r}"
            raise FinvizQueryError(msg)


@dataclass(frozen=True, slots=True)
class GroupColumn:
    """One validated custom-view column."""

    name: str

    @property
    def code(self) -> str:
        return groups_registry().columns[self.name]

    def __post_init__(self) -> None:
        if self.name not in groups_registry().columns:
            msg = f"unknown column {self.name!r}"
            raise FinvizQueryError(msg)


@dataclass(frozen=True, slots=True)
class GroupQuery:
    """An immutable, validated groups query.

    Values are validated against the checked-in registry at construction time,
    so a successfully constructed query always encodes cleanly and never needs
    the network to find out it is invalid.
    """

    dimension: GroupDimension = field(default_factory=lambda: GroupDimension("Sector"))
    view: GroupView = field(default_factory=lambda: GroupView("overview"))
    order: GroupOrder | None = field(default_factory=lambda: GroupOrder("Name"))
    descending: bool = False
    columns: tuple[GroupColumn, ...] | None = None

    def __post_init__(self) -> None:
        # Accept plain names and list input for ergonomics; normalize to the
        # canonical frozen typed shape.
        if isinstance(self.dimension, str):
            object.__setattr__(self, "dimension", GroupDimension(self.dimension))
        if isinstance(self.view, str):
            object.__setattr__(self, "view", GroupView(self.view))
        if isinstance(self.order, str):
            object.__setattr__(self, "order", GroupOrder(self.order, self.descending))
        if self.order is not None and self.descending and not self.order.descending:
            object.__setattr__(self, "order", GroupOrder(self.order.name, True))
        if self.columns is not None:
            normalized = tuple(
                column if isinstance(column, GroupColumn) else GroupColumn(column)
                for column in self.columns
            )
            names = [column.name for column in normalized]
            if len(set(names)) != len(names):
                msg = "duplicate column"
                raise FinvizQueryError(msg)
            object.__setattr__(self, "columns", tuple(normalized))
        # Compatibility constraints, all decided at construction time.
        if self.view.name == "custom" and self.columns is None:
            msg = "the custom view requires columns"
            raise FinvizQueryError(msg)
        if self.view.name != "custom" and self.columns is not None:
            msg = "columns are only valid with the custom view"
            raise FinvizQueryError(msg)

    # -- canonical serialization ------------------------------------------------

    def to_json(self) -> str:
        """Canonical JSON serialization, independent of input ordering."""
        payload: dict[str, Any] = {
            "dimension": self.dimension.name,
            "view": self.view.name,
        }
        if self.dimension.sub_industry is not None:
            payload["sub_industry"] = self.dimension.sub_industry
        if self.order is not None:
            payload["order"] = {"name": self.order.name, "descending": self.order.descending}
        if self.columns is not None:
            payload["columns"] = [column.name for column in self.columns]
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def hash(self) -> str:
        """Stable short hash of the canonical serialization."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()[:16]

    # -- provider encoding --------------------------------------------------------

    def provider_params(self) -> dict[str, str]:
        """Encode into Finviz groups URL parameters (no URL, no network)."""
        params = {
            "v": self.view.code,
            "g": self.dimension.code,
            # The live provider carries st=d1 on every groups navigation URL;
            # it is the daily representation stamp, not a caller choice.
            "st": "d1",
        }
        if (sg := self.dimension.sub_industry_code) is not None:
            params["sg"] = sg
        if self.order is not None:
            params["o"] = ("-" if self.order.descending else "") + self.order.code
        if self.columns is not None:
            # The provider always renders the No./Name columns first
            # (live-verified custom view); request codes follow them.
            codes = [column.code for column in self.columns]
            if "0" not in codes:
                codes.insert(0, "0")
            if "1" not in codes:
                codes.insert(1, "1")
            params["c"] = ",".join(codes)
        return params


def groups_registry() -> GroupRegistry:
    """The checked-in groups provider-code registry (validated once)."""
    _check_grammar(_DIMENSIONS, _CODE_GRAMMAR, "dimension")
    _check_grammar(_SUB_INDUSTRIES, _CODE_GRAMMAR, "sub-industry")
    _check_grammar(_VIEWS, _NUMERIC_CODE_GRAMMAR, "view")
    _check_grammar(_ORDERS, _CODE_GRAMMAR, "order")
    _check_grammar(_COLUMNS, _NUMERIC_CODE_GRAMMAR, "column")
    return GroupRegistry(
        version=1,
        observation_date=_OBSERVATION_DATE,
        dimensions=dict(_DIMENSIONS),
        sub_industries=dict(_SUB_INDUSTRIES),
        views=dict(_VIEWS),
        orders=dict(_ORDERS),
        columns=dict(_COLUMNS),
    )


# Named constants on the validated types (e.g. ``GroupDimension.SECTOR``) are
# installed here, after :func:`groups_registry` is defined; a bad registry entry
# fails validation at construction, so the constants double as a registry check.
GroupDimension.SECTOR = GroupDimension("Sector")  # type: ignore[misc]
GroupDimension.INDUSTRY = GroupDimension("Industry")  # type: ignore[misc]
GroupDimension.COUNTRY = GroupDimension("Country")  # type: ignore[misc]
GroupDimension.CAPITALIZATION = GroupDimension("Capitalization")  # type: ignore[misc]
GroupView.OVERVIEW = GroupView("overview")  # type: ignore[misc]
GroupView.VALUATION = GroupView("valuation")  # type: ignore[misc]
GroupView.PERFORMANCE = GroupView("performance")  # type: ignore[misc]
GroupView.CUSTOM = GroupView("custom")  # type: ignore[misc]
GroupView.SPECTRUM = GroupView("spectrum")  # type: ignore[misc]
