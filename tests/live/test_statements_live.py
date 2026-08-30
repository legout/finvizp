"""Bounded live smoke for the statements operation (opt-in; never CI).

Run: uv run pytest -q tests/test_statements_live.py -m live_public
One request per reviewed statement code against the canonical public endpoint,
proving current access and shape only. Never replaces fixtures.
"""

from __future__ import annotations

from importlib import import_module

import pytest

statements = import_module("finvizp.statements")

from finvizp.errors import FinvizError  # noqa: E402
from finvizp.results import ResultStatus  # noqa: E402

pytestmark = pytest.mark.live_public


@pytest.mark.parametrize("code", ["IA", "IQ", "BA", "BQ", "CA", "CQ"])
async def test_live_statement_codes(code: str) -> None:
    client = statements._transient_client()
    try:
        result = await statements.statements_async("AAPL", statement=code, client=client)
    except FinvizError as exc:  # network/access failure, not parser drift
        raise AssertionError(f"live access failed for {code}: {exc}") from exc
    finally:
        await client.close()
    table = result.table
    assert result.metadata.status in {ResultStatus.COMPLETE, ResultStatus.EMPTY}
    if result.metadata.status is ResultStatus.COMPLETE:
        assert table.num_rows > 0
        assert set(table.column("statement_kind").to_pylist()) == {
            statements.STATEMENT_CODES[code][0]
        }
        assert set(table.column("periodicity").to_pylist()) == {statements.STATEMENT_CODES[code][1]}
        assert all(table.column("currency").to_pylist())
