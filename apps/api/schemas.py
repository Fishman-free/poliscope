from __future__ import annotations

from datetime import datetime
from uuid import UUID

from packages.kernel.contracts import (
    ContractModel,
    FrozenDict,
    thaw_for_serialization,
)


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
        """Render one frame.

        Deliberately no ``event:`` line. SSE dispatches a typed frame *only* to
        listeners registered for that exact type, so putting the event kind
        there meant any client had to enumerate every kind the backend can emit
        -- and silently dropped the ones it had not heard of. An audit trail
        that quietly omits event types it does not recognise is the one thing
        this stream must never do (CLAUDE.md 7, 11).

        Untyped frames all arrive on the default channel, so nothing can be
        lost. ``kind`` is in the body, so filtering is still possible; it is now
        the client's choice rather than the wire's silent default.
        """
        import json

        # thaw_for_serialization, not dict(): the payload is frozen recursively,
        # so a nested object arrives as a FrozenDict inside a tuple and
        # json.dumps raises on it. That raise happened mid-stream, inside the
        # response generator, so the browser saw the connection simply end --
        # the audit trail stopped at the first event with a nested payload and
        # said nothing. A shallow dict() call is not enough here.
        data = json.dumps(
            {
                "event_id": self.event_id,
                "task_id": self.task_id,
                "kind": self.kind,
                "workspace_version": self.workspace_version,
                "payload": thaw_for_serialization(self.payload),
            },
            ensure_ascii=False,
        )
        return f"id: {self.event_id}\ndata: {data}\n\n"


class CreateTaskRequest(ContractModel):
    question: str
    scope: FrozenDict[str, object]
    budget: FrozenDict[str, object]
    user_evidence: FrozenDict[str, object]
    # Optional per-task model endpoint: {"base_url", "api_key", "model_name?"}.
    # Validated by TaskModelConfig in packages/research/contracts.py; the key
    # is stored on the task row and never returned by any endpoint.
    task_model_config: FrozenDict[str, object] | None = None
    # Optional knowledge base whose documents the council should treat as
    # Level A user-provided sources; the router validates the id exists.
    knowledge_base_id: UUID | None = None
    # Skills the researcher enabled for this task (migration 0013); the
    # router validates every id belongs to the calling account.
    skill_ids: tuple[UUID, ...] = ()


class ConfirmClaimsRequest(ContractModel):
    claim_ids: tuple[UUID, ...]


class CouncilGuidanceRequest(ContractModel):
    """Plan phase 8.2: the human's advisory steer at the JOINT_MODELING gate.

    Deliberately allows the empty string as a first-class value -- CLAUDE.md
    4/8 make this an advisory-only steer, never a vote, so "no intervention,
    just continue" is a complete, honest answer rather than a missing field.
    """

    guidance_text: str = ""


class TaskResponse(ContractModel):
    id: UUID
    question: str
    status: str
    created_by: str
    created_at: datetime | None = None


class SkillAddRequest(ContractModel):
    """Body for adding a GitHub skill repository."""

    github_url: str


class SkillToggleRequest(ContractModel):
    """Body for checking/unchecking a skill."""

    enabled: bool


class AuthCredentials(ContractModel):
    """Register/login body. Passwords never leave this request in any
    response, log, or stored form (only a PBKDF2 hash is persisted)."""

    username: str
    password: str


class ModelSettingsUpdate(ContractModel):
    """The researcher's permanent model endpoint.

    ``api_key`` semantics are keep-vs-replace: an absent or blank value leaves
    the stored key untouched, a non-blank value replaces it, and
    ``clear_api_key`` (a deliberate act, not a default) removes it. The key
    itself is never returned by any endpoint -- only ``has_api_key`` is
    (CLAUDE.md 16).
    """

    base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    clear_api_key: bool = False
