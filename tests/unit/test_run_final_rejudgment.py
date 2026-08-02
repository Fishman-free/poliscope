"""Unit coverage for run_final_rejudgment's DissentCertificate wiring (registry.py).

CLAUDE.md 4 forbids a dissent from being silently dropped, and CLAUDE.md 16's
acceptance criterion 10 requires at least one DissentCertificate to be
produced. This module checks the three branches of that wiring without Docker
or a real model gateway: a hand-written fake deliberator plays each seat's
FINAL_REJUDGMENT output.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    EmittedEvent,
    PhaseContext,
    run_final_rejudgment,
)
from packages.epistemo.contracts import TaskPhase
from packages.evidence.contracts import EvidenceNodeType


class _ScriptedDeliberator:
    def __init__(self, outputs: Mapping[Seat, Mapping[str, object]]) -> None:
        self._outputs = outputs

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        assert phase is TaskPhase.FINAL_REJUDGMENT
        return self._outputs.get(seat)


def _context(
    claim_ids: tuple[UUID, ...],
    outputs: Mapping[Seat, Mapping[str, object]],
) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.FINAL_REJUDGMENT,
        seats=tuple(outputs),
        question="Does screen time affect wellbeing?",
        confirmed_claims=claim_ids,
        deliberator=_ScriptedDeliberator(outputs),
    )


def _dissent_events(events: tuple[EmittedEvent, ...]) -> list[EmittedEvent]:
    return [
        event
        for event in events
        if event.event_type == EvidenceNodeType.DISSENT_CERTIFICATE.value
    ]


async def test_dissenting_seat_with_a_confirmed_claim_produces_a_certificate() -> None:
    claim_id = uuid4()
    outputs = {
        Seat.ADVERSARY_FALSIFIER: {
            "final_judgment": "I dissent: the causal claim is unsupported."
        },
        Seat.THEORY_BUILDER: {"final_judgment": "narrowed, not withdrawn"},
    }

    outcome = await run_final_rejudgment(_context((claim_id,), outputs))

    certificates = _dissent_events(outcome.events)
    assert len(certificates) == 1
    event = certificates[0]
    assert event.evidence_level == "A"
    assert event.payload["author"] == Seat.ADVERSARY_FALSIFIER.value
    assert event.payload["target_id"] == str(claim_id)
    assert event.payload["statement"] == (
        "I dissent: the causal claim is unsupported."
    )
    assert isinstance(event.payload["reason"], str) and event.payload["reason"]
    assert "FINAL_REJUDGMENT:no_dissent_target" not in outcome.unfilled_slots
    judgment_events = [
        e for e in outcome.events if e.event_type == "FINAL_JUDGMENT"
    ]
    dissenting = [
        e
        for e in judgment_events
        if e.payload["seat"] == Seat.ADVERSARY_FALSIFIER.value
    ]
    assert dissenting[0].payload["has_dissent"] is True


async def test_dissenting_seat_with_no_confirmed_claims_records_unfilled_slot() -> None:
    outputs = {
        Seat.ADVERSARY_FALSIFIER: {
            "final_judgment": "I dissent: the causal claim is unsupported."
        },
    }

    outcome = await run_final_rejudgment(_context((), outputs))

    assert _dissent_events(outcome.events) == []
    assert "FINAL_REJUDGMENT:no_dissent_target" in outcome.unfilled_slots


async def test_no_dissenting_seats_produces_no_certificate_and_no_slot() -> None:
    claim_id = uuid4()
    outputs = {
        Seat.THEORY_BUILDER: {"final_judgment": "narrowed, not withdrawn"},
        Seat.CAUSAL_SCIENTIST: {"final_judgment": "confirmed with lower confidence"},
    }

    outcome = await run_final_rejudgment(_context((claim_id,), outputs))

    assert _dissent_events(outcome.events) == []
    assert "FINAL_REJUDGMENT:no_dissent_target" not in outcome.unfilled_slots
