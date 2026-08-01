from __future__ import annotations

from uuid import uuid4

import pytest

from packages.memory.fold import fold_process, GraphBoundaryViolation
from packages.memory.process_graph import (
    ProcessEdge,
    ProcessGraphSnapshot,
    ProcessNode,
    ProcessNodeType,
)


def _make_snapshot() -> ProcessGraphSnapshot:
    task = ProcessNode(
        id=uuid4(),
        node_type=ProcessNodeType.TASK,
        payload={"summary": "investigate screen time", "next_need": "replication"},
    )
    tool = ProcessNode(
        id=uuid4(),
        node_type=ProcessNodeType.TOOL_CALL,
        payload={"summary": "found correlation"},
    )
    decision = ProcessNode(
        id=uuid4(),
        node_type=ProcessNodeType.DECISION,
        payload={
            "blindspot": "publication bias",
            "challenge": "confounding",
            "dissent": "null result in subgroup",
        },
    )
    return ProcessGraphSnapshot(
        version=1,
        nodes=(task, tool, decision),
        edges=(
            ProcessEdge(source_id=task.id, target_id=tool.id, kind="caused"),
        ),
    )


def test_fold_preserves_six_backbone_elements() -> None:
    snapshot = _make_snapshot()
    result = fold_process(snapshot, token_budget=800)
    assert result.retention.passed
    assert result.retention.current_task_preserved
    assert result.retention.confirmed_findings_preserved
    assert result.retention.active_blindspots_preserved
    assert result.retention.unresolved_challenges_preserved
    assert result.retention.minority_dissents_preserved
    assert result.retention.next_evidence_needs_preserved


def test_fold_rejects_when_backbone_incomplete() -> None:
    task = ProcessNode(
        id=uuid4(),
        node_type=ProcessNodeType.TASK,
        payload={"summary": "only task"},
    )
    snapshot = ProcessGraphSnapshot(version=1, nodes=(task,), edges=())
    result = fold_process(snapshot, token_budget=800)
    assert result.rejected
    assert not result.retention.passed
