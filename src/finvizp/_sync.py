"""Event-loop-safe sync execution bridge."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

__all__ = ["run_sync"]


def run_sync(coroutine: Coroutine[Any, Any, T]) -> T:
    """Run one coroutine to completion outside any event loop.

    Raises :class:`RuntimeError` when called inside a running loop; never starts
    a nested loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        coroutine.close()  # consume the unawaited coroutine; no never-awaited warning
        msg = (
            "run_sync() cannot be called inside a running event loop; "
            "await the async operation directly instead"
        )
        raise RuntimeError(msg)
    return asyncio.run(coroutine)
