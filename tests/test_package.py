from __future__ import annotations

import finvizp


def test_package_metadata() -> None:
    assert finvizp.__version__ == "0.1.0"
    assert finvizp.__all__ == ["__version__"]
