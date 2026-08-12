"""Unit coverage for run_evidence_exchange's Resurrect wiring (registry.py).

Design spec 5, mechanism 1 of 3: new evidence that satisfies a quarantined
node's recorded resurrection condition should produce a RESURRECTION_GRANTED
status-change event, per packages/evidence/lifecycle.py's
``check_resurrection_conditions``. This checks the emitted events directly,
without Docker or a real model gateway.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    CONFIDENCE_UPDATED,
    EVIDENCE_PUBLISHED,
    RESURRECTION_GRANTED,
    EmittedEvent,
    PhaseContext,
    run_evidence_exchange,
)
from packages.epistemo.contracts import TaskPhase
from packages.evidence.lifecycle import QuarantinedNode


class _ScriptedDeliberator:
    def __init__(self, output: Mapping[str, object]) -> None:
        self._output = output

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        assert phase is TaskPhase.EVIDENCE_EXCHANGE
        return self._output


def _context(
    output: Mapping[str, object],
    quarantined: tuple[QuarantinedNode, ...] = (),
    confirmed_claims: tuple[UUID, ...] = (),
    available_sources: tuple[Mapping[str, object], ...] = (),
) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.EVIDENCE_EXCHANGE,
        seats=(Seat.EVIDENCE_AUDITOR,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=confirmed_claims,
        deliberator=_ScriptedDeliberator(output),
        carried={"available_sources": available_sources},
        quarantined=quarantined,
    )


def _confidence_events(events: tuple[EmittedEvent, ...]) -> list[EmittedEvent]:
    return [event for event in events if event.event_type == CONFIDENCE_UPDATED]


def _resurrection_events(events: tuple[EmittedEvent, ...]) -> list[EmittedEvent]:
    return [event for event in events if event.event_type == RESURRECTION_GRANTED]


def _node(node_id: UUID) -> QuarantinedNode:
    return QuarantinedNode(
        node_id=node_id,
        reason="single source, no replication",
        attacker="evidence_gate",
        missing_evidence="not recorded",
        resurrection_condition="an independent replication",
    )


async def test_new_evidence_resurrects_a_quarantined_node() -> None:
    node_id = uuid4()
    new_evidence_id = uuid4()
    output = {
        "resurrection_requests": [
            {"node_id": str(node_id), "evidence_refs": [str(new_evidence_id)]}
        ]
    }

    outcome = await run_evidence_exchange(
        _context(output, quarantined=(_node(node_id),))
    )

    events = _resurrection_events(outcome.events)
    assert len(events) == 1
    assert events[0].payload["node_id"] == str(node_id)
    assert events[0].payload["evidence_refs"] == [str(new_evidence_id)]
    assert not any(
        "resurrection" in slot for slot in outcome.unfilled_slots
    )


async def test_empty_evidence_refs_does_not_resurrect() -> None:
    node_id = uuid4()
    output = {"resurrection_requests": [{"node_id": str(node_id), "evidence_refs": []}]}

    outcome = await run_evidence_exchange(
        _context(output, quarantined=(_node(node_id),))
    )

    assert _resurrection_events(outcome.events) == []
    assert any(
        slot == f"EVIDENCE_EXCHANGE:resurrection_condition_not_met:{node_id}"
        for slot in outcome.unfilled_slots
    )


async def test_unknown_node_id_records_unfilled_slot() -> None:
    node_id = uuid4()
    unknown_id = uuid4()
    output = {
        "resurrection_requests": [
            {"node_id": str(unknown_id), "evidence_refs": [str(uuid4())]}
        ]
    }

    outcome = await run_evidence_exchange(
        _context(output, quarantined=(_node(node_id),))
    )

    assert _resurrection_events(outcome.events) == []
    assert any(
        slot == f"EVIDENCE_EXCHANGE:resurrection_unknown_node:{unknown_id}"
        for slot in outcome.unfilled_slots
    )


async def test_malformed_node_id_records_unfilled_slot_not_dropped_silently() -> None:
    output = {
        "resurrection_requests": [
            {"node_id": "not-a-uuid", "evidence_refs": [str(uuid4())]}
        ]
    }
    # A non-empty quarantined tuple is required here: _resurrection_events
    # short-circuits to (), () when nothing at all is quarantined (there is
    # nothing for any request to resurrect), which would otherwise mask the
    # malformed-id parsing branch this test targets.
    outcome = await run_evidence_exchange(
        _context(output, quarantined=(_node(uuid4()),))
    )

    assert _resurrection_events(outcome.events) == []
    assert any(
        slot == "EVIDENCE_EXCHANGE:resurrection_malformed:not-a-uuid"
        for slot in outcome.unfilled_slots
    )


async def test_no_quarantined_nodes_skips_resurrection_entirely() -> None:
    """A fresh task with nothing quarantined has nothing to resurrect -- this
    must not raise even when a seat's output carries no such data at all."""
    output: dict[str, object] = {}

    outcome = await run_evidence_exchange(_context(output, quarantined=()))

    assert _resurrection_events(outcome.events) == []


async def test_published_evidence_emits_a_confidence_marker_per_confirmed_claim() -> (
    None
):
    """Plan phase 5: an Evolution View trajectory point for every confirmed
    claim once evidence actually moves in this round -- not per item, since no
    evidence item carries a claim reference (see the production code's own
    comment on why the scope is the whole confirmed set, not a fabricated
    one-to-one mapping)."""
    claim_id = uuid4()
    other_claim_id = uuid4()
    source_id = uuid4()
    output = {
        "evidence_items": [
            {
                "source_id": str(source_id),
                "anchor_summary": "a preregistered cohort study",
                "level": "A",
            }
        ]
    }

    outcome = await run_evidence_exchange(
        _context(
            output,
            confirmed_claims=(claim_id, other_claim_id),
            available_sources=(
                {"source_id": str(source_id), "title": "Cohort", "level": "A"},
            ),
        )
    )

    markers = _confidence_events(outcome.events)
    assert {marker.claim_id for marker in markers} == {claim_id, other_claim_id}
    for marker in markers:
        assert marker.payload["phase"] == TaskPhase.EVIDENCE_EXCHANGE.value
        # A plain-language count of items published, never a fabricated
        # numeric confidence delta (CLAUDE.md 16).
        assert marker.payload["confidence_delta_note"] == (
            "证据交换阶段新增 1 条已发布证据条目。"
        )


async def test_no_published_evidence_emits_no_confidence_marker() -> None:
    """A round where nothing was published has no trajectory point to add --
    the honest absence, not a bug (CLAUDE.md 7)."""
    claim_id = uuid4()
    output: dict[str, object] = {}

    outcome = await run_evidence_exchange(
        _context(output, confirmed_claims=(claim_id,))
    )

    assert _confidence_events(outcome.events) == []


async def test_evidence_exchange_rejects_malformed_and_unknown_source_ids() -> None:
    known_source_id = uuid4()
    unknown_source_id = uuid4()
    output = {
        "evidence_items": [
            {
                "source_id": str(known_source_id),
                "anchor_summary": "verified anchor",
                "level": "A",
            },
            {
                "source_id": "not-a-uuid",
                "anchor_summary": "model fabrication",
                "level": "A",
            },
            {
                "source_id": str(unknown_source_id),
                "anchor_summary": "unknown source",
                "level": "B",
            },
        ]
    }

    outcome = await run_evidence_exchange(
        _context(
            output,
            available_sources=(
                {
                    "source_id": str(known_source_id),
                    "title": "Verified source",
                    "level": "A",
                },
            ),
        )
    )

    published = [
        event for event in outcome.events if event.event_type == EVIDENCE_PUBLISHED
    ]
    assert len(published) == 1
    assert published[0].payload["items"] == [
        {
            "source_id": str(known_source_id),
            "anchor_summary": "verified anchor",
            "level": "A",
        }
    ]
    assert (
        "EVIDENCE_EXCHANGE:invalid_source_id:evidence_auditor:not-a-uuid"
        in outcome.unfilled_slots
    )
    assert (
        f"EVIDENCE_EXCHANGE:unknown_source_id:evidence_auditor:{unknown_source_id}"
        in outcome.unfilled_slots
    )


async def test_evidence_exchange_carries_only_sanitized_published_evidence() -> None:
    source_id = uuid4()
    output = {
        "evidence_items": [
            {
                "source_id": str(source_id),
                "anchor_summary": "public methodological anchor",
                "level": "A",
                "private_notes": "must not cross the seat boundary",
            }
        ]
    }

    outcome = await run_evidence_exchange(
        _context(
            output,
            available_sources=(
                {"source_id": str(source_id), "title": "Source", "level": "A"},
            ),
        )
    )

    assert outcome.carry == {
        "published_evidence": (
            {
                "seat": Seat.EVIDENCE_AUDITOR.value,
                "source_id": str(source_id),
                "anchor_summary": "public methodological anchor",
                "level": "A",
            },
        )
    }
