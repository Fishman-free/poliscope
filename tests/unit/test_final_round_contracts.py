from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.rounds.blindspot_bounty import (
    BlindspotBountyHandler,
    BlindspotItem,
    BountyInput,
    BountyOutput,
)
from packages.council.rounds.final_rejudgment import (
    FinalRejudgmentHandler,
    FinalRejudgmentInput,
    FinalRejudgmentOutput,
)
from packages.council.rounds.joint_modeling import (
    JointModelingHandler,
    JointModelInput,
    JointModelOutput,
)


def _make_blindspot(**overrides: Any) -> BlindspotItem:
    base: dict[str, Any] = dict(
        id=uuid4(),
        statement="unmeasured confound",
        impact=Decimal("0.9"),
        uncertainty=Decimal("0.8"),
        investigability=Decimal("0.7"),
        novelty=Decimal("0.6"),
        normalized_cost=Decimal("0.3"),
    )
    base.update(overrides)
    return BlindspotItem(**base)


def test_blindspot_bounty_scores_and_assigns() -> None:
    handler = BlindspotBountyHandler()
    bounty_input = BountyInput(
        blindspot_items=(_make_blindspot(),),
        claim_refs=(uuid4(),),
    )
    output = handler.score_and_assign(bounty_input)
    assert isinstance(output, BountyOutput)
    assert len(output.scored_items) == 1
    assert output.scored_items[0].score > 0
    assert output.assignments  # non-empty assignment generated


def test_joint_model_requires_dialectical_fields() -> None:
    handler = JointModelingHandler()
    incomplete = JointModelInput(
        claim_refs=(uuid4(),),
        challenge_refs=(),
        strongest_opposition_refs=(),
        falsification_conditions=(),
        boundary_conditions=(),
        unresolved_conflicts=(),
    )
    result = handler.run(incomplete)
    assert isinstance(result, JointModelOutput)
    assert result.ready is False
    assert "strongest_opposition_refs" in result.missing_fields
    assert "falsification_conditions" in result.missing_fields


def test_joint_model_complete_when_all_fields_present() -> None:
    handler = JointModelingHandler()
    complete = JointModelInput(
        claim_refs=(uuid4(),),
        challenge_refs=(uuid4(),),
        strongest_opposition_refs=(uuid4(),),
        falsification_conditions=("if X then Y fails",),
        boundary_conditions=("only in adolescents",),
        unresolved_conflicts=("effect size varies",),
    )
    result = handler.run(complete)
    assert result.ready is True
    assert result.conditional_consensus != ""
    assert result.hinge_variables  # must identify hinge variables


def test_final_rejudgment_is_independent_for_all_seats() -> None:
    handler = FinalRejudgmentHandler()
    joint_snapshot = JointModelInput(
        claim_refs=(uuid4(),),
        challenge_refs=(uuid4(),),
        strongest_opposition_refs=(uuid4(),),
        falsification_conditions=("fc",),
        boundary_conditions=("bc",),
        unresolved_conflicts=("uc",),
    )
    final_input = FinalRejudgmentInput(
        joint_snapshot=joint_snapshot,
        initial_judgments={seat: f"init-{seat.value}" for seat in Seat},
    )
    result = handler.run(final_input)
    assert isinstance(result, FinalRejudgmentOutput)
    assert len(result.judgments) == 7
    assert len({j.seat for j in result.judgments}) == 7
    assert all(j.evidence_driven_update for j in result.judgments)


def test_final_rejudgment_dissent_preserved() -> None:
    handler = FinalRejudgmentHandler()
    joint_snapshot = JointModelInput(
        claim_refs=(uuid4(),),
        challenge_refs=(uuid4(),),
        strongest_opposition_refs=(uuid4(),),
        falsification_conditions=("fc",),
        boundary_conditions=("bc",),
        unresolved_conflicts=("uc",),
    )
    initial = {seat: f"init-{seat.value}" for seat in Seat}
    initial[Seat.ADVERSARY_FALSIFIER] = "strong dissent"
    final_input = FinalRejudgmentInput(
        joint_snapshot=joint_snapshot,
        initial_judgments=initial,
    )
    result = handler.run(final_input)
    adversary = next(
        j for j in result.judgments if j.seat == Seat.ADVERSARY_FALSIFIER
    )
    assert adversary.has_dissent is True
