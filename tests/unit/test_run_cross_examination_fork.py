"""Unit coverage for run_cross_examination's Fork wiring (registry.py).

Design spec 5, mechanism 2 of 3: a fatal challenge that cannot be reconciled by
QUALIFY should produce a parallel Claim node linked back to the challenged claim
by the existing CONTRADICTS edge, rather than the disagreement disappearing
(CLAUDE.md 4). This checks the emitted events directly, without Docker or a
real model gateway -- a hand-written fake deliberator plays one seat's
CROSS_EXAMINATION output.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    CHALLENGE_RAISED,
    CONFIDENCE_UPDATED,
    EmittedEvent,
    PhaseContext,
    run_cross_examination,
)
from packages.epistemo.contracts import TaskPhase
from packages.evidence.contracts import EvidenceEdgeType, EvidenceNodeType


def _confidence_events(events: tuple[EmittedEvent, ...]) -> list[EmittedEvent]:
    return [event for event in events if event.event_type == CONFIDENCE_UPDATED]


class _ScriptedDeliberator:
    def __init__(self, output: Mapping[str, object]) -> None:
        self._output = output

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        assert phase is TaskPhase.CROSS_EXAMINATION
        return self._output


def _context(claim_id: UUID, output: Mapping[str, object]) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.CROSS_EXAMINATION,
        seats=(Seat.ADVERSARY_FALSIFIER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(claim_id,),
        deliberator=_ScriptedDeliberator(output),
    )


def _claim_events(events: tuple[EmittedEvent, ...]) -> list[EmittedEvent]:
    return [
        event for event in events if event.event_type == EvidenceNodeType.CLAIM.value
    ]


async def test_fatal_challenge_with_fork_produces_anchor_and_fork_claims() -> None:
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The correlation vanishes in the replication sample.",
                "is_fatal": True,
                "fork": {
                    "statement": "Under high baseline anxiety, the effect reverses.",
                    "falsification_condition": (
                        "No reversal in a preregistered subgroup test."
                    ),
                },
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    claim_events = _claim_events(outcome.events)
    assert len(claim_events) == 2

    anchor = next(event for event in claim_events if event.claim_id == claim_id)
    assert anchor.payload["claim_type"] == "correlational"
    assert anchor.payload["scope"] == {}
    assert "edges" not in anchor.payload

    forked = next(event for event in claim_events if event.claim_id != claim_id)
    assert forked.payload["statement"] == (
        "Under high baseline anxiety, the effect reverses."
    )
    assert forked.payload["edges"] == [
        {"type": EvidenceEdgeType.CONTRADICTS.value, "target": str(claim_id)}
    ]

    # The original challenge is still recorded -- forking augments it, it does
    # not replace it (CLAUDE.md 4 forbids a challenge disappearing).
    challenge_events = [e for e in outcome.events if e.event_type == CHALLENGE_RAISED]
    assert len(challenge_events) == 1
    blocked = outcome.carry["blocked_claim_ids"]
    assert isinstance(blocked, (list, tuple, set))
    assert str(claim_id) in blocked


async def test_fork_is_deterministic_across_replay() -> None:
    """A resumed run must reach the same fork claim id, or a replay would mint
    a second parallel node for the same disagreement."""
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The correlation vanishes in the replication sample.",
                "is_fatal": True,
                "fork": {"statement": "Under high baseline anxiety, it reverses."},
            }
        ]
    }
    context = _context(claim_id, output)

    first = await run_cross_examination(context)
    second = await run_cross_examination(context)

    def fork_claim_id(outcome_events: tuple[EmittedEvent, ...]) -> UUID | None:
        for event in _claim_events(outcome_events):
            if event.claim_id != claim_id:
                return event.claim_id
        return None

    assert fork_claim_id(first.events) == fork_claim_id(second.events)


async def test_non_fatal_challenge_with_fork_does_not_fork() -> None:
    """Fork requires the seat to also report the challenge as fatal -- the same
    self-reported signal that already gates ``blocked_claim_ids``."""
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The effect may be smaller than reported.",
                "is_fatal": False,
                "fork": {"statement": "An alternative reading of the same data."},
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    assert _claim_events(outcome.events) == []


async def test_fatal_challenge_without_fork_stays_a_plain_challenge() -> None:
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The correlation vanishes in the replication sample.",
                "is_fatal": True,
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    assert _claim_events(outcome.events) == []
    blocked = outcome.carry["blocked_claim_ids"]
    assert isinstance(blocked, (list, tuple, set))
    assert str(claim_id) in blocked


async def test_fork_with_empty_statement_is_ignored() -> None:
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The correlation vanishes in the replication sample.",
                "is_fatal": True,
                "fork": {"statement": ""},
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    assert _claim_events(outcome.events) == []


async def test_fork_with_self_reported_causal_design_tags_the_forked_claim() -> None:
    """Phase 4: a seat that self-reports a causal design gets a real causal
    claim_type and study_design on the forked claim, so
    score_causal_overclaim finally has something to evaluate -- the anchor
    (the pre-existing claim, not asserted here) is untouched."""
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The correlation vanishes in the replication sample.",
                "is_fatal": True,
                "fork": {
                    "statement": "The randomized trial shows the effect is causal.",
                    "claim_type": "causal",
                    "study_design": "experimental",
                },
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    claim_events = _claim_events(outcome.events)
    anchor = next(event for event in claim_events if event.claim_id == claim_id)
    assert anchor.payload["claim_type"] == "correlational"

    forked = next(event for event in claim_events if event.claim_id != claim_id)
    assert forked.payload["claim_type"] == "causal"
    assert forked.payload["study_design"] == "experimental"


async def test_fork_without_self_reported_claim_type_stays_correlational() -> None:
    """Existing correlational-fork path is unaffected: no claim_type/
    study_design in the fork mapping still yields the old default, and no
    study_design key is fabricated onto the payload."""
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The correlation vanishes in the replication sample.",
                "is_fatal": True,
                "fork": {
                    "statement": "Under high baseline anxiety, the effect reverses."
                },
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    forked = next(
        event
        for event in _claim_events(outcome.events)
        if event.claim_id != claim_id
    )
    assert forked.payload["claim_type"] == "correlational"
    assert "study_design" not in forked.payload


async def test_challenge_and_fork_each_emit_a_confidence_marker() -> None:
    """Plan phase 5: a fatal challenge gets one Evolution View marker against
    the challenged claim, and its fork (when produced) gets its own separate
    marker against the forked claim -- two distinct trajectory points, not one
    conflated marker."""
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The correlation vanishes in the replication sample.",
                "is_fatal": True,
                "fork": {
                    "statement": "Under high baseline anxiety, the effect reverses."
                },
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    markers = _confidence_events(outcome.events)
    assert len(markers) == 2

    challenge_marker = next(m for m in markers if m.claim_id == claim_id)
    assert challenge_marker.payload["phase"] == TaskPhase.CROSS_EXAMINATION.value
    note = challenge_marker.payload["confidence_delta_note"]
    assert isinstance(note, str) and "致命" in note

    forked_claim_id = next(
        event.claim_id
        for event in _claim_events(outcome.events)
        if event.claim_id != claim_id
    )
    fork_marker = next(m for m in markers if m.claim_id == forked_claim_id)
    fork_note = fork_marker.payload["confidence_delta_note"]
    assert isinstance(fork_note, str) and "分支主张" in fork_note


async def test_non_fatal_challenge_still_emits_its_own_confidence_marker() -> None:
    """Even a non-fatal challenge is a real trajectory point -- it just never
    forks."""
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The effect may be smaller than reported.",
                "is_fatal": False,
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    markers = _confidence_events(outcome.events)
    assert len(markers) == 1
    assert markers[0].claim_id == claim_id
    note = markers[0].payload["confidence_delta_note"]
    assert isinstance(note, str) and "非致命" in note


async def test_fork_with_unrecognised_claim_type_falls_back_to_correlational() -> None:
    """An unparseable claim_type string is treated as unknown, not guessed as
    causal (CLAUDE.md 7) -- score_causal_overclaim must never be fed a
    fabricated causal claim."""
    claim_id = uuid4()
    output = {
        "challenges": [
            {
                "claim_id": str(claim_id),
                "statement": "The correlation vanishes in the replication sample.",
                "is_fatal": True,
                "fork": {
                    "statement": "Some alternative reading.",
                    "claim_type": "not-a-real-claim-type",
                },
            }
        ]
    }

    outcome = await run_cross_examination(_context(claim_id, output))

    forked = next(
        event
        for event in _claim_events(outcome.events)
        if event.claim_id != claim_id
    )
    assert forked.payload["claim_type"] == "correlational"
