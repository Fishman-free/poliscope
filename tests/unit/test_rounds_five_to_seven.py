from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

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
from packages.council.rounds.registry import PhaseContext, run_blindspot_bounty
from packages.epistemo.contracts import TaskPhase


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


class _BlindspotDeliberator:
    def __init__(self, blindspot_id: UUID) -> None:
        self._blindspot_id = blindspot_id

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        assert phase is TaskPhase.BLINDSPOT_BOUNTY
        return {
            "blindspots": [
                {
                    "id": str(self._blindspot_id),
                    "statement": "An unmeasured confound remains untested",
                    "impact": "0.9",
                    "uncertainty": "0.8",
                    "investigability": "0.7",
                    "novelty": "0.6",
                    "normalized_cost": "0.3",
                }
            ]
        }


class _CollidingIdDeliberator:
    """Two seats nominate DIFFERENT blindspots under the SAME hallucinated id.

    Real models do this (a prompt example uuid reused verbatim); the id is
    derived into the idempotency key, so a duplicate must be re-keyed
    deterministically instead of raising EventConflict.
    """

    def __init__(self, shared_id: UUID) -> None:
        self._shared_id = shared_id

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        assert phase is TaskPhase.BLINDSPOT_BOUNTY
        statement = (
            "A measurement bias from self-reported use"
            if seat is Seat.CAUSAL_SCIENTIST
            else "An unmeasured reverse-causation channel"
        )
        return {
            "blindspots": [
                {
                    "id": str(self._shared_id),
                    "statement": statement,
                    "impact": "0.9",
                    "uncertainty": "0.8",
                    "investigability": "0.7",
                    "novelty": "0.6",
                    "normalized_cost": "0.3",
                }
            ]
        }


async def test_bounty_rekeys_hallucinated_duplicate_ids() -> None:
    """A repeated model uuid must not collide the ledger idempotency key."""
    shared_id = uuid4()
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.BLINDSPOT_BOUNTY,
        seats=(Seat.CAUSAL_SCIENTIST, Seat.MEASUREMENT_SCIENTIST),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(uuid4(),),
        deliberator=_CollidingIdDeliberator(shared_id),
    )

    outcome = await run_blindspot_bounty(context)

    # Both blindspots survive (nothing dropped on the collision), and their
    # ids are distinct: the second was re-keyed deterministically.
    ranked = outcome.carry["ranked_blindspots"]
    assert len(ranked) == 2
    rekeyed = [r for r in ranked if r["blindspot_id"] != str(shared_id)]
    assert len(rekeyed) == 1
    assert rekeyed[0]["statement"] == "An unmeasured reverse-causation channel"


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


def test_bounty_assignments_form_seven_seat_coalition() -> None:
    handler = BlindspotBountyHandler()
    output = handler.score_and_assign(
        BountyInput(blindspot_items=(_blindspot(),))
    )
    # Design doc 6: a blindspot is not handed to one best scientist; the whole
    # seven-seat council investigates it from complementary angles.
    seats = {a["target_seat"] for a in output.assignments}
    assert seats == {seat.value for seat in Seat}
    # Every seat got a distinct, non-empty task.
    tasks = {a["target_seat"]: a["task"] for a in output.assignments}
    assert all(tasks[seat] for seat in seats)
    assert len(set(tasks.values())) == 7


async def test_bounty_carries_ranked_pending_investigations() -> None:
    blindspot_id = uuid4()
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.BLINDSPOT_BOUNTY,
        seats=(Seat.CAUSAL_SCIENTIST,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(uuid4(),),
        deliberator=_BlindspotDeliberator(blindspot_id),
    )

    outcome = await run_blindspot_bounty(context)

    ranked = outcome.carry["ranked_blindspots"]
    assignments = outcome.carry["blindspot_assignments"]
    assert ranked == (
        {
            "blindspot_id": str(blindspot_id),
            "statement": "An unmeasured confound remains untested",
            "score": "0.7700",
            "rank": 1,
            "status": "pending_investigation",
        },
    )
    # Seven-seat division of labour: one entry per seat, each with its own task.
    assert {a["target_seat"] for a in assignments} == {seat.value for seat in Seat}
    assert all(a["task"] for a in assignments)
    assert all(a["priority_rank"] == 1 for a in assignments)


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
