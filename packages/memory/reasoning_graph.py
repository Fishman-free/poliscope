"""Dependency-aware process graph: MemoBrain's core substrate, adapted for a council.

Design doc 3/4 (``thought.docx``) keeps *two* graphs apart: the Process Graph
answers "how did a scientist arrive at this judgment" and the Evidence Graph
answers "what do we currently know". This module is the Process Graph side: a
mutable, dependency-aware graph that records each seat's exploration, tool
calls, failures, challenges and decisions, and supports the three MemoBrain
operations the design doc lifts from upstream -- Flush (mark a route inactive,
never delete), Fold (compress a finished sub-trajectory into a summary node and
re-wire its dependencies), and Recall (reconstruct a compact working context
from the still-active frontier).

The graph is *mutable* on purpose: it is a running seat's working memory, not a
frozen contract. The immutable
:class:`~packages.memory.process_graph.ProcessGraphSnapshot` is produced on
demand for fold/snapshot, so the rest of the system still sees
the frozen shape it was built around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.kernel.contracts import FrozenDict
from packages.memory.process_graph import (
    ProcessEdge,
    ProcessGraphSnapshot,
    ProcessNode,
    ProcessNodeType,
)

# Edge kinds a process edge may carry, matching design doc 4's process-edge
# vocabulary (DEPENDS_ON / DECOMPOSES_TO / PRODUCES / RESPONDS_TO / SUPERSEDES).
DEPENDS_ON = "depends_on"
DECOMPOSES_TO = "decomposes_to"
PRODUCES = "produces"
RESPONDS_TO = "responds_to"
SUPERSEDES = "supersedes"


@dataclass(slots=True)
class GraphNode:
    """One mutable node in the running graph, keyed by a stable id.

    ``status`` is the MemoBrain lifecycle: ``active`` feeds the working context,
    ``flushed`` is kept for audit but withheld from recall, ``folded`` means the
    node's content has been collapsed into a summary node that now owns its
    dependencies. A node is never physically removed (CLAUDE.md 6: a flushed or
    folded route stays traceable).

    ``status`` and ``payload`` are deliberately mutable (``slots=True``, not
    ``frozen=True``): the running graph is a seat's live working memory, and
    Flush/Fold mutate status while ``patch`` mutates payload. The immutable
    :class:`~packages.memory.process_graph.ProcessGraphSnapshot` is produced on
    demand for the frozen consumers.
    """

    id: UUID
    node_type: ProcessNodeType
    payload: dict[str, object] = field(default_factory=dict)
    status: str = "active"


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source_id: UUID
    target_id: UUID
    kind: str


class ReasoningGraph:
    """A dependency-aware process graph for one private brain.

    Thin over two dicts/lists, but the operations encode the MemoBrain
    semantics the design doc wants: Flush marks instead of deletes, Fold
    compresses a sub-trajectory and re-wires edges so the summary node inherits
    the compressed nodes' dependents, and the active frontier is the Recall
    input (not the whole raw transcript).
    """

    def __init__(self) -> None:
        self._nodes: dict[UUID, GraphNode] = {}
        self._edges: list[GraphEdge] = []

    # -- construction -----------------------------------------------------

    def add_node(
        self,
        node_type: ProcessNodeType,
        payload: dict[str, object] | None = None,
        *,
        node_id: UUID | None = None,
    ) -> UUID:
        node_id = node_id or uuid4()
        self._nodes[node_id] = GraphNode(
            id=node_id, node_type=node_type, payload=dict(payload or {})
        )
        return node_id

    def add_edge(self, source_id: UUID, target_id: UUID, kind: str) -> None:
        if source_id not in self._nodes or target_id not in self._nodes:
            raise KeyError("edge endpoints must exist before wiring")
        self._edges.append(
            GraphEdge(source_id=source_id, target_id=target_id, kind=kind)
        )

    def patch(self, node_id: UUID, **fields: object) -> None:
        """Incremental update to one node's payload (design doc 9's ``patch``).

        Raises ``KeyError`` for an unknown node so a patch to a flushed/absent
        route surfaces instead of silently writing into nothing.
        """
        if node_id not in self._nodes:
            raise KeyError(f"unknown node {node_id!r}")
        self._nodes[node_id].payload.update(fields)

    # -- MemoBrain operations --------------------------------------------

    def flush(self, node_id: UUID) -> None:
        """Mark a node inactive (Flush), never delete it.

        A flushed node stays in ``_nodes`` for audit but is excluded from the
        active frontier, so Recall does not replay a failed or superseded route
        (design doc 1: MemoBrain Flush = mark, not delete; and design doc 2: a
        disfavoured scientific view is quarantined in the *Evidence* graph, not
        dropped here).
        """
        if node_id not in self._nodes:
            raise KeyError(f"unknown node {node_id!r}")
        self._nodes[node_id].status = "flushed"

    def fold(self, node_ids: tuple[UUID, ...], summary: str) -> UUID:
        """Compress a finished sub-trajectory into a summary node (Fold).

        The summary node inherits every outgoing edge of the compressed nodes
        (re-wiring), so dependents of a compressed route keep pointing at the
        compacted survivor rather than at the dead intermediates. Incoming edges
        are left in place so provenance is still traceable backwards. The
        compressed nodes are marked ``folded`` and withheld from recall.
        """
        for node_id in node_ids:
            if node_id not in self._nodes:
                raise KeyError(f"unknown node {node_id!r}")
        summary_id = self.add_node(
            ProcessNodeType.TASK,
            {"summary": summary, "folded_from": [str(i) for i in node_ids]},
        )
        # Re-wire: every edge that left a compressed node now leaves the summary.
        rewired: list[GraphEdge] = []
        for edge in self._edges:
            if edge.source_id in node_ids:
                rewired.append(
                    GraphEdge(
                        source_id=summary_id,
                        target_id=edge.target_id,
                        kind=edge.kind,
                    )
                )
            else:
                rewired.append(edge)
        self._edges = rewired
        for node_id in node_ids:
            self._nodes[node_id].status = "folded"
        return summary_id

    # -- queries ----------------------------------------------------------

    def active_frontier(self) -> tuple[UUID, ...]:
        """The still-active nodes, in insertion order, for Recall.

        Flushed and folded nodes are excluded: Recall rebuilds a compact context
        from the frontier, not a growing transcript (design doc 1).
        """
        return tuple(
            node_id
            for node_id, node in self._nodes.items()
            if node.status == "active"
        )

    def active_leaf_nodes(self) -> tuple[UUID, ...]:
        """Active nodes with no active outgoing edge -- the open ends of the
        current investigation, which is what "what is still unresolved" means
        for one private brain."""
        outgoing = {edge.source_id for edge in self._edges}
        return tuple(
            node_id
            for node_id in self.active_frontier()
            if node_id not in outgoing
        )

    def node(self, node_id: UUID) -> GraphNode | None:
        return self._nodes.get(node_id)

    def edges(self) -> tuple[GraphEdge, ...]:
        return tuple(self._edges)

    def __len__(self) -> int:
        return len(self._nodes)

    # -- snapshot ---------------------------------------------------------

    def snapshot(self, version: int = 1) -> ProcessGraphSnapshot:
        """Export the immutable, frozen shape the rest of the system consumes."""
        nodes = tuple(
            ProcessNode(
                id=node.id,
                node_type=node.node_type,
                payload=FrozenDict(node.payload),
            )
            for node in self._nodes.values()
        )
        edges = tuple(
            ProcessEdge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                kind=edge.kind,
            )
            for edge in self._edges
        )
        return ProcessGraphSnapshot(version=version, nodes=nodes, edges=edges)


__all__ = [
    "DEPENDS_ON",
    "DECOMPOSES_TO",
    "PRODUCES",
    "RESPONDS_TO",
    "SUPERSEDES",
    "GraphEdge",
    "GraphNode",
    "ReasoningGraph",
]
