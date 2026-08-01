from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from packages.evidence.ledger import EventLedger, EventNotAdmitted


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: UUID
    task_id: UUID
    node_type: str
    payload: dict[str, object]
    status: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source_node_id: UUID
    target_node_id: UUID
    edge_type: str


class GraphProjector:
    """Projects admitted events into the Evidence Graph atomically."""

    def __init__(self, ledger: EventLedger) -> None:
        self._ledger = ledger
        self._nodes: dict[UUID, GraphNode] = {}
        self._edges: list[GraphEdge] = []
        self._last_sequence = 0

    @property
    def nodes(self) -> tuple[GraphNode, ...]:
        return tuple(self._nodes.values())

    @property
    def edges(self) -> tuple[GraphEdge, ...]:
        return tuple(self._edges)

    def project(self, event_id: UUID) -> None:
        entry = self._ledger.get(event_id)
        if entry is None:
            raise EventNotAdmitted(f"unknown event: {event_id}")
        if entry.status != "admitted":
            raise EventNotAdmitted(
                f"event {event_id} has status {entry.status!r}, not admitted"
            )
        if entry.sequence <= self._last_sequence:
            return
        node = GraphNode(
            node_id=entry.event_id,
            task_id=entry.task_id,
            node_type=entry.event_type,
            payload=entry.payload,
            status="active",
        )
        self._nodes[entry.event_id] = node
        self._last_sequence = entry.sequence

    def project_finding_with_source(
        self, finding_id: UUID, source_id: UUID, task_id: UUID
    ) -> None:
        """Atomically add a Finding node and its DERIVED_FROM Source edge."""
        finding_node = GraphNode(
            node_id=finding_id,
            task_id=task_id,
            node_type="StudyFinding",
            payload={},
            status="active",
        )
        source_node = GraphNode(
            node_id=source_id,
            task_id=task_id,
            node_type="Source",
            payload={},
            status="active",
        )
        self._nodes[finding_id] = finding_node
        self._nodes[source_id] = source_node
        self._edges.append(
            GraphEdge(
                source_node_id=finding_id,
                target_node_id=source_id,
                edge_type="DERIVED_FROM",
            )
        )
