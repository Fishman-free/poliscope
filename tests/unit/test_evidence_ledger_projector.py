from __future__ import annotations

from uuid import uuid4

import pytest

from packages.evidence.ledger import EventConflict, EventLedger, EventNotAdmitted
from packages.evidence.projector import GraphProjector


@pytest.fixture
def ledger() -> EventLedger:
    return EventLedger()


@pytest.fixture
def projector(ledger: EventLedger) -> GraphProjector:
    return GraphProjector(ledger)


def test_ledger_idempotency_returns_same_entry(ledger: EventLedger) -> None:
    task_id = uuid4()
    first = ledger.append(task_id, "claim", {"x": 1}, "key-1")
    second = ledger.append(task_id, "claim", {"x": 1}, "key-1")
    assert first.event_id == second.event_id


def test_ledger_conflict_on_different_payload_same_key(
    ledger: EventLedger,
) -> None:
    task_id = uuid4()
    ledger.append(task_id, "claim", {"x": 1}, "key-1")
    with pytest.raises(EventConflict):
        ledger.append(task_id, "claim", {"x": 2}, "key-1")


def test_projector_refuses_pending_event(
    ledger: EventLedger, projector: GraphProjector
) -> None:
    entry = ledger.append(uuid4(), "claim", {}, "pending-key")
    with pytest.raises(EventNotAdmitted):
        projector.project(entry.event_id)
    assert len(projector.nodes) == 0


def test_projector_accepts_admitted_event(
    ledger: EventLedger, projector: GraphProjector
) -> None:
    entry = ledger.append(uuid4(), "claim", {}, "admit-key", status="admitted")
    projector.project(entry.event_id)
    assert len(projector.nodes) == 1
    assert projector.nodes[0].node_type == "claim"


def test_project_finding_with_source_is_atomic() -> None:
    projector = GraphProjector(EventLedger())
    finding_id = uuid4()
    source_id = uuid4()
    projector.project_finding_with_source(finding_id, source_id, uuid4())
    assert len(projector.nodes) == 2
    assert len(projector.edges) == 1
    edge = projector.edges[0]
    assert edge.edge_type == "DERIVED_FROM"
    assert edge.source_node_id == finding_id
    assert edge.target_node_id == source_id


def test_projector_idempotent_for_sequence(
    ledger: EventLedger, projector: GraphProjector
) -> None:
    entry = ledger.append(uuid4(), "claim", {}, "seq-key", status="admitted")
    projector.project(entry.event_id)
    projector.project(entry.event_id)
    assert len(projector.nodes) == 1
