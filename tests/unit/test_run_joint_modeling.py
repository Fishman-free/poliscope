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
    CONFIDENCE_UPDATED,
    EmittedEvent,
    PhaseContext,
    UnavailableDeliberator,
    run_joint_modeling,
)
from packages.epistemo.contracts import TaskPhase
from packages.evidence.contracts import EvidenceNodeType


def _confidence_events(events: tuple[EmittedEvent, ...]) -> list[EmittedEvent]:
    return [event for event in events if event.event_type == CONFIDENCE_UPDATED]


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


def _consensus_events(events: tuple[EmittedEvent, ...]) -> list[EmittedEvent]:
    from packages.council.rounds.registry import CONSENSUS_DRAFTED

    return [event for event in events if event.event_type == CONSENSUS_DRAFTED]


async def test_consensus_annotates_unresolved_conflicts_as_merge_candidates() -> None:
    """Merge (design spec 5, mechanism 3 of 3), cut down to record-only: every
    unresolved conflict is surfaced as a merge candidate on the same event, with
    no code here executing a merge -- that stays the researcher's call."""
    claim_id = uuid4()
    output = {
        "strongest_opposition_refs": [str(uuid4())],
        "falsification_conditions": ["A null effect in a preregistered RCT."],
        "boundary_conditions": ["Western adolescent samples only."],
        "unresolved_conflicts": ["Effect direction across sexes."],
    }

    outcome = await run_joint_modeling(_context(claim_id, output))

    consensus_events = _consensus_events(outcome.events)
    assert len(consensus_events) == 1
    assert consensus_events[0].payload["merge_candidates"] == [
        "Effect direction across sexes."
    ]


async def test_ready_consensus_emits_a_confidence_marker_per_confirmed_claim() -> (
    None
):
    """Plan phase 5: JOINT_MODELING is one of the four Evolution View phase
    boundaries -- a ready consensus gives every confirmed claim a trajectory
    point, qualitative rather than a fabricated numeric delta (CLAUDE.md 16)."""
    claim_id = uuid4()
    output = {
        "strongest_opposition_refs": [str(uuid4())],
        "falsification_conditions": ["A null effect in a preregistered RCT."],
        "boundary_conditions": ["Western adolescent samples only."],
        "unresolved_conflicts": ["Effect direction across sexes."],
    }

    outcome = await run_joint_modeling(_context(claim_id, output))

    markers = _confidence_events(outcome.events)
    assert len(markers) == 1
    assert markers[0].claim_id == claim_id
    assert markers[0].payload["phase"] == TaskPhase.JOINT_MODELING.value
    assert markers[0].payload["confidence_delta_note"] == (
        "联合建模阶段：已形成条件化共识。"
    )


async def test_unready_consensus_emits_no_confidence_marker() -> None:
    """No consensus was actually formed, so there is no trajectory point to
    add -- an honest absence, not a bug."""
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.JOINT_MODELING,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(uuid4(),),
        deliberator=UnavailableDeliberator(),
    )

    outcome = await run_joint_modeling(context)

    assert _confidence_events(outcome.events) == []


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
