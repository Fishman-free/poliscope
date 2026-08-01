from __future__ import annotations

from packages.evaluation.corpus import load_case_inventory


def test_case_inventory_has_cases() -> None:
    cases = load_case_inventory()
    assert len(cases) >= 1


def test_suite() -> None:
    test_case_inventory_has_cases()
