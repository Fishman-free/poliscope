from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

from packages.council.contracts import ALL_SEATS, Seat

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


# Each seat's fixed angle on any blindspot, keyed by role. This is design
# doc 6's "FormCoalition" -- a blindspot is not handed to one best scientist;
# the whole council investigates it from seven complementary angles. The task
# text is deterministic so a replay yields the same division table.
_SEAT_ANGLE_TEMPLATES: dict[Seat, str] = {
    Seat.THEORY_BUILDER: "判断哪些理论依赖该盲点的前提，并给出可区分预测",
    Seat.CAUSAL_SCIENTIST: "分析该盲点若成立，会把因果效应推向哪个方向",
    Seat.MEASUREMENT_SCIENTIST: "比较各研究对相关构念的操作化，定位测量差异",
    Seat.REPLICATION_SCIENTIST: "核对支撑/反驳该盲点的证据是否来自独立样本与设计",
    Seat.BOUNDARY_SCIENTIST: "检查该盲点在不同人群、国家与时期之间是否不同",
    Seat.ADVERSARY_FALSIFIER: "寻找即使该盲点不成立、原结论仍成立的证据",
    Seat.EVIDENCE_AUDITOR: "核验该盲点涉及的原文、DOI 与数据独立性",
}


@dataclass
class BlindspotBountyHandler:
    _scored: list[ScoredBlindspot] = field(default_factory=list)

    def score_and_assign(self, input: BountyInput) -> BountyOutput:
        scored = tuple(
            ScoredBlindspot(item=item, score=score_blindspot(item))
            for item in input.blindspot_items
        )
        sorted_scored = sorted(scored, key=lambda s: s.score, reverse=True)
        # Seven-seat division of labour per blindspot (design doc 6): one entry
        # per seat, each with its own angle. The whole council investigates the
        # same blindspot from complementary roles rather than a single seat
        # carrying it alone.
        assignments: list[dict[str, object]] = []
        for rank, item in enumerate(sorted_scored):
            for seat in sorted(ALL_SEATS, key=lambda s: s.value):
                assignments.append(
                    {
                        "id": uuid4(),
                        "type": "ASSIGNMENT",
                        "target_seat": seat.value,
                        "blindspot_id": item.item.id,
                        "priority_rank": rank + 1,
                        "statement": item.item.statement,
                        "score": str(item.score),
                        "task": _SEAT_ANGLE_TEMPLATES[seat],
                    }
                )
        self._scored.extend(sorted_scored)
        return BountyOutput(
            scored_items=tuple(sorted_scored), assignments=tuple(assignments)
        )
