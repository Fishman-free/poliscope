from __future__ import annotations

from decimal import Decimal

from packages.epistemo.blindspots import Blindspot, score_blindspot


def _make_blindspot(**over) -> Blindspot:
    base = dict(
        impact=Decimal("0.8"),
        uncertainty=Decimal("0.8"),
        investigability=Decimal("0.6"),
        novelty=Decimal("0.4"),
        normalized_cost=Decimal("0.2"),
    )
    base.update(over)
    return Blindspot(**base)


def test_blindspot_score_uses_exact_five_dimension_formula() -> None:
    item = _make_blindspot(
        impact=Decimal("1"),
        uncertainty=Decimal("0.8"),
        investigability=Decimal("0.6"),
        novelty=Decimal("0.4"),
        normalized_cost=Decimal("0.2"),
    )
    assert score_blindspot(item) == Decimal("0.7600")


def test_blindspot_score_zero_when_all_zero() -> None:
    item = _make_blindspot(
        impact=Decimal("0"),
        uncertainty=Decimal("0"),
        investigability=Decimal("0"),
        novelty=Decimal("0"),
        normalized_cost=Decimal("1"),
    )
    assert score_blindspot(item) == Decimal("0.0000")


def test_blindspot_rejects_sixth_dimension() -> None:
    import pytest
    with pytest.raises((TypeError, ValueError)):
        Blindspot(
            impact=Decimal("0.5"),
            uncertainty=Decimal("0.5"),
            investigability=Decimal("0.5"),
            novelty=Decimal("0.5"),
            normalized_cost=Decimal("0.5"),
            popularity=Decimal("0.5"),  # not allowed
        )


def test_suite() -> None:
    test_blindspot_score_uses_exact_five_dimension_formula()
    test_blindspot_score_zero_when_all_zero()
    test_blindspot_rejects_sixth_dimension()
