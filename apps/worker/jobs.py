"""One unit of background work: run a queued task through the protocol.

The job is split into two transactions on purpose, and the split is the whole
point of the module.

1. **Deliberation** runs as ``poliscope_app``. It appends to the Scientific Event
   Ledger and updates the task row. That identity holds no write privilege on
   ``graph_nodes`` or ``graph_edges``, so a bug here cannot reach the Evidence
   Graph even if it tries.
2. **Projection** runs as ``poliscope_projector``. It reads the committed ledger
   and writes the graph. That identity holds no INSERT on the ledger, so the
   projector cannot invent the events it then projects.

CLAUDE.md 5.3 makes the projector the only writer of the Evidence Graph. Running
both halves in one session with one role would leave that rule enforced by
nothing but this file's good intentions.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator, ReasoningCallback
from packages.council.models import CouncilRoundModel, ScientistRunModel
from packages.council.rounds.registry import (
    MODEL_REASONING_CAPTURED,
    SeatDeliberator,
)
from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    CouncilCheckpoint,
    TaskPhase,
    TaskStatus,
)
from packages.epistemo.orchestrator import CouncilOrchestrator, TaskRunReport
from packages.evidence.ledger import EventConflict
from packages.evidence.lifecycle import QuarantinedNode
from packages.evidence.models import EventAuditModel, ScientificEventModel
from packages.evidence.process_stream import ProcessStreamWriter
from packages.evidence.sql_ledger import SqlEventLedger
from packages.evidence.sql_projector import (
    STATUS_QUARANTINED,
    ProjectionReport,
    SqlGraphProjector,
    node_id_for,
)
from packages.kernel.database import canonical_uuid
from packages.knowledge.models import KnowledgeDocumentModel
from packages.knowledge.search import KnowledgeBaseSearch
from packages.memory.adapter import create_memory_adapter
from packages.memory.council_memory import CouncilMemory
from packages.models.contracts import ModelClass, ModelGateway
from packages.models.endpoint_config import normalize_base_url
from packages.models.gateway import AuditedModelGateway
from packages.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleModelGateway,
)
from packages.papers.acquisition import KnowledgeDocumentRef, SourceAcquisition
from packages.papers.bibtex import extract_dois_from_bibtex
from packages.papers.finding_extraction import FindingExtractor
from packages.papers.object_store import PrivateObjectStore
from packages.papers.understanding import (
    load_paper_understanding,
    understand_paper,
)
from packages.reports.synthesis import synthesize_paper
from packages.research.contracts import TaskModelConfig
from packages.research.language import detect_output_language
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED, ResearchRepository
from packages.skills.models import SkillModel
from packages.skills.repository import to_stored
from packages.skills.service import SkillsService
from packages.tools.contracts import ToolGateway
from packages.tools.fulltext_fetcher import FullTextFetcher
from packages.tools.gateway import AuditedToolGateway

logger = logging.getLogger(__name__)


class TaskNotRunnable(Exception):
    """Raised when a task cannot be run, with the reason kept for the caller."""


@dataclass(frozen=True, slots=True)
class JobResult:
    task_id: UUID
    run: TaskRunReport
    projection: ProjectionReport | None


async def _claim(session: AsyncSession, task_id: UUID) -> ResearchTaskModel:
    """Lock the task row, then check that it is still ours to run.

    The lock is what makes two workers safe. Without it both read QUEUED, both
    deliberate, and the ledger's idempotency keys quietly absorb the duplicate --
    which looks like it worked while burning the budget twice. The second worker
    here blocks until the first commits and then sees a status that is no longer
    QUEUED.

    Two statuses are runnable. ``RUNNING`` is the worker's claim state
    (``claim_queued_tasks`` flips the row before releasing its lock, so no
    second claim can ever select it again -- see apps/worker/main.py); the
    direct ``deliberate`` callers (CLI, tests) still pass QUEUED tasks.
    """
    row = await session.scalar(
        select(ResearchTaskModel)
        .where(ResearchTaskModel.task_id == task_id)
        .with_for_update()
    )
    if row is None:
        raise TaskNotRunnable(f"task {task_id} does not exist")
    if row.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
        raise TaskNotRunnable(
            f"task {task_id} is {row.status}, not runnable"
        )
    return row


def _gateway_for_task_config(
    config: Mapping[str, object],
) -> OpenAICompatibleModelGateway:
    """Build a per-task gateway from the task's own model configuration.

    All three model tiers use the task's single model name: a researcher who
    brings their own endpoint brings one model, not a tier ladder. The name
    defaults to the deployment's configured model, or ``deepseek-v4-flash`` when
    the deployment has none -- the "system default DeepSeek" the web form
    promises (TaskModelConfig's docstring).
    """
    parsed = TaskModelConfig.model_validate(dict(config))
    model_name = (
        parsed.model_name
        or os.environ.get("POLISCOPE_MODEL_NAME")
        or "deepseek-v4-flash"
    )
    # Normalise the stored endpoint too: tasks created before the settings API
    # learned to do this can carry a console-portal URL (the incident that
    # made the council go absent), and the gateway must not inherit it.
    base_url, _ = normalize_base_url(parsed.base_url)
    # extra_body (round-7 free trial): vendor-specific request fields merged
    # into the chat-completions body; None on the ordinary researcher-owned
    # path keeps the DeepSeek-style thinking toggle behaviour unchanged.
    extra_body = dict(parsed.extra_body) if parsed.extra_body is not None else None
    return OpenAICompatibleModelGateway(
        OpenAICompatibleConfig(
            api_key=parsed.api_key,
            base_url=base_url,
            model_names={
                ModelClass.STRONG_REASONING: model_name,
                ModelClass.MEDIUM: model_name,
                ModelClass.LIGHTWEIGHT: model_name,
            },
            extra_body=extra_body,
        )
    )


def _reasoning_emitter(ledger: SqlEventLedger) -> ReasoningCallback:
    """Return the callback that records a seat's raw chain of thought.

    Appends a process-only ``MODEL_REASONING_CAPTURED`` event under the same
    transaction as the rest of the round (the ledger neither commits nor
    rolls back). The idempotency key is derived from task/seat/phase, so a
    resumed run cannot duplicate it -- and the projector's allowlist keeps it
    out of the Evidence Graph no matter what (CLAUDE.md 5.1).
    """

    async def emit(
        task_id: UUID,
        seat: Seat,
        phase: TaskPhase,
        reasoning: str,
    ) -> None:
        await ledger.append(
            task_id,
            MODEL_REASONING_CAPTURED,
            {
                "seat": seat.value,
                "phase": phase.value,
                "reasoning": reasoning,
                "char_count": len(reasoning),
            },
            f"reasoning:{task_id}:{seat.value}:{phase.value}",
        )

    return emit


def _budget_for(row: ResearchTaskModel) -> BudgetTracker:
    return BudgetTracker(
        limits=ResearchBudget(
            wall_clock_minutes=row.wall_clock_minutes,
            model_cost_usd=Decimal(row.model_cost_usd),
            tool_call_limit=row.tool_call_limit,
            source_limit=row.source_limit,
        )
    )


async def _confirmed_claim_ids(
    session: AsyncSession,
    task_id: UUID,
) -> tuple[UUID, ...]:
    result = await session.execute(
        select(AtomicClaimModel.id)
        .where(
            AtomicClaimModel.task_id == task_id,
            AtomicClaimModel.status == CLAIM_CONFIRMED,
        )
        .order_by(AtomicClaimModel.created_at, AtomicClaimModel.id)
    )
    # canonical_uuid at the driver boundary: asyncpg returns its own UUID
    # subclass, and the frozen contracts these ids flow into admit a leaf only
    # when its type matches exactly. See packages.kernel.database.
    return tuple(canonical_uuid(value) for value in result.scalars())


async def _quarantined_nodes(
    session: AsyncSession,
    task_id: UUID,
) -> tuple[QuarantinedNode, ...]:
    """Load nodes an earlier run of this task quarantined, for Resurrect.

    A quarantined event never reaches ``graph_nodes`` (the projector returns
    before ``_upsert_node`` on ``AdmissionDisposition.QUARANTINE``), so the
    ledger is the only source of truth for what is quarantined and why. This
    read is legal under the ``poliscope_app`` identity that runs
    ``deliberate()``: migration 0003 grants that role ``READ`` on both
    ``scientific_events`` (already exercised by ``SqlEventLedger``) and
    ``event_audits`` -- no new grant needed.

    Real gate-driven quarantine never populates an ``attacker``/
    ``missing_evidence``/``resurrection_condition`` -- those are
    ``LifecycleService.quarantine()``'s own fields (unused in production, see
    README's known-gaps note on ``packages/evidence/lifecycle.py``). Rather
    than fabricate plausible-looking text for fields the gate never recorded,
    the honest default is used (CLAUDE.md 7): the gate's own disposition
    reasons become ``reason``, and the rest stay explicitly unrecorded.
    """
    events = await session.scalars(
        select(ScientificEventModel).where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.status == STATUS_QUARANTINED,
        )
    )
    nodes: list[QuarantinedNode] = []
    for event in events:
        audit = await session.scalar(
            select(EventAuditModel)
            .where(
                EventAuditModel.event_id == event.id,
                EventAuditModel.gate_stage == "ADMISSION",
            )
            .order_by(EventAuditModel.created_at.desc())
        )
        raw_reasons = audit.reasons.get("reasons") if audit is not None else None
        reasons = (
            tuple(str(item) for item in raw_reasons)
            if isinstance(raw_reasons, (list, tuple))
            else ()
        )
        nodes.append(
            QuarantinedNode(
                node_id=canonical_uuid(node_id_for(event)),
                reason="; ".join(reasons) if reasons else "not recorded",
                attacker="evidence_gate",
                missing_evidence="not recorded",
                resurrection_condition="not recorded",
            )
        )
    return tuple(nodes)


def _pdf_object_ids(task: ResearchTaskModel) -> tuple[UUID, ...]:
    """Read back the object ids ``apps/api/routers/papers.py`` recorded.

    ``user_evidence`` is untyped JSONB (see ``ResearchTaskModel.user_evidence``),
    so what comes back is plain strings, not ``UUID`` objects -- converted here
    rather than trusting the stored shape, since a hand-edited row or a future
    schema change should fail loudly at this boundary, not deep inside the
    acquisition round.
    """
    raw = task.user_evidence.get("pdf_object_ids", ())
    return tuple(UUID(str(value)) for value in raw)


def _user_dois(task: ResearchTaskModel) -> tuple[str, ...]:
    """Resolve the researcher's own DOIs: explicit ones plus BibTeX-extracted.

    ``dois`` are stored as strings and passed through as-is; ``bibtex_entries``
    are free text whose DOIs only appear once ``packages/papers/bibtex.py``
    pulls them out. Both were previously persisted and never read -- CLAUDE.md
    7 treats a stored-but-unused entry as silent data loss, so this is where
    they finally enter the pipeline. Bad values fail loudly at this boundary
    (str(value) would otherwise silently accept a non-string leaf), mirroring
    ``_pdf_object_ids``.
    """
    raw_dois = task.user_evidence.get("dois", ())
    explicit = tuple(str(value) for value in raw_dois)
    bibtex = "".join(
        str(value) for value in task.user_evidence.get("bibtex_entries", ())
    )
    extracted = extract_dois_from_bibtex(bibtex) if bibtex else ()
    seen: set[str] = set()
    result: list[str] = []
    for doi in (*explicit, *extracted):
        if doi and doi not in seen:
            seen.add(doi)
            result.append(doi)
    return tuple(result)


async def _knowledge_documents(
    session: AsyncSession,
    task: ResearchTaskModel,
) -> tuple[KnowledgeDocumentRef, ...]:
    """Load the linked knowledge base's documents for the council.

    Runs on the session the deliberation already holds; empty when the task
    linked no knowledge base. The ids are canonicalised at this boundary
    (same asyncpg-UUID reasoning as _confirmed_claim_ids).
    """
    if task.knowledge_base_id is None:
        return ()
    rows = await session.execute(
        select(KnowledgeDocumentModel).where(
            KnowledgeDocumentModel.knowledge_base_id == task.knowledge_base_id
        )
    )
    return tuple(
        KnowledgeDocumentRef(
            document_id=canonical_uuid(row.id),
            object_key=row.object_key,
            title=row.title,
        )
        for row in rows.scalars()
    )


def _knowledge_searcher(
    session: AsyncSession,
    task: ResearchTaskModel,
) -> KnowledgeBaseSearch | None:
    """Keyword search over the linked knowledge base, or None without one."""
    if task.knowledge_base_id is None:
        return None
    return KnowledgeBaseSearch(session, canonical_uuid(task.knowledge_base_id))


async def _skills_context(
    session: AsyncSession,
    task: ResearchTaskModel,
) -> tuple[tuple[str, str], ...]:
    """Resolve the task's enabled skills to ``(name, markdown)`` pairs.

    Only skills that are both listed on the task and still enabled join the
    council's prompts; a skill the researcher disabled after creating the
    task no longer instructs the scientists. A missing file on disk is
    re-downloaded rather than silently dropped (SkillsService.
    ensure_downloaded), so a disk hiccup cannot quietly remove a skill the
    researcher chose.
    """
    if not task.skill_ids:
        return ()
    rows = (
        await session.scalars(
            select(SkillModel).where(
                SkillModel.id.in_(task.skill_ids),
                SkillModel.enabled.is_(True),
            )
        )
    ).all()
    if not rows:
        return ()
    service = SkillsService(session)
    result: list[tuple[str, str]] = []
    for row in rows:
        result.append((row.name, await service.ensure_downloaded(to_stored(row))))
    return tuple(result)


async def deliberate(
    session: AsyncSession,
    task_id: UUID,
    deliberator: SeatDeliberator | None = None,
    gateway: ModelGateway | None = None,
    tools: ToolGateway | None = None,
    fulltext_fetcher: FullTextFetcher | None = None,
    object_store: PrivateObjectStore | None = None,
    process: ProcessStreamWriter | None = None,
) -> TaskRunReport:
    """Run the seven rounds and persist the resulting events and status.

    Refuses a task that is not QUEUED. Re-running a finished task would append
    the same events under the same idempotency keys and be harmless, but it would
    also reset a terminal status, and CLAUDE.md 10 wants a completed task's
    reported gaps to stay as they were.

    ``deliberator`` overrides ``gateway``; passing neither runs the protocol with
    every seat reported unavailable, which is what a deployment with no model
    provider should honestly produce.

    Plan phase 8.2: a task's ``council_checkpoint`` column tells this function
    which of two runs it is doing. Both start from QUEUED, so ``_claim`` needs no
    change either way -- ``submit_council_guidance``
    (packages/research/service.py) already moves the task back to QUEUED before
    the worker ever sees it again.

    - No stored checkpoint: this is the first pass. The orchestrator halts
      before JOINT_MODELING and returns ``AWAITING_COUNCIL_INPUT`` rather than
      running the full eight phases, so the human gets a chance to steer before
      the council commits to a joint model.
    - A stored checkpoint: this is the resume pass, after
      ``council-guidance`` recorded the human's (possibly empty) advisory text.
      The checkpoint is handed back to the orchestrator verbatim via
      ``resume_from`` so already-run phases are not repeated, and its
      ``guidance`` field is passed separately so it reaches JOINT_MODELING's
      prompt only (CLAUDE.md 4/8 -- see ``CouncilOrchestrator.run``'s
      docstring).

    Either way, the checkpoint column is rewritten to match what the
    orchestrator reports this pass: set when it halts again (which the fixed
    single-checkpoint design here never actually produces after a resume, but
    is handled the same way regardless), cleared once the run reaches a
    terminal status, so a finished task never carries stale checkpoint JSON
    that a later reader could mistake for still-pending input.
    """
    task = await _claim(session, task_id)
    budget = _budget_for(task)
    # A task with its own model configuration runs against the researcher's
    # endpoint, not the process's gateway -- a per-task override, owned and
    # closed by this run (its httpx client must not leak between tasks).
    owned_gateway: OpenAICompatibleModelGateway | None = None
    if task.model_config:
        owned_gateway = _gateway_for_task_config(task.model_config)
        gateway = owned_gateway
    try:
        return await _deliberate_impl(
            session, task, task_id, budget, deliberator, gateway, tools,
            fulltext_fetcher, object_store, process,
        )
    finally:
        if owned_gateway is not None:
            await owned_gateway.aclose()


async def _deliberate_impl(
    session: AsyncSession,
    task: ResearchTaskModel,
    task_id: UUID,
    budget: BudgetTracker,
    deliberator: SeatDeliberator | None,
    gateway: ModelGateway | None,
    tools: ToolGateway | None,
    fulltext_fetcher: FullTextFetcher | None,
    object_store: PrivateObjectStore | None,
    process: ProcessStreamWriter | None = None,
) -> TaskRunReport:
    # task_id is the canonicalised argument, not task.task_id: the ORM row's
    # value is asyncpg's UUID subclass, which the frozen contracts reject
    # (same reason _confirmed_claim_ids canonicalises at its boundary).
    if deliberator is None and gateway is not None:
        # Every model call goes through the gateway, audited, per CLAUDE.md 8.
        # With no gateway the run still happens and reports every seat as
        # unavailable, which is the truthful outcome rather than a silent
        # success. A captured chain of thought is recorded as a process-only
        # ledger event in the same transaction (see _reasoning_emitter).
        deliberator = GatewayDeliberator(
            AuditedModelGateway(gateway, session),
            budget,
            on_reasoning=_reasoning_emitter(SqlEventLedger(session)),
            on_process=None if process is None else process.emit,
            on_flush=None if process is None else process.flush,
        )
    # Resolved before the components below so an enabled skill's instructions
    # reach every model call of the run -- the council's prompts and the
    # finding extractor alike (round-5 request).
    skills = await _skills_context(session, task)
    orchestrator = CouncilOrchestrator(
        ledger=SqlEventLedger(session),
        budget=budget,
        deliberator=deliberator,
        # Process memory, per CLAUDE.md 6. It is created per run rather than per
        # process so one task's recall can never leak into another's.
        memory=CouncilMemory(create_memory_adapter(), task_id),
        # Only when a tool provider is configured. Without one the acquisition
        # round records requests and reports the gap, per CLAUDE.md 7 and 10.
        acquirer=(
            None
            if tools is None
            else SourceAcquisition(
                session,
                AuditedToolGateway(tools, session),
                task_id,
                budget,
                on_process=None if process is None else process.emit,
            )
        ),
        # Needs both a tool provider (open access lookup) and a model provider
        # (extraction call); missing either leaves every acquired source at
        # Level B and the gap recorded, same honesty rule as acquirer above.
        finding_extractor=(
            None
            if tools is None or gateway is None
            else FindingExtractor(
                session,
                AuditedToolGateway(tools, session),
                AuditedModelGateway(gateway, session),
                task_id,
                budget,
                fulltext_fetcher=fulltext_fetcher,
                object_store=object_store,
                researcher_skills=skills,
            )
        ),
    )
    checkpoint = (
        None
        if task.council_checkpoint is None
        else CouncilCheckpoint.model_validate(task.council_checkpoint)
    )
    # Legacy rows created before migration 0017 carry "auto": resolve it from
    # the question here so no pre-existing task is left without a language.
    output_language = task.output_language or "auto"
    if output_language == "auto":
        output_language = detect_output_language(task.question)

    # Round-7 paper-review tasks: before the council runs, one model call
    # reads the uploaded paper and states what it claims (see
    # packages/papers/understanding.py). The first pass runs it (its ledger
    # idempotency key is stable, so a replay is a no-op); a resumed pass reads
    # the captured event back instead of paying for the call again. The
    # summary is injected into every seat's prompt as explicitly non-evidence
    # context; the paper's own text is the Level A evidence via the
    # acquisition pass. With no model provider the step is skipped and the
    # report must admit it (gateway None -> no event).
    paper_understanding: dict[str, object] | None = None
    if getattr(task, "task_type", "deep_research") == "paper_review":
        if checkpoint is None:
            understanding = await understand_paper(
                session,
                task_id,
                gateway,
                object_store or PrivateObjectStore.from_env(),
                output_language=output_language,
            )
            if understanding.ok:
                paper_understanding = understanding.payload
            elif understanding.reason:
                logger.warning(
                    "paper understanding unavailable for task %s: %s",
                    task_id,
                    understanding.reason,
                )
        else:
            paper_understanding = await load_paper_understanding(session, task_id)

    if checkpoint is None:
        report = await orchestrator.run(
            task_id=task_id,
            question=task.question,
            confirmed_claims=await _confirmed_claim_ids(session, task_id),
            quarantined=await _quarantined_nodes(session, task_id),
            pdf_object_ids=_pdf_object_ids(task),
            user_dois=_user_dois(task),
            knowledge_documents=await _knowledge_documents(session, task),
            knowledge_search=_knowledge_searcher(session, task),
            researcher_skills=skills,
            output_language=output_language,
            paper_understanding=paper_understanding,
            stop_before=TaskPhase.JOINT_MODELING,
        )
    else:
        report = await orchestrator.run(
            task_id=task_id,
            question=task.question,
            confirmed_claims=await _confirmed_claim_ids(session, task_id),
            quarantined=await _quarantined_nodes(session, task_id),
            pdf_object_ids=_pdf_object_ids(task),
            user_dois=_user_dois(task),
            knowledge_documents=await _knowledge_documents(session, task),
            knowledge_search=_knowledge_searcher(session, task),
            researcher_skills=skills,
            output_language=output_language,
            paper_understanding=paper_understanding,
            resume_from=checkpoint,
            council_guidance=checkpoint.guidance,
        )

    # Per-seat attendance audit (round-9): persist which seats ran in which
    # phases, how many attempts each took, and why the absent ones are absent.
    # Same transaction as everything else in this function -- the caller's
    # session.commit() makes the audit trail atomic with the ledger and task row.
    await _persist_council_runs(session, task_id, report)

    repository = ResearchRepository(session)
    if report.final_status == TaskStatus.AWAITING_COUNCIL_INPUT:
        assert report.checkpoint is not None
        await repository.set_checkpoint(
            task_id, report.checkpoint.model_dump(mode="json")
        )
    else:
        await repository.set_checkpoint(task_id, None)
    await repository.set_status(task_id, report.final_status)

    # The synthesis step only runs once the council reached a terminal
    # status: a task parked at the AWAITING_COUNCIL_INPUT checkpoint has not
    # finished deliberating, and synthesising a paper over a half-run council
    # would present an incomplete run as complete. It runs inside the same
    # deliberation transaction (the ledger neither commits nor rolls back on
    # its own), before the projector admits the round's node events, so the
    # ledger's sequence reads: ... REPORTING phase events, then
    # FINAL_PAPER_DRAFTED/FAILED, then the graph. The paper is process-only
    # history (never a graph node); a failed synthesis is recorded as
    # FINAL_PAPER_FAILED and never raises the run.
    if report.final_status in (
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED_WITH_GAPS,
    ):
        outcome = await synthesize_paper(
            session, task_id, gateway, output_language=output_language
        )
        if not outcome.available:
            logger.warning(
                "paper synthesis unavailable for task %s: %s",
                task_id,
                outcome.reason,
            )
    return report


async def project(session: AsyncSession, task_id: UUID) -> ProjectionReport:
    """Admit the committed events into the graph under the projector identity."""
    return await SqlGraphProjector(session).project_pending(task_id)


async def _persist_council_runs(
    session: AsyncSession, task_id: UUID, report: TaskRunReport
) -> None:
    """Write one pass's seat attendance into council_rounds / scientist_runs.

    Round-9 audit trail. The rows are keyed by a deterministic round id derived
    from ``(task_id, phase)``, and both writes are upserts keyed on that id, so a
    replay (e.g. a requeued task re-running an already-completed phase) updates
    the same row to its latest attempts/status instead of stacking a second
    copy -- the same idempotency philosophy as the ledger (0005's "a retry
    updates the row it already owns"). Each pass of ``run()`` reports only the
    phases it actually ran, so a checkpoint-resumed pass adds its later phases
    on top of the first pass's rows.

    ``round_outputs`` is deliberately not written here: this is an absence /
    retry audit, and the seats' structured actions already live on the ledger
    (JSONB payload, no 64-char truncation).
    """
    if not report.phases_run:
        return
    now = datetime.now(UTC)
    for phase in report.phases_run:
        round_id = uuid5(
            NAMESPACE_URL, f"poliscope/council-round/{task_id}/{phase.value}"
        )
        phase_records = [
            record for record in report.seat_runs if record.phase is phase
        ]
        started_at = (
            phase_records[0].started_at if phase_records else now
        )
        completed_at = (
            phase_records[0].completed_at if phase_records else now
        )
        phase_index = PHASE_SEQUENCE.index(phase)
        next_phase = (
            PHASE_SEQUENCE[phase_index + 1].value
            if phase_index + 1 < len(PHASE_SEQUENCE)
            else None
        )
        await session.execute(
            pg_insert(CouncilRoundModel)
            .values(
                id=round_id,
                task_id=task_id,
                phase=phase.value,
                status="completed",
                started_at=started_at,
                completed_at=completed_at,
                next_phase=next_phase,
            )
            .on_conflict_do_update(
                index_elements=[CouncilRoundModel.id],
                set_={
                    "status": "completed",
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "next_phase": next_phase,
                },
            )
        )
        for record in phase_records:
            error_code = (
                str(record.error_code)[:64] if record.error_code else None
            )
            await session.execute(
                pg_insert(ScientistRunModel)
                .values(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"poliscope/scientist-run/{task_id}/{phase.value}/"
                        f"{record.seat.value}",
                    ),
                    round_id=round_id,
                    seat=record.seat.value,
                    status=record.status,
                    started_at=record.started_at or now,
                    completed_at=record.completed_at or now,
                    error_code=error_code,
                    attempts=record.attempts,
                )
                .on_conflict_do_update(
                    constraint="uq_scientist_run_seat",
                    set_={
                        "status": record.status,
                        "started_at": record.started_at or now,
                        "completed_at": record.completed_at or now,
                        "error_code": error_code,
                        "attempts": record.attempts,
                    },
                )
            )


async def _mark_failed(
    app_sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> None:
    """Terminate a task stuck behind an unrecoverable ``EventConflict``.

    Runs in a brand-new session because the one that raised is already
    rolled back and about to be closed by its own ``async with`` block in
    :func:`run_task` -- writing through a rolled-back session would silently
    no-op or reuse a dead transaction.

    The council checkpoint is deliberately kept: ``reResearch`` (round-8)
    moves a FAILED task back to QUEUED, and the worker then resumes from the
    stored checkpoint instead of re-running the phases that already completed
    (a checkpoint exists only once the run has reached AWAITING_COUNCIL_INPUT).
    """
    async with app_sessions() as session:
        repository = ResearchRepository(session)
        await repository.set_status(task_id, TaskStatus.FAILED)
        await session.commit()


async def run_task(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    deliberator: SeatDeliberator | None = None,
    gateway: ModelGateway | None = None,
    tools: ToolGateway | None = None,
    fulltext_fetcher: FullTextFetcher | None = None,
    object_store: PrivateObjectStore | None = None,
) -> JobResult:
    """Deliberate, commit, then project.

    Projection failing does not undo the deliberation. The events are already
    durable and the checkpoint has not moved, so the next pass reprocesses
    exactly the events that were not admitted -- which is the resume behaviour
    CLAUDE.md 10 asks for, rather than a lost round.

    ``EventConflict`` is different from every other failure this function can
    raise, and is handled separately on purpose. It means two *different*
    events were assigned the same identity -- under normal operation this
    cannot happen any more: the claim transaction flips the task to RUNNING
    (apps/worker/main.py), so a task is never run twice, and a crashed
    worker's reclaim (``recover_stale_running``) only ever resets runs whose
    transaction rolled back, so no committed event is ever replayed against a
    different payload. The exception survives as a backstop: if a conflict
    somehow still surfaces, the honest response is to stop retrying and record
    the task as terminally ``FAILED`` rather than burn model calls in an
    infinite reclaim loop with no terminal status ever recorded -- the
    "budget exhausted must report incomplete evidence, never fabricate
    completeness" rule of CLAUDE.md 10, applied to identity conflicts.
    """
    # The live-view trace (model deltas, tool calls) rides a separate,
    # self-owned session chain: it must never tangle with the deliberation
    # transaction, and a broken trace must never break the run. It is created
    # here because this function owns app_sessions; everything downstream only
    # sees the synchronous emit callback.
    process = ProcessStreamWriter(app_sessions, task_id)
    try:
        async with app_sessions() as session:
            try:
                report = await deliberate(
                    session,
                    task_id,
                    deliberator,
                    gateway,
                    tools,
                    fulltext_fetcher,
                    object_store,
                    process=process,
                )
                await session.commit()
            except EventConflict:
                await session.rollback()
                await _mark_failed(app_sessions, task_id)
                raise
            except BaseException:
                await session.rollback()
                raise
    finally:
        # Flush whatever the trace still holds, even on failure -- the live
        # view should show how far the run got before it stopped.
        await process.close()

    async with projector_sessions() as session:
        try:
            projection = await project(session, task_id)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise

    return JobResult(task_id=task_id, run=report, projection=projection)


__all__ = [
    "JobResult",
    "TaskNotRunnable",
    "deliberate",
    "project",
    "run_task",
]
