"""Contract tests for symbol normalization (trim, uppercase, class-share mapping)."""

from __future__ import annotations

import pytest

from finvizp._symbols import SymbolInputError, normalize_symbols


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("brk.b", "BRK-B"),
        ("BRK/B", "BRK-B"),
        ("BRK-B", "BRK-B"),
        (" aapl ", "AAPL"),
        ("bf-b", "BF-B"),
    ],
)
def test_normalization_cases(raw: str, canonical: str) -> None:
    assert normalize_symbols(raw) == [canonical]


def test_iterable_input_and_first_occurrence_order() -> None:
    assert normalize_symbols(["msft", "brk.b", "AAPL"]) == ["MSFT", "BRK-B", "AAPL"]


def test_duplicates_dedupe_by_canonical_symbol() -> None:
    assert normalize_symbols(["brk.b", "BRK/B", "BRK-B"]) == ["BRK-B"]
    assert normalize_symbols(["AAPL", "aapl ", " AAPL"]) == ["AAPL"]


def test_blank_input_rejected() -> None:
    with pytest.raises(SymbolInputError):
        normalize_symbols("")
    with pytest.raises(SymbolInputError):
        normalize_symbols(["AAPL", "   "])


def test_empty_iterable_rejected() -> None:
    with pytest.raises(SymbolInputError):
        normalize_symbols([])


def test_empty_batch_without_symbols_rejected() -> None:
    with pytest.raises(SymbolInputError):
        normalize_symbols(None)


def test_unknown_punctuation_rejected() -> None:
    with pytest.raises(SymbolInputError):
        normalize_symbols("AAP!")
    with pytest.raises(SymbolInputError):
        normalize_symbols(["GOOG+L"])


def test_long_symbols_are_accepted() -> None:
    assert normalize_symbols("A" * 13) == ["A" * 13]


def test_positions_spelling_and_mapping_are_preserved() -> None:
    records = normalize_symbols(["brk.b", "AAPL", "brk/B"], resolve=True)
    assert [(r.position, r.requested, r.canonical) for r in records] == [
        (0, "brk.b", "BRK-B"),
        (1, "AAPL", "AAPL"),
        (2, "brk/B", "BRK-B"),
    ]
