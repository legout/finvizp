"""Pure map parsers: public map page HTML + static hierarchy asset.

Transport-free per the foundation parser contract (direct lxml / stdlib json,
no network, client, or cache imports). Two representations, verified through
actual transport on 2026-08-30 (tests/fixtures/maps/representation-evidence.md):

- the public ``/map.ashx`` page embeds a first-party ``initialPerf`` JSON
  payload (``nodes`` ticker->performance, ``subtype``, ``version``, ``hash``)
  and preloads the hierarchy data asset itself via
  ``<link rel="preload" ... data-chunk-id="map_base_sec">``;
- the preloaded asset is a static webpack module whose export is a
  ``Root -> sectors -> industries -> leaves`` object literal with
  ``name``/``description``/``value`` leaves (unquoted JS keys).

No JavaScript is executed and the canvas renderer is not reproduced: these
parsers read the data documents the page already carries. Malformed/incomplete
embedded data is typed drift (:class:`FinvizParseError`); a page without the
data script or an export without children is the recognized empty state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from lxml import html as lxml_html

from finvizp.errors import FinvizParseError

__all__ = ["HierarchyNode", "MapPage", "parse_hierarchy_asset", "parse_map_page"]

_PERF_KEY = "initialPerf"
_HIERARCHY_CHUNK_ID = "map_base_sec"
# Delay provenance: the page footer's own statement, required on every page
# fetch (verified 2026-08-30: "Stock quotes delayed by 1 minute.").
_DELAY_DISPLAY = re.compile(r"Stock quotes delayed by (\d+) minute")
# Unquoted JS object keys in the data module (verified shape: name/children/
# description/value). String values are double-quoted JSON strings; the literal
# carries no comments/escapes beyond JSON's own, so quoting keys makes it JSON.
_BARE_KEY = re.compile(r"([{,])([A-Za-z_][A-Za-z0-9_]*):")


@dataclass(frozen=True, slots=True)
class MapPage:
    """One parsed map page: typed perf payload plus the hierarchy asset URL."""

    perf: dict[str, float]
    subtype: str | None
    version: int | None
    payload_hash: str | None
    hierarchy_url: str | None
    delay_minutes: float | None = None


@dataclass(frozen=True, slots=True)
class HierarchyNode:
    """One hierarchy node: sector, industry, or symbol leaf.

    ``perf`` is typed on every node but carries a value only on symbol leaves
    after the endpoint module joins the page's embedded perf payload (the
    parser itself is transport-free and leaves it ``None``).
    """

    name: str
    description: str | None = None
    value: float | None = None
    parent: HierarchyNode | None = None
    children: tuple[HierarchyNode, ...] = ()
    perf: float | None = None

    def __eq__(self, other: object) -> bool:
        # parent is excluded: it would recurse through the child->parent cycle.
        if not isinstance(other, HierarchyNode):
            return NotImplemented
        return (self.name, self.description, self.value, self.children, self.perf) == (
            other.name,
            other.description,
            other.value,
            other.children,
            other.perf,
        )

    def __hash__(self) -> int:
        return hash((self.name, self.description, self.value, self.perf))


def parse_map_page(html: str) -> MapPage:
    """Parse the public map page into its perf payload and hierarchy asset URL.

    The embedded ``initialPerf`` JSON is the performance contract; its absence
    (no data script at all) is the positively recognized empty page. A present
    payload always carries the page's own hierarchy preload link: perf without
    it is incomplete embedded data (the two-request contract cannot be
    satisfied and the URL is never constructed locally) — typed drift.
    """
    document = lxml_html.fromstring(html)
    hierarchy_url = _hierarchy_preload(document)
    payload = _embedded_perf(document)
    delay_match = _DELAY_DISPLAY.search(html)
    if delay_match is None:
        # The footer's delay statement is the page's own access/delay
        # provenance; a page without it is not the reviewed representation.
        msg = "map page carries no stock-quote delay statement (unrecognized representation)"
        raise FinvizParseError(msg, context={"endpoint": "/map.ashx"})
    delay_minutes = float(delay_match.group(1))
    if payload is None:
        # No canvas data at all: recognized empty page (e.g. data-asset variant
        # of the surface), not drift.
        return MapPage(
            perf={},
            subtype=None,
            version=None,
            payload_hash=None,
            hierarchy_url=hierarchy_url,
            delay_minutes=delay_minutes,
        )
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict):
        msg = "initialPerf nodes must be a JSON object"
        raise FinvizParseError(msg, context={"endpoint": "/map.ashx"})
    perf: dict[str, float] = {}
    for key, value in nodes.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            msg = "initialPerf nodes must map ticker strings to numbers"
            raise FinvizParseError(msg, context={"endpoint": "/map.ashx"})
        perf[key] = float(value)
    subtype = payload.get("subtype")
    version = payload.get("version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        msg = "initialPerf version must be an integer"
        raise FinvizParseError(msg, context={"endpoint": "/map.ashx"})
    payload_hash = payload.get("hash")
    if hierarchy_url is None:
        msg = (
            "map page embeds performance data but carries no map_base_sec "
            "hierarchy preload link (incomplete embedded data)"
        )
        raise FinvizParseError(msg, context={"endpoint": "/map.ashx"})
    return MapPage(
        perf=perf,
        subtype=subtype if isinstance(subtype, str) else None,
        version=version,
        payload_hash=payload_hash if isinstance(payload_hash, str) else None,
        hierarchy_url=hierarchy_url,
        delay_minutes=delay_minutes,
    )


def _hierarchy_preload(document: Any) -> str | None:
    """The page's own hierarchy asset URL from its preload link."""
    for element in document.xpath(
        "//link[@rel='preload' and @data-chunk-id=$_id]".replace("$_id", repr(_HIERARCHY_CHUNK_ID))
    ):
        href = element.get("href")
        if href:
            return href
    return None


def _embedded_perf(document: Any) -> dict[str, Any] | None:
    """Decode the inline ``initialPerf`` JSON payload, or ``None`` when absent."""
    for script in document.xpath("//script/text()"):
        if _PERF_KEY not in script:
            continue
        match = re.search(rf"{_PERF_KEY}\s*:\s*", script)
        if match is None:
            continue
        decoder = json.JSONDecoder()
        try:
            payload, _end = decoder.raw_decode(script[match.end() :].lstrip())
        except json.JSONDecodeError as exc:
            msg = f"malformed embedded initialPerf JSON: {exc.msg}"
            raise FinvizParseError(msg, context={"endpoint": "/map.ashx"}) from None
        if not isinstance(payload, dict):
            msg = "initialPerf must be a JSON object"
            raise FinvizParseError(msg, context={"endpoint": "/map.ashx"})
        return payload
    return None


def parse_hierarchy_asset(asset: str) -> HierarchyNode:
    """Parse the preloaded static hierarchy asset into a node tree.

    The asset is a webpack module assignment (``e.exports={name:"Root",...}``);
    its object literal is read directly (bare JS keys quoted, then JSON) — no
    JavaScript execution. Leaves must carry ticker ``name`` and numeric
    ``value``; anything else is incomplete provider data, typed drift.
    """
    match = re.search(r"e\.exports\s*=\s*", asset)
    if match is None:
        msg = "hierarchy asset carries no module export"
        raise FinvizParseError(msg, context={"endpoint": "map_base_sec"})
    literal = _object_literal(asset[match.end() :])
    if literal is None:
        msg = "hierarchy export is not an object literal"
        raise FinvizParseError(msg, context={"endpoint": "map_base_sec"})
    try:
        payload = json.loads(_BARE_KEY.sub(r'\1"\2":', literal))
    except json.JSONDecodeError as exc:
        msg = f"malformed hierarchy object literal: {exc.msg}"
        raise FinvizParseError(msg, context={"endpoint": "map_base_sec"}) from None
    return _node(payload, parent=None, path="Root")


def _object_literal(text: str) -> str | None:
    """The first balanced ``{...}`` literal, honoring string escapes."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _node(payload: dict[str, Any], *, parent: HierarchyNode | None, path: str) -> HierarchyNode:
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        msg = f"hierarchy node at {path} has no name"
        raise FinvizParseError(msg, context={"endpoint": "map_base_sec"})
    value = payload.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        msg = f"hierarchy leaf {name} has a non-numeric value"
        raise FinvizParseError(msg, context={"endpoint": "map_base_sec"})
    description = payload.get("description")
    children_payload = payload.get("children", [])
    if not isinstance(children_payload, list):
        msg = f"hierarchy node {name} children must be an array"
        raise FinvizParseError(msg, context={"endpoint": "map_base_sec"})
    # One node object per payload: children link to it by identity, so the
    # returned tree's parent pointers are the nodes themselves (frozen
    # dataclasses are filled in place, the models.QuoteBundle pattern).
    node = HierarchyNode(name=name)
    children = tuple(
        _node(child, parent=node, path=f"{path}/{name}")
        for child in children_payload
        if isinstance(child, dict) or _fail_child(child, name)
    )
    if not children and value is None and parent is not None:
        # A nested node with neither children nor value is incomplete provider
        # data; the childless root is the recognized empty export.
        msg = f"hierarchy leaf {name} carries no value"
        raise FinvizParseError(msg, context={"endpoint": "map_base_sec"})
    object.__setattr__(node, "description", description if isinstance(description, str) else None)
    object.__setattr__(node, "value", None if value is None else float(value))
    object.__setattr__(node, "parent", parent)
    object.__setattr__(node, "children", children)
    return node


def _fail_child(child: Any, name: str) -> bool:
    msg = f"hierarchy node {name} has a non-object child"
    raise FinvizParseError(msg, context={"endpoint": "map_base_sec"})
