from __future__ import annotations

from uuid import UUID

from packages.evidence.projector import GraphEdge, GraphNode


class GraphRepository:
    """In-memory graph store; production uses projector_session."""

    def __init__(self) -> None:
        self._nodes: dict[UUID, GraphNode] = {}
        self._edges: list[GraphEdge] = []

    def add_node(self, node: GraphNode) -> None:
        self._nodes[node.node_id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        self._edges.append(edge)

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)

    def outgoing_edges(
        self, node_id: UUID, edge_type: str | None = None
    ) -> tuple[UUID, ...]:
        return tuple(
            e.target_node_id
            for e in self._edges
            if e.source_node_id == node_id
            and (edge_type is None or e.edge_type == edge_type)
        )
