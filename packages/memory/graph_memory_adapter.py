"""MemoBrain-backed private memory: one :class:`ReasoningGraph` per agent.

This replaces the flat ``list[Episode]`` stand-in with a dependency-aware
graph, so each seat's private brain records *how* it arrived at a judgment --
exploration, tool calls, failures, challenges, decisions -- and can Flush a dead
route, Fold a finished sub-trajectory, and Recall a compact frontier, exactly
the operations design doc 1/9 lift from upstream MemoBrain. The public
:class:`~packages.memory.contracts.MemoryAdapter` protocol is unchanged, so the
council and the evaluation harness keep working against the same shape.
"""

from __future__ import annotations

from uuid import UUID

from packages.memory.contracts import Episode, RecallResult
from packages.memory.process_graph import ProcessNodeType
from packages.memory.reasoning_graph import (
    PRODUCES,
    ReasoningGraph,
)


def _node_type_for_kind(kind: str) -> ProcessNodeType:
    """Map an episode kind to the closest process-node type.

    ``kind`` is the free-form label the council passes; the mapping is a
    best-effort lift so recall/fold can treat a tool call differently from a
    decision without inventing a second vocabulary.
    """
    lowered = kind.lower()
    if lowered in {"tool", "tool_call", "search", "retrieval"}:
        return ProcessNodeType.TOOL_CALL
    if lowered in {"challenge", "challenged"}:
        return ProcessNodeType.CHALLENGE
    if lowered in {"decision", "decided", "verdict"}:
        return ProcessNodeType.DECISION
    if lowered in {"assignment", "assigned"}:
        return ProcessNodeType.ASSIGNMENT
    if lowered in {"debate", "cross_exam"}:
        return ProcessNodeType.DEBATE
    if lowered in {"failure", "failed", "dead_end"}:
        return ProcessNodeType.FAILURE_ROUTE
    return ProcessNodeType.TASK


class GraphMemoryAdapter:
    """Per-agent dependency-aware private memory."""

    def __init__(self) -> None:
        self._graphs: dict[str, ReasoningGraph] = {}

    async def init_private_memory(self, agent_id: str, task: str) -> None:
        graph = ReasoningGraph()
        graph.add_node(ProcessNodeType.TASK, {"summary": task})
        self._graphs[agent_id] = graph

    async def memorize_episode(self, agent_id: str, episode: Episode) -> None:
        graph = self._graphs.get(agent_id)
        if graph is None:
            raise KeyError(f"agent {agent_id!r} not initialized")
        node_id = graph.add_node(
            _node_type_for_kind(episode.kind),
            {"summary": episode.summary},
        )
        # Link the new episode to the current frontier so the graph stays a
        # connected reasoning trace, not a bag of nodes. The most recent active
        # node is the natural "previous step" for the next episode.
        frontier = graph.active_frontier()
        if len(frontier) >= 2:
            previous = frontier[-2] if frontier[-1] == node_id else frontier[-1]
            graph.add_edge(previous, node_id, PRODUCES)

    async def recall_private(self, agent_id: str, token_budget: int) -> RecallResult:
        graph = self._graphs.get(agent_id)
        if graph is None:
            raise KeyError(f"agent {agent_id!r} not initialized")
        # Recall the active frontier only: flushed/folded routes are withheld
        # so a seat's context stays a compact scientific skeleton rather than a
        # replay of everything it ever did. Fold aggressively into one summary
        # when the frontier would overflow the budget, so recall never exceeds
        # the caller's character cap.
        frontier = graph.active_frontier()
        parts: list[str] = []
        for node_id in frontier:
            node = graph.node(node_id)
            if node is None:
                continue
            summary = str(node.payload.get("summary", ""))
            if not summary:
                continue
            label = (
                f"{node.node_type.value}:{summary}"
                if node.node_type is not ProcessNodeType.TASK
                else summary
            )
            parts.append(label)
        text = " ".join(parts)[:token_budget]
        return RecallResult(text=text)

    async def save_snapshot(self, agent_id: str) -> dict[str, object]:
        graph = self._graphs.get(agent_id)
        if graph is None:
            raise KeyError(f"agent {agent_id!r} not initialized")
        snapshot = graph.snapshot()
        return {
            "nodes": [
                {
                    "id": str(node.id),
                    "node_type": node.node_type.value,
                    "payload": dict(node.payload),
                }
                for node in snapshot.nodes
            ],
            "edges": [
                {
                    "source_id": str(e.source_id),
                    "target_id": str(e.target_id),
                    "kind": e.kind,
                }
                for e in snapshot.edges
            ],
        }

    async def load_snapshot(self, agent_id: str, snapshot: dict[str, object]) -> None:
        raw_nodes = snapshot.get("nodes")
        if not isinstance(raw_nodes, (list, tuple)):
            raise ValueError(f"snapshot for {agent_id!r} has no node list")
        graph = ReasoningGraph()
        for item in raw_nodes:
            if not isinstance(item, dict):
                continue
            graph.add_node(
                ProcessNodeType(str(item.get("node_type", "Task"))),
                dict(item.get("payload", {})),
                node_id=_parse_uuid(item.get("id")),
            )
        raw_edges = snapshot.get("edges", [])
        if isinstance(raw_edges, (list, tuple)):
            for item in raw_edges:
                if not isinstance(item, dict):
                    continue
                try:
                    graph.add_edge(
                        _parse_uuid(item.get("source_id")),
                        _parse_uuid(item.get("target_id")),
                        str(item.get("kind", PRODUCES)),
                    )
                except KeyError:
                    # A malformed snapshot with a dangling edge must not restore
                    # a broken graph; skip the edge but keep the nodes.
                    continue
        self._graphs[agent_id] = graph


def _parse_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


__all__ = ["GraphMemoryAdapter"]
