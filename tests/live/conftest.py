"""Keep ordinary CI hermetic: live smokes run only under ``-m live_public``.

The foundation spec (``docs/superpowers/specs/2026-08-27-finvizp-foundation-design.md``)
requires hermetic ordinary tests with live smokes separately gated. Default
``pytest``/CI skips every test under ``tests/live``; the marker expression
``-m live_public`` is the explicit opt-in.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.option.markexpr and "live_public" in config.option.markexpr:
        return
    skip = pytest.mark.skip(reason="live smoke: run explicitly with -m live_public")
    for item in items:
        if "tests/live" in str(item.fspath):
            item.add_marker(skip)
