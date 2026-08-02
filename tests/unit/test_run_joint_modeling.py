"""Unit coverage for run_joint_modeling's Dialectical Fold wiring (registry.py).

CLAUDE.md 5.2 requires a Dialectical Fold to preserve common ground, the
strongest support and opposition, hinge variables, boundary conditions,
unresolved conflicts, falsification conditions, and source refs -- and
forbids folding a debate that has nothing to preserve. This module checks
both branches of that rule without Docker or a real model gateway: a
hand-written fake deliberator plays the seven seats' JOINT_MODELING output.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

import pytest

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    EmittedEvent,
    PhaseContext,
    UnavailableDeliberator,
    run_joint_modeling,
)
from packages.epistemo.contracts import TaskPhase
from packages.evidence.contracts import EvidenceNodeType


class _ScriptedDeliberator:
    def __init__(self, output: Mapping[str, object]) -> None:
        self._output = output

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        assert phase is TaskPhase.JOINT_MODELING
        return self._output


def _context(claim_id: UUID, output: Mapping[str, object]) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.JOINT_MODELING,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(claim_id,),
        deliberator=_ScriptedDeliberator(output),
    )


def _capsule_events(events: tuple[EmittedEvent, ...]) -> list[EmittedEvent]:
    return [
        event
        for event in events
        if event.event_type == EvidenceNodeType.DEBATE_CAPSULE.value
    ]


async def test_capsule_produced_when_boundary_and_conflicts_present() -> None:
    claim_id = uuid4()
    opposition_id = uuid4()
    output = {
        "strongest_opposition_refs": [str(opposition_id)],
        "falsification_conditions": ["A null effect in a preregistered RCT."],
        "boundary_conditions": ["Western adolescent samples only."],
        "unresolved_conflicts": ["Effect direction across sexes."],
    }

    outcome = await run_joint_modeling(_context(claim_id, output))

    capsules = _capsule_events(outcome.events)
    assert len(capsules) == 1
    event = capsules[0]
    assert event.evidence_level == "A"
    assert event.payload["boundary_conditions"] == ["Western adolescent samples only."]
    assert event.payload["unresolved_conflicts"] == ["Effect direction across sexes."]
    assert event.payload["strongest_support"] == [str(claim_id)]
    assert event.payload["strongest_opposition"] == [str(opposition_id)]
    source_refs = event.payload["source_refs"]
    assert isinstance(source_refs, list)
    assert set(source_refs) == {str(claim_id), str(opposition_id)}
    assert not any(
        slot == "JOINT_MODELING:no_capsule_fold" for slot in outcome.unfilled_slots
    )


@pytest.mark.parametrize(
    "missing_field", ["boundary_conditions", "unresolved_conflicts"]
)
async def test_missing_boundary_or_conflict_records_unfilled_slot_not_capsule(
    missing_field: str,
) -> None:
    claim_id = uuid4()
    output = {
        "strongest_opposition_refs": [str(uuid4())],
        "falsification_conditions": ["A null effect in a preregistered RCT."],
        "boundary_conditions": ["Western adolescent samples only."],
        "unresolved_conflicts": ["Effect direction across sexes."],
    }
    output[missing_field] = []

    outcome = await run_joint_modeling(_context(claim_id, output))

    assert _capsule_events(outcome.events) == []
    assert "JOINT_MODELING:no_capsule_fold" in outcome.unfilled_slots


async def test_no_ready_consensus_never_attempts_a_capsule() -> None:
    """When required fields are missing, the gap is the missing field itself,
    not a capsule-fold gap -- there was never a consensus to fold."""
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.JOINT_MODELING,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(uuid4(),),
        deliberator=UnavailableDeliberator(),
    )

    outcome = await run_joint_modeling(context)

    assert _capsule_events(outcome.events) == []
    assert "JOINT_MODELING:no_capsule_fold" not in outcome.unfilled_slots
