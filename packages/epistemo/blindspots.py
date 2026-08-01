from __future__ import annotations

from decimal import Decimal

from packages.kernel.contracts import ContractModel


class Blindspot(ContractModel):
    impact: Decimal
    uncertainty: Decimal
    investigability: Decimal
    novelty: Decimal
    normalized_cost: Decimal


WEIGHTS = (
    Decimal("0.30"),  # impact
    Decimal("0.25"),  # uncertainty
    Decimal("0.20"),  # investigability
    Decimal("0.15"),  # novelty
    Decimal("0.10"),  # (1 - normalized_cost)
)


def score_blindspot(item: Blindspot) -> Decimal:
    score = (
        WEIGHTS[0] * item.impact
        + WEIGHTS[1] * item.uncertainty
        + WEIGHTS[2] * item.investigability
        + WEIGHTS[3] * item.novelty
        + WEIGHTS[4] * (Decimal("1") - item.normalized_cost)
    )
    return score.quantize(Decimal("0.0001"))
