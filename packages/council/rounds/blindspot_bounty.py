from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

from packages.council.contracts import Seat

BLINDSPOT_WEIGHTS = (
    Decimal("0.30"),  # impact
    Decimal("0.25"),  # uncertainty
    Decimal("0.20"),  # investigability
    Decimal("0.15"),  # novelty
    Decimal("0.10"),  # (1 - normalized_cost)
)


@dataclass(frozen=True, slots=True)
class BlindspotItem:
    id: UUID
    statement: str
    impact: Decimal
    uncertainty: Decimal
    investigability: Decimal
    novelty: Decimal
    normalized_cost: Decimal


@dataclass(frozen=True, slots=True)
class ScoredBlindspot:
    item: BlindspotItem
    score: Decimal


@dataclass(frozen=True, slots=True)
class BountyInput:
    blindspot_items: tuple[BlindspotItem, ...]
    claim_refs: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class BountyOutput:
    scored_items: tuple[ScoredBlindspot, ...]
    assignments: tuple[dict[str, object], ...]


def score_blindspot(item: BlindspotItem) -> Decimal:
    score = (
        BLINDSPOT_WEIGHTS[0] * item.impact
        + BLINDSPOT_WEIGHTS[1] * item.uncertainty
        + BLINDSPOT_WEIGHTS[2] * item.investigability
        + BLINDSPOT_WEIGHTS[3] * item.novelty
        + BLINDSPOT_WEIGHTS[4] * (Decimal("1") - item.normalized_cost)
    )
    return score.quantize(Decimal("0.0001"))


@dataclass
class BlindspotBountyHandler:
    _scored: list[ScoredBlindspot] = field(default_factory=list)

    def score_and_assign(self, input: BountyInput) -> BountyOutput:
        scored = tuple(
            ScoredBlindspot(item=item, score=score_blindspot(item))
            for item in input.blindspot_items
        )
        sorted_scored = sorted(scored, key=lambda s: s.score, reverse=True)
        assignments = tuple(
            {
                "id": uuid4(),
                "type": "ASSIGNMENT",
                "target_seat": Seat.EVIDENCE_AUDITOR.value,
                "blindspot_id": item.item.id,
                "priority_rank": rank + 1,
                "statement": item.item.statement,
                "score": str(item.score),
            }
            for rank, item in enumerate(sorted_scored)
        )
        self._scored.extend(sorted_scored)
        return BountyOutput(scored_items=tuple(sorted_scored), assignments=assignments)
