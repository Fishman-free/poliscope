from __future__ import annotations

from uuid import uuid4

from packages.memory.process_graph import (
    ProcessEdge,
    ProcessGraphSnapshot,
    ProcessNode,
    ProcessNodeType,
)


def test_process_graph_supports_poliscope_nodes() -> None:
    assert ProcessNodeType.DEBATE.value == "Debate"
    assert ProcessNodeType.DECISION.value == "Decision"
    assert ProcessNodeType.ASSIGNMENT.value == "Assignment"


def test_process_graph_snapshot_is_immutable() -> None:
    node = ProcessNode(
        id=uuid4(),
        node_type=ProcessNodeType.DEBATE,
        payload={"topic": "causation"},
    )
    edge = ProcessEdge(source_id=node.id, target_id=uuid4(), kind="responds_to")
    snapshot = ProcessGraphSnapshot(version=1, nodes=(node,), edges=(edge,))
    assert len(snapshot.nodes) == 1
    assert snapshot.nodes[0].node_type == ProcessNodeType.DEBATE
