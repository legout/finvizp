"""Bounded screener registry drift reports (developer tooling, 0.2-D).

Compares the checked-in ``screener_registry.json`` against fresh provider
metadata observations and renders a reviewable JSON report. Pure diff logic is
separated from the bounded live collector so the comparison is fully testable
without network access.

The observation surface is two reviewed public pages fetched once per report
(never crawled, never scheduled):

- the custom view (``v=151``) carries the complete order and signal dropdowns
  in static HTML;
- the all-filters layout (``v=111&ft=4``) carries every filter combo
  (``select#fs_<code>`` with its options) in static HTML.

Fixed-view column drift is covered by the page parser's header-driven contract
and is not part of this report. The tool never mutates the registry: applying
drift is a human decision, made by editing ``screener_registry.json`` in
review after reading the report. Reports contain only human names, provider
codes, and structural metadata — never cookies, proxy configuration, or raw
response bodies.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any
from urllib.parse import parse_qs

from lxml import html as lxml_html

from finvizp.errors import FinvizQueryError

__all__ = [
    "OBSERVATION_PATHS",
    "build_live_report",
    "collect_observations",
    "compare_registries",
]

# The complete bounded observation surface: two pages, one request each.
OBSERVATION_PATHS = ("/screener.ashx", "/screener.ashx")  # path-only form
_OBSERVATION_QUERIES = ({"v": "151"}, {"v": "111", "ft": "4"})

_NAMESPACES = ("filters", "signals", "orders", "views", "columns")
_CODE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_.")
_DROPDOWN_NAMESPACES = (("orderSelect", "orders"), ("signalSelect", "signals"))


# --- pure comparison --------------------------------------------------------------


def _norm_entries(entries: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Index one payload namespace by entry name."""
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries or []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            indexed[entry["name"]] = {
                key: value for key, value in entry.items() if key != "name"
            }
    return indexed


def _diff_namespace(
    checked_in: list[dict[str, Any]] | None,
    live: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    checked = _norm_entries(checked_in)
    current = _norm_entries(live)
    added = [{"name": name, **current[name]} for name in sorted(set(current) - set(checked))]
    removed = [{"name": name, **checked[name]} for name in sorted(set(checked) - set(current))]
    changed = []
    for name in sorted(set(checked) & set(current)):
        if checked[name] == current[name]:
            continue
        fields = {
            key: {"checked_in": checked[name].get(key), "live": current[name].get(key)}
            for key in sorted(set(checked[name]) | set(current[name]))
            if checked[name].get(key) != current[name].get(key)
        }
        if fields:
            changed.append({"name": name, "fields": fields})
    return {"added": added, "removed": removed, "changed": changed}


def compare_registries(checked_in: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Pure, deterministic metadata diff of two registry payloads.

    Entries are paired by human ``name``; added/removed/changed entries are
    reported per namespace, sorted by name. Payloads are never mutated.
    """
    report: dict[str, Any] = {}
    for namespace in _NAMESPACES:
        report[namespace] = _diff_namespace(checked_in.get(namespace), live.get(namespace))
    report["unchanged"] = not any(
        report[ns][kind] for ns in _NAMESPACES for kind in ("added", "removed", "changed")
    )
    return report


# --- bounded live observation -----------------------------------------------------


def _code_from_value(value: str) -> str:
    """Extract the provider code from an option value.

    Dropdown values are relative URLs (``screener?v=152&o=ticker``); filter
    select values are bare codes (``nyse``).
    """
    if "?" not in value and "&" not in value:
        return value
    query = parse_qs(value.split("?", 1)[-1])
    for key in ("o", "s"):
        if query.get(key):
            return query[key][0]
    return ""


def _options_from_select(select: Any) -> list[dict[str, str]]:
    options = []
    for option in select.xpath(".//option"):
        name = option.text_content().strip()
        code = _code_from_value((option.get("value") or "").strip())
        # "Any" placeholders and Elite-gated entries are not public metadata.
        if not code or "elite" in name.lower():
            continue
        options.append({"name": name, "code": code})
    return options


def _parse_observations(pages: list[tuple[dict[str, str], str]]) -> dict[str, Any]:
    """Extract provider metadata from fetched pages; values only, no raw HTML."""
    observations: dict[str, Any] = {}
    for query, html in pages:
        document = lxml_html.fromstring(html)
        if query.get("ft") == "4":
            filters: dict[str, list[dict[str, str]]] = {}
            for select in document.xpath("//select[starts-with(@id, 'fs_')]"):
                code = (select.get("data-filter") or "").strip()
                options = _options_from_select(select)
                if code and options:
                    filters[code] = options
            observations["filters"] = filters
        else:
            for select_id, namespace in _DROPDOWN_NAMESPACES:
                for select in document.xpath(f"//select[@id='{select_id}']"):
                    entries = {
                        option["code"]: option["name"]
                        for option in _options_from_select(select)
                        if set(option["code"]) <= _CODE_CHARS
                    }
                    observations.setdefault(namespace, {}).update(entries)
    return observations


async def collect_observations(*, client: Any) -> dict[str, Any]:
    """Fetch the bounded observation surface through the classified client.

    Exactly one request per reviewed route; the parsed metadata mapping is
    returned and nothing is written anywhere.
    """
    from finvizp.client import FinvizClient

    if not isinstance(client, FinvizClient):
        msg = "collect_observations requires a finvizp FinvizClient"
        raise FinvizQueryError(msg)
    html_by_path: list[tuple[dict[str, str], str]] = []
    async with client:
        for path, query in zip(OBSERVATION_PATHS, _OBSERVATION_QUERIES, strict=True):
            response = await client._fetch(path, params=query)
            html_by_path.append((query, str(response.data)))
    return _parse_observations(html_by_path)


# --- opt-in report -----------------------------------------------------------------

# Report strings are reviewer-facing: any string carrying markup or shell-ish
# characters is replaced wholesale so cookies, proxies, or raw HTML fragments
# can never reach the JSON report.
_UNSAFE_CHARS = frozenset('<>";\'`{}\\')


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return "[redacted]" if _UNSAFE_CHARS & set(value) else value
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    return value


def _registry_payload() -> dict[str, Any]:
    path = resources.files("finvizp").joinpath("screener_registry.json")
    return json.loads(path.read_text("utf-8"))


def _registry_shape(observations: dict[str, Any]) -> dict[str, Any]:
    """Reshape live observations into the registry payload shape."""
    return {
        "version": None,
        "observation_date": None,
        "page_size": None,
        "filters": [
            {"name": code, "code": code, "type": "observed", "options": options}
            for code, options in sorted((observations.get("filters") or {}).items())
        ],
        "signals": [
            {"name": name, "code": code}
            for code, name in sorted((observations.get("signals") or {}).items())
        ],
        "orders": [
            {"name": name, "code": code}
            for code, name in sorted((observations.get("orders") or {}).items())
        ],
        "views": [],
        "columns": [],
    }


def build_live_report(
    *,
    live: bool,
    client: Any = None,
    observations: dict[str, Any] | None = None,
    out_path: Any = None,
) -> str:
    """Build the reviewable drift report as ordered, deterministic JSON.

    ``live`` must be an explicit boolean keyword: ``live=True`` performs the
    bounded metadata fetch through ``client`` (a default-constructed
    :class:`~finvizp.client.FinvizClient` when omitted); ``live=False``
    compares already-supplied ``observations`` so the report shape can be
    exercised offline. Only namespaces actually observed are compared — an
    unobserved namespace is never reported as removed drift. The checked-in
    registry is only ever read; the report goes to ``out_path`` or is returned
    to the caller.
    """
    if not isinstance(live, bool):
        msg = "live must be an explicit boolean: live access is always opt-in"
        raise FinvizQueryError(msg)
    checked_in = _registry_payload()
    if live:
        if observations is not None:
            msg = "pass either live=True or observations, not both"
            raise FinvizQueryError(msg)
        if client is None:
            from finvizp.client import FinvizClient

            client = FinvizClient()

        async def _run() -> dict[str, Any]:
            return await collect_observations(client=client)

        from finvizp._sync import run_sync

        observed = _registry_shape(run_sync(_run()))
    else:
        observed = _registry_shape(observations or {})
    # Compare only namespaces with observed entries: absent evidence is not
    # removal evidence, so unobserved namespaces stay out of the report.
    observed_namespaces = [ns for ns in _NAMESPACES if observed.get(ns)]
    checked_slice = {ns: checked_in.get(ns) for ns in observed_namespaces}
    observed_slice = {ns: observed[ns] for ns in observed_namespaces}
    payload = {
        "meta": {
            "observation_date": checked_in["observation_date"],
            "registry_version": checked_in["version"],
            "registry_mutated": False,
        },
        "report": _scrub(compare_registries(checked_slice, observed_slice)),
    }
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
    return text
