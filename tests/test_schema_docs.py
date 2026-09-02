"""Docs validate against the live registry (Card 0.5-B, step 6).

``docs/user/reference/schema-versioning.md`` is hand-written but registry-bound:
its dataset/version table must always match ``schema_registry.json``, so the
reference can never silently drift from the contract the code enforces.
"""

from __future__ import annotations

import re
from pathlib import Path

from finvizp import schemas

PAGE = Path("docs/user/reference/schema-versioning.md")
ROW = re.compile(r"^\| `([a-z_]+)` \| (\d+) \|", re.MULTILINE)


def _documented_versions() -> dict[str, int]:
    text = PAGE.read_text("utf-8")
    found = dict(ROW.findall(text))
    assert found, "dataset table missing from schema-versioning docs"
    return {k: int(v) for k, v in found.items()}


def test_docs_dataset_table_matches_registry() -> None:
    documented = _documented_versions()
    actual = {name: ds.version for name, ds in schemas.registry().items()}
    assert documented == actual, (
        "schema-versioning.md dataset table drifted from schema_registry.json"
    )


def test_docs_version_bump_checklist_names_version_guard() -> None:
    """The documented change procedure points at the pinned-version guard."""
    text = PAGE.read_text("utf-8")
    assert "tests/test_schema_contracts.py" in text
    assert "schema_registry.json" in text
