"""Shared bounded-smoke helpers: one request per family, typed failure buckets.

Consolidates the per-file ``_fetch`` boilerplate and classifies every failure
into exactly one review category so a red weekly run names its bucket:

- ``network``: transport/5xx/rate limit — access path broken, retry later;
- ``block``: 403/challenge/entitlement — never bypassed, route for review;
- ``drift``: :class:`FinvizParseError` — provider markup/shape changed;
- ``data``: :class:`FinvizDataError` — conversion/unit normalization broke;
- ``assertion``: any other exception — smoke contract itself broken.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from finvizp import FinvizBlockedError, FinvizDataError, FinvizError, FinvizParseError

# Ponytail: sequential only — each test awaits one request before the next
# starts; promote to an explicit pacing helper only if live runs ever need one.
SEQUENCE = "one sequential bounded request per public family"


async def fetch(coro_factory: Callable[[], Any], *, skip_drift: bool = False) -> Any:
    """Await one smoke request; classify failures instead of collapsing them.

    Access/network failures always skip (a red build must mean drift or a
    broken contract, never a flaky network). Parse drift skips only when the
    caller marks the parser ``skip_drift`` — surfaces chosen for their stable
    structure leave drift red so it is routed for review.
    """
    try:
        return await coro_factory()
    except FinvizBlockedError as exc:
        pytest.skip(f"live block/challenge/entitlement, never bypassed: {exc}")
    except FinvizParseError as exc:
        if skip_drift:
            pytest.skip(f"live parse drift, route for review: {exc}")
        raise
    except FinvizDataError:
        raise  # data conversion broke: keep red, bucket = data
    except FinvizError as exc:
        pytest.skip(f"live access unavailable (network/transport): {exc}")


__all__ = ["SEQUENCE", "fetch"]
