from __future__ import annotations

from datetime import datetime
from uuid import UUID

from packages.kernel.contracts import ContractModel, FrozenDict


class SafetyNotice(ContractModel):
    classification: str = "科研辅助工具"
    medical_disclaimer: str = (
        "本系统不提供医学诊断或医疗建议。"
        "统计不确定性不等于临床确定性。"
    )
    limitations: str = (
        "输出为 AI 辅助科研综述，"
        "不替代同行评审或专业判断。"
    )


class WorkspaceSnapshot(ContractModel):
    task: FrozenDict[str, object]
    brief: FrozenDict[str, object]
    seats: tuple[FrozenDict[str, object], ...]
    graph: FrozenDict[str, object]
    blindspots: tuple[FrozenDict[str, object], ...]
    discriminating_studies: tuple[FrozenDict[str, object], ...]
    dissents: tuple[FrozenDict[str, object], ...]
    evolution: tuple[FrozenDict[str, object], ...]
    paper_count: int
    independent_cluster_count: int
    workspace_version: int
    safety_notice: SafetyNotice


class SSEEvent(ContractModel):
    event_id: str
    task_id: str
    kind: str
    workspace_version: int
    payload: FrozenDict[str, object]

    def format_sse(self) -> str:
        import json
        data = json.dumps(
            {
                "event_id": self.event_id,
                "task_id": self.task_id,
                "kind": self.kind,
                "workspace_version": self.workspace_version,
                "payload": dict(self.payload),
            },
            ensure_ascii=False,
        )
        return f"id: {self.event_id}\nevent: {self.kind}\ndata: {data}\n\n"


class CreateTaskRequest(ContractModel):
    question: str
    scope: FrozenDict[str, object]
    budget: FrozenDict[str, object]
    user_evidence: FrozenDict[str, object]


class ConfirmClaimsRequest(ContractModel):
    claim_ids: tuple[UUID, ...]


class TaskResponse(ContractModel):
    id: UUID
    question: str
    status: str
    created_by: str
    created_at: datetime | None = None
