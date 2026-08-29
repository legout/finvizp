from __future__ import annotations

import finvizp


def test_package_metadata() -> None:
    assert finvizp.__version__ == "0.1.0"
    # The curated 0.1 public surface (see finvizp.capabilities for the
    # implemented/planned ledger this export list mirrors).
    assert finvizp.__all__ and "symbols_async" in finvizp.__all__
    for name in finvizp.__all__:
        assert hasattr(finvizp, name), name
