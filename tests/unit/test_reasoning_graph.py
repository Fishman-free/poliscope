"""Dependency-aware process graph: Flush/Fold/Recall and edge re-wiring.

Design doc 1/9 lift MemoBrain's three operations into the council's private
memory. The properties worth pinning are the ones a flat list cannot express:
Flush withholds a route without deleting it, Fold compresses a sub-trajectory
and re-wires its outgoing edges, and Recall reads only the active frontier.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.memory.process_graph import ProcessNodeType
from packages.memory.reasoning_graph import (
    PRODUCES,
    RESPONDS_TO,
    ReasoningGraph,
)


def test_flush_marks_inactive_but_keeps_node() -> None:
    graph = ReasoningGraph()
    task = graph.add_node(ProcessNodeType.TASK, {"summary": "task"})
    dead = graph.add_node(ProcessNodeType.FAILURE_ROUTE, {"summary": "dead end"})

    graph.flush(dead)

    assert graph.node(dead) is not None  # not deleted
    assert graph.node(dead).status == "flushed"
    assert dead not in graph.active_frontier()
    assert task in graph.active_frontier()


def test_fold_compresses_and_rewires_outgoing_edges() -> None:
    graph = ReasoningGraph()
    task = graph.add_node(ProcessNodeType.TASK, {"summary": "task"})
    search = graph.add_node(ProcessNodeType.TOOL_CALL, {"summary": "search"})
    read = graph.add_node(ProcessNodeType.TOOL_CALL, {"summary": "read paper"})
    result = graph.add_node(ProcessNodeType.DECISION, {"summary": "conclusion"})
    graph.add_edge(task, search, PRODUCES)
    graph.add_edge(search, read, PRODUCES)
    graph.add_edge(read, result, PRODUCES)

    summary_id = graph.fold((search, read), "retrieved and read 3 papers")

    # The compressed nodes are folded away from recall but kept for audit.
    assert search not in graph.active_frontier()
    assert read not in graph.active_frontier()
    # The summary node inherited the outgoing edge to the result node.
    assert graph.node(summary_id) is not None
    edges = graph.edges()
    assert any(
        e.source_id == summary_id and e.target_id == result for e in edges
    )
    # The original incoming edge is preserved (provenance).
    assert any(e.source_id == task and e.target_id == search for e in edges)


def test_fold_unknown_node_raises() -> None:
    graph = ReasoningGraph()
    with pytest.raises(KeyError):
        graph.fold((uuid4(),), "summary")


def test_patch_updates_payload() -> None:
    graph = ReasoningGraph()
    node_id = graph.add_node(ProcessNodeType.TASK, {"summary": "task"})
    graph.patch(node_id, next_need="replication")
    assert graph.node(node_id).payload["next_need"] == "replication"


def test_patch_unknown_node_raises() -> None:
    graph = ReasoningGraph()
    with pytest.raises(KeyError):
        graph.patch(uuid4(), summary="x")


def test_active_leaf_nodes_are_open_ends() -> None:
    graph = ReasoningGraph()
    task = graph.add_node(ProcessNodeType.TASK, {"summary": "task"})
    mid = graph.add_node(ProcessNodeType.TOOL_CALL, {"summary": "mid"})
    leaf = graph.add_node(ProcessNodeType.DECISION, {"summary": "leaf"})
    graph.add_edge(task, mid, PRODUCES)
    graph.add_edge(mid, leaf, RESPONDS_TO)

    # task and mid have outgoing edges; only leaf is an open end.
    assert leaf in graph.active_leaf_nodes()
    assert task not in graph.active_leaf_nodes()
    assert mid not in graph.active_leaf_nodes()


def test_snapshot_is_immutable_shape() -> None:
    graph = ReasoningGraph()
    graph.add_node(ProcessNodeType.TASK, {"summary": "task"})
    snapshot = graph.snapshot()
    assert snapshot.version == 1
    assert len(snapshot.nodes) == 1
