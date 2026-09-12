from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from packages.council.contracts import Seat
from packages.council.rounds.joint_modeling import JointModelInput


@dataclass(frozen=True, slots=True)
class FinalRejudgmentInput:
    joint_snapshot: JointModelInput
    initial_judgments: dict[Seat, str]
    # The seats that actually took part in this rejudgment. None (the
    # default, and the value production always passes today) means every
    # seat -- a run with fewer seats, such as the single-agent evaluation
    # baseline, must not mint "no initial judgment" placeholders for seats
    # that never participated.
    seats: tuple[Seat, ...] | None = None
    # What each seat reported as its own confidence this round, keyed by seat.
    # Absent for a seat that did not answer this round (its judgment falls
    # back to the precommitment text, whose confidence belongs to a different
    # phase and must not be presented as this one's).
    confidences: Mapping[Seat, float] | None = None


@dataclass(frozen=True, slots=True)
class SeatJudgment:
    seat: Seat
    final_judgment: str
    # None means "this seat did not report one", which the panel renders as
    # 未记录. It is deliberately not defaulted to a number: a constant in this
    # field reads as a measurement and is not one (CLAUDE.md 16).
    confidence: float | None
    evidence_refs: tuple[UUID, ...]
    has_dissent: bool = False
    evidence_driven_update: bool = True


@dataclass(frozen=True, slots=True)
class FinalRejudgmentOutput:
    judgments: tuple[SeatJudgment, ...]


_DISSENT_KEYWORDS = frozenset(
    {"dissent", "reject", "oppose", "disagree", "反驳", "反对"}
)


def _detect_dissent(statement: str) -> bool:
    lowered = statement.lower()
    return any(keyword in lowered for keyword in _DISSENT_KEYWORDS)


@dataclass
class FinalRejudgmentHandler:
    _last_output: FinalRejudgmentOutput | None = field(
        default=None, init=False
    )

    def run(self, input: FinalRejudgmentInput) -> FinalRejudgmentOutput:
        # Only the seats that actually took part in the rejudgment get a
        # SeatJudgment; without the explicit list this used to iterate every
        # seat, minting "no initial judgment" placeholders for seats that
        # never participated (six of seven FINAL_JUDGMENT events in the
        # single-agent evaluation baseline). ``tuple(Seat)`` is exactly what
        # ``for seat in Seat`` enumerated, so the all-seats fallback is
        # behaviour-identical to the pre-fix path.
        seats = input.seats if input.seats is not None else tuple(Seat)
        confidences = input.confidences or {}
        judgments = tuple(
            SeatJudgment(
                seat=seat,
                final_judgment=input.initial_judgments.get(
                    seat, "no initial judgment"
                ),
                confidence=confidences.get(seat),
                evidence_refs=input.joint_snapshot.claim_refs,
                has_dissent=_detect_dissent(
                    input.initial_judgments.get(seat, "")
                ),
                evidence_driven_update=bool(input.joint_snapshot.claim_refs),
            )
            for seat in seats
        )
        output = FinalRejudgmentOutput(judgments=judgments)
        self._last_output = output
        return output
