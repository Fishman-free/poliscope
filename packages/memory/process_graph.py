from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from packages.kernel.contracts import ContractModel, FrozenDict


class ProcessNodeType(StrEnum):
    TASK = "Task"
    TOOL_CALL = "ToolCall"
    FAILURE_ROUTE = "FailureRoute"
    CHALLENGE = "Challenge"
    DECISION = "Decision"
    ASSIGNMENT = "Assignment"
    DEBATE = "Debate"


class ProcessNode(ContractModel):
    id: UUID
    node_type: ProcessNodeType
    payload: FrozenDict[str, object]


class ProcessEdge(ContractModel):
    source_id: UUID
    target_id: UUID
    kind: str


class ProcessGraphSnapshot(ContractModel):
    version: int
    nodes: tuple[ProcessNode, ...]
    edges: tuple[ProcessEdge, ...]
