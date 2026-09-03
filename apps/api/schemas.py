from __future__ import annotations

from datetime import date, datetime
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
    # The synthesised final paper (FINAL_PAPER_DRAFTED payload) and the
    # conditioned consensus (CONSENSUS_DRAFTED payload), or None before they
    # exist. Both are ledger-derived read-only views; neither is evidence
    # (the paper is an expression-layer document, the consensus is the joint
    # modelling round's own text).
    # A1 evidence lineage: sources, dependency links, independent clusters.
    lineage: FrozenDict[str, object] | None = None
    # B6 adjudication queue: pending merge candidates + quarantined nodes.
    adjudication: FrozenDict[str, object] | None = None
    # D12 real-time budget/cost usage aggregated from the audit tables.
    usage: FrozenDict[str, object] | None = None
    paper: FrozenDict[str, object] | None = None
    consensus: FrozenDict[str, object] | None = None


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
    # Language the council must write its outputs in (round-4 language
    # following): "auto" (default) resolves from the question; otherwise one
    # of zh-Hans / zh-Hant / en.
    output_language: str = "auto"
    # Task mode (round-7): "deep_research" (default) or "paper_review" -- the
    # researcher uploads a paper and the council critiques it instead of
    # investigating a controversy question.
    task_type: str = "deep_research"
    # A3 time-travel: when set, only sources published in/before this year are
    # admitted during acquisition (year granularity).
    corpus_cutoff: date | None = None


class ConfirmClaimsRequest(ContractModel):
    claim_ids: tuple[UUID, ...]


class CouncilGuidanceRequest(ContractModel):
    """Plan phase 8.2: the human's advisory steer at the JOINT_MODELING gate.

    Deliberately allows the empty string as a first-class value -- CLAUDE.md
    4/8 make this an advisory-only steer, never a vote, so "no intervention,
    just continue" is a complete, honest answer rather than a missing field.
    """

    guidance_text: str = ""


class FollowUpRequest(ContractModel):
    """Body for a post-completion follow-up question (round-9 「补充提问」).

    The researcher asks a question about the *finished* research; the answer
    must stay grounded in what the task actually produced, so the server
    threads the task question, the Research Brief and the confirmed claims
    into the model prompt -- never a bare chat with a fresh model that knows
    nothing about the run.
    """

    question: str = ""
    # Optional skills the researcher wants applied to this one answer
    # (same SKILL.md texts the council already knows; never evidence).
    skill_ids: tuple[UUID, ...] = ()
    # When true, one OpenAlex relevance search is appended as labelled
    # process context -- not a new Source, never Evidence Graph input.
    search_literature: bool = False
    # Prior turns of this follow-up thread (oldest first). Each item is
    # {"role": "user"|"assistant", "content": "..."}. Used so a second
    # question can refer to the previous answer without the model
    # forgetting mid-thread. Capped server-side.
    history: tuple[FrozenDict[str, str], ...] = ()


class ReResearchRequest(ContractModel):
    """Body for POST /api/tasks/{id}/re-research (round-12 「重新研究模式」).

    ``mode`` decides where the re-run starts:

    - ``full``: the whole protocol re-runs from PRECOMMITMENT (the council
      checkpoint is cleared; ledger idempotency keys make the replay safe).
    - ``first_gap`` (default): restart from the first unfinished phase when
      the checkpoint records one; with no recorded gap this degrades to
      ``full``.

    The same semantics apply to deep-research and paper-review tasks alike --
    both run through the same worker resume path.
    """

    mode: str = "first_gap"


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


class RegistrationRequest(ContractModel):
    """Phase-1 registration body: validate and email a verification code.
    No account is created until the code is confirmed."""

    username: str
    password: str
    email: str


class RegistrationConfirm(ContractModel):
    """Phase-2 registration body: verify the emailed code and create the
    account (auto-login with the returned bearer token)."""

    username: str
    password: str
    email: str
    code: str


class ForgotPasswordRequest(ContractModel):
    """Ask the server to email a password-reset code to this address."""

    email: str


class ResetPasswordRequest(ContractModel):
    """Verify the emailed reset code and set a new password."""

    email: str
    code: str
    password: str


class ChangeUsernameRequest(ContractModel):
    """Rename the account. The current password is required as proof."""

    new_username: str
    password: str


class ChangePasswordRequest(ContractModel):
    """Replace the password after verifying the old one."""

    old_password: str
    new_password: str


class DeleteAccountRequest(ContractModel):
    """Permanently delete the account. The password is required as proof."""

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


class ReplayRequest(ContractModel):
    """Body for POST /api/tasks/{id}/replay (A3 time-travel)."""

    corpus_cutoff: date


class AdjudicationRequest(ContractModel):
    """Body for a researcher's manual merge/quarantine decision (B6).

    The decision is recorded as a process-only RESEARCHER_ADJUDICATION ledger
    event; it never writes the Evidence Graph (AGENTS.md principle 8).
    """

    # Which merge candidate (its text) or quarantined node (its node id).
    target_key: str
    # Free-form decision label, e.g. keep_separate / accept / keep_quarantined.
    decision: str
    note: str = ""


class ModelOverrideRequest(ContractModel):
    """Body for C10 model hot-swap. ``clear`` removes the override."""

    config: FrozenDict[str, object] | None = None
    clear: bool = False


class AnnotationItemInput(ContractModel):
    ref_kind: str
    ref_node_id: str
    statement: str
    position: FrozenDict[str, object] | None = None


class AnnotationCreateRequest(ContractModel):
    title: str = ""
    note: str = ""
    items: tuple[AnnotationItemInput, ...] = ()


class AnnotationLabelRequest(ContractModel):
    item_id: UUID
    rater_name: str
    label: str
    note: str = ""


class SaveToKnowledgeRequest(ContractModel):
    """Body for A4: distil a finished task into one knowledge-base document."""

    knowledge_base_id: UUID
