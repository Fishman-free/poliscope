from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.rounds.blindspot_bounty import (
    BlindspotBountyHandler,
    BlindspotItem,
    BountyInput,
)
from packages.council.rounds.final_rejudgment import (
    FinalRejudgmentHandler,
    FinalRejudgmentInput,
)
from packages.council.rounds.joint_modeling import (
    JointModelingHandler,
    JointModelInput,
)


def _blindspot(**over: Any) -> BlindspotItem:
    base: dict[str, Any] = dict(
        id=uuid4(),
        statement="unmeasured confound",
        impact=Decimal("0.9"),
        uncertainty=Decimal("0.8"),
        investigability=Decimal("0.7"),
        novelty=Decimal("0.6"),
        normalized_cost=Decimal("0.3"),
    )
    base.update(over)
    return BlindspotItem(**base)


def test_full_bounty_to_rejudgment_pipeline() -> None:
    bounty_handler = BlindspotBountyHandler()
    bounty_output = bounty_handler.score_and_assign(
        BountyInput(
            blindspot_items=(
                _blindspot(statement="confound A"),
                _blindspot(statement="confound B", impact=Decimal("0.5")),
            ),
            claim_refs=(uuid4(),),
        )
    )
    assert len(bounty_output.scored_items) == 2
    assert bounty_output.scored_items[0].score >= bounty_output.scored_items[1].score

    joint_handler = JointModelingHandler()
    joint_output = joint_handler.run(
        JointModelInput(
            claim_refs=(uuid4(),),
            challenge_refs=(uuid4(),),
            strongest_opposition_refs=(uuid4(),),
            falsification_conditions=("fc1",),
            boundary_conditions=("bc1",),
            unresolved_conflicts=("uc1",),
        )
    )
    assert joint_output.ready is True

    final_handler = FinalRejudgmentHandler()
    final_output = final_handler.run(
        FinalRejudgmentInput(
            joint_snapshot=JointModelInput(
                claim_refs=(uuid4(),),
                challenge_refs=(uuid4(),),
                strongest_opposition_refs=(uuid4(),),
                falsification_conditions=("fc",),
                boundary_conditions=("bc",),
                unresolved_conflicts=("uc",),
            ),
            initial_judgments={seat: f"init-{seat.value}" for seat in Seat},
        )
    )
    assert len(final_output.judgments) == 7


def test_bounty_assignments_target_evidence_auditor() -> None:
    handler = BlindspotBountyHandler()
    output = handler.score_and_assign(
        BountyInput(blindspot_items=(_blindspot(),))
    )
    assert all(
        a["target_seat"] == Seat.EVIDENCE_AUDITOR.value
        for a in output.assignments
    )


def test_joint_model_no_majority_vote() -> None:
    handler = JointModelingHandler()
    output = handler.run(
        JointModelInput(
            claim_refs=(uuid4(),),
            challenge_refs=(uuid4(),),
            strongest_opposition_refs=(uuid4(),),
            falsification_conditions=("fc",),
            boundary_conditions=("bc",),
            unresolved_conflicts=("uc",),
        )
    )
    # No vote count field exists — consensus is conditional, not numeric
    assert "conditional" in output.conditional_consensus.lower()


def test_final_rejudgment_all_seats_independent() -> None:
    handler = FinalRejudgmentHandler()
    output = handler.run(
        FinalRejudgmentInput(
            joint_snapshot=JointModelInput(
                claim_refs=(uuid4(),),
                challenge_refs=(uuid4(),),
                strongest_opposition_refs=(uuid4(),),
                falsification_conditions=("fc",),
                boundary_conditions=("bc",),
                unresolved_conflicts=("uc",),
            ),
            initial_judgments={seat: f"init-{seat.value}" for seat in Seat},
        )
    )
    seats = {j.seat for j in output.judgments}
    assert seats == set(Seat)
