"""Task lifecycle endpoints.

Creating a task never starts research. The task waits in
AWAITING_CLAIM_CONFIRMATION until the researcher confirms which atomic claims
the council will investigate, which is the control point CLAUDE.md 2 requires.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.routers.workspace import _seats
from apps.api.schemas import (
    ConfirmClaimsRequest,
    CouncilGuidanceRequest,
    CreateTaskRequest,
)
from packages.accounts.repository import StoredUser
from packages.council.models import (
    CouncilRoundModel,
    RoundOutputModel,
    ScientistRunModel,
)
from packages.evidence.models import (
    EventAuditModel,
    GraphEdgeModel,
    GraphNodeModel,
    ProcessStreamModel,
    ProjectionCheckpointModel,
    ScientificEventModel,
)
from packages.knowledge.repository import KnowledgeBaseNotFound, KnowledgeRepository
from packages.models.endpoint_config import normalize_base_url
from packages.models.free_trial import (
    FREE_TRIAL_EXHAUSTED_MESSAGE,
    FREE_TRIAL_EXTRA_BODY,
    FREE_TRIAL_LIMIT,
)
from packages.models.models import ModelCallModel
from packages.models.settings import ModelSettingsRepository, StoredModelSettings
from packages.papers.models import ObjectModel, SourceModel, SourceVersionModel
from packages.research.contracts import ResearchContract
from packages.research.language import detect_output_language
from packages.research.models import (
    AtomicClaimModel,
    ResearchScopeModel,
    ResearchTaskModel,
)
from packages.research.repository import ResearchRepository, StoredTask, TaskNotFound
from packages.research.service import (
    InvalidCouncilGuidanceState,
    InvalidPauseState,
    ResearchService,
    UnconfirmedClaims,
)
from packages.skills.repository import SkillsRepository
from packages.tools.models import ToolCallModel

router = APIRouter()

TASK_NOT_FOUND = "unknown task"


def _service(session: SessionDep) -> ResearchService:
    return ResearchService(ResearchRepository(session))


def _effective_model_config(
    task_config: dict[str, Any] | None,
    saved: StoredModelSettings,
) -> dict[str, Any]:
    """The model endpoint this task would actually run with, for display.

    Round-6 report: a researcher saved an API key and model name, then saw
    tasks still run the deployment default and could not tell whether their
    settings had ever been applied. This resolves the same inheritance
    create_task applies -- explicit per-task config wins, then the account's
    saved settings (only when both URL and key are present), else the
    deployment default -- so the UI can say which one a task uses. The key
    itself never leaves the server (CLAUDE.md 16), only its presence.
    """
    explicit = task_config or {}
    if explicit.get("base_url") and explicit.get("api_key"):
        return {
            "source": "saved",
            "base_url": explicit["base_url"],
            "model_name": explicit.get("model_name"),
            "has_api_key": True,
        }
    if saved.model_base_url and saved.has_api_key:
        return {
            "source": "saved",
            "base_url": saved.model_base_url,
            "model_name": saved.model_name,
            "has_api_key": True,
        }
    return {
        "source": "default",
        "base_url": None,
        "model_name": None,
        "has_api_key": False,
    }


def _normalized_model_config(config: dict[str, object]) -> dict[str, object]:
    """Normalise the endpoint before it is stored on the task.

    User input is never stored verbatim (see packages/models/endpoint_config.py
    for the incident that proved it): the base_url gets its scheme, trailing
    slash, and any console-portal rewrite applied so the worker later builds a
    working gateway. ``TaskModelConfig`` also rejects scheme-less values, so
    normalising first means a bare ``platform.deepseek.com`` becomes a valid
    ``https://api.deepseek.com`` instead of a 422.
    """
    raw_url = config.get("base_url")
    if isinstance(raw_url, str) and raw_url.strip():
        normalized, _ = normalize_base_url(raw_url)
        config = {**config, "base_url": normalized}
    return config


def _not_found(task_id: UUID, error: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{TASK_NOT_FOUND} {task_id}",
    )


async def _owned_task(
    session: SessionDep, task_id: UUID, user: StoredUser
) -> StoredTask:
    """Fetch a task scoped to the caller; someone else's task is a 404.

    The repository treats an unowned or other-owned task as TaskNotFound, so
    the API answers 404 for both "no such task" and "not your task" -- the
    existence of another account's research must not leak.
    """
    try:
        return await ResearchRepository(session).get_task(task_id, user.id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error


@router.get("")
@router.get("/", include_in_schema=False)
async def list_tasks(
    session: SessionDep,
    current_user: CurrentUserDep,
) -> list[dict[str, Any]]:
    """The caller's sessions, newest first, for the web session-history panel.

    The panel replaces the old "paste a task id" box: the researcher's whole
    history is one click away. Scoped to the calling account -- another
    account's sessions are invisible, and pre-account rows belong to no one.
    Summaries only -- no claims, no evidence. The model endpoint each task
    will use is resolved once for the whole list (the account's saved settings
    are the same for every task).
    """
    tasks = await ResearchRepository(session).list_tasks(current_user.id)
    saved = await ModelSettingsRepository(session).get(current_user.id)
    return [
        {
            "task_id": str(task.task_id),
            "question": task.question,
            "status": task.status,
            "created_by": task.created_by,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "task_type": task.task_type,
            "effective_model_config": _effective_model_config(
                task.model_config, saved
            ),
        }
        for task in tasks
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_task(
    request: CreateTaskRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Create a task (owned by the caller) and return its suggested claims."""
    if request.knowledge_base_id is not None:
        # The contract cannot validate this itself (cross-table), so the
        # router does: a task linked to a knowledge base that does not exist
        # would silently lose the researcher's documents at worker time.
        # Scoped to the caller -- another account's base is "unknown".
        try:
            await KnowledgeRepository(session).get_knowledge_base(
                request.knowledge_base_id, current_user.id
            )
        except KnowledgeBaseNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown knowledge base {request.knowledge_base_id}",
            ) from error
    if request.skill_ids:
        # A task may only enable skills the caller actually owns -- another
        # account's skill id would leak its name into this task's prompts.
        repository = SkillsRepository(session)
        for skill_id in request.skill_ids:
            if await repository.get_for_user(current_user.id, skill_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"unknown skill {skill_id}",
                )
    task_model_config: dict[str, object] | None = (
        dict(request.task_model_config) if request.task_model_config else None
    )
    if task_model_config is None:
        # Permanent model settings: a task that carries no explicit per-task
        # config inherits the account's saved endpoint, so the researcher
        # sets it once on the right-hand settings panel (or via the CLI) and
        # every new task uses it. An explicit per-task config always wins.
        saved = await ModelSettingsRepository(session).get(current_user.id)
        if saved.model_base_url and saved.has_api_key:
            task_model_config = {
                "base_url": saved.model_base_url,
                "api_key": saved.model_api_key,
                "model_name": saved.model_name,
            }
            if saved.is_free_trial:
                # Free-trial marker: the task's own config must remember it
                # came from the trial (confirm-claims consumes a quota slot
                # on this flag), and the vendor's request fields must reach
                # the worker's gateway. The slot is consumed when the task
                # actually starts, so a trial task that is created but never
                # confirmed costs nothing.
                task_model_config["is_free_trial"] = True
                task_model_config["extra_body"] = dict(FREE_TRIAL_EXTRA_BODY)
                if saved.free_trial_used >= FREE_TRIAL_LIMIT:
                    # The first gate: refuse at the moment of asking, before
                    # any draft exists. The second gate is confirm-claims'
                    # atomic consume, which stays authoritative under
                    # concurrency.
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=FREE_TRIAL_EXHAUSTED_MESSAGE,
                    )
    if task_model_config is not None:
        task_model_config = _normalized_model_config(task_model_config)
    # Output language follows the language the researcher asked in: "auto"
    # (the default) is resolved here from the question so the stored row
    # always carries a concrete language and the worker never has to guess.
    # An explicit value from the client (round-4 language switching) wins.
    if request.output_language in (None, "", "auto"):
        output_language = detect_output_language(request.question)
    else:
        output_language = request.output_language
    try:
        contract = ResearchContract.model_validate(
            {
                "question": request.question,
                "scope": dict(request.scope),
                "budget": dict(request.budget),
                "user_evidence": dict(request.user_evidence),
                "task_model_config": task_model_config,
                "knowledge_base_id": request.knowledge_base_id,
                "skill_ids": tuple(request.skill_ids),
                "output_language": output_language,
                "task_type": request.task_type,
            }
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    created = await _service(session).create(
        contract, created_by=current_user.username, user_id=current_user.id
    )
    return {
        "task_id": str(created.task_id),
        "status": created.status,
        "suggested_claims": [
            {
                "id": str(claim.claim_id),
                "statement": claim.statement,
                "claim_type": claim.claim_type,
                "falsification_condition": claim.falsification_condition,
            }
            for claim in created.suggested_claims
        ],
    }


@router.post("/{task_id}/confirm-claims")
async def confirm_claims(
    task_id: UUID,
    request: ConfirmClaimsRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Confirm the claims to investigate, then queue the task."""
    task = await _owned_task(session, task_id, current_user)
    service = _service(session)
    # A task can be confirmed exactly once: a second confirm would re-queue an
    # already-queued task and -- for a free-trial task -- burn a second quota
    # slot on the same research. Refuse rather than double-charge.
    if task.status != "AWAITING_CLAIM_CONFIRMATION":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"task is {task.status}, not awaiting claim confirmation",
        )
    # A paper-review task's whole subject is the uploaded paper: confirming
    # without one would hand the council an empty critique (the worker's
    # understanding step has nothing to read). Refuse up front with the
    # reason rather than let the run degrade silently (CLAUDE.md 7).
    if task.task_type == "paper_review":
        pdf_object_ids = (task.user_evidence or {}).get("pdf_object_ids") or ()
        if not pdf_object_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="论文审查任务必须至少上传一篇论文，请先上传后再确认。",
            )
    # Free-trial quota (round-7): a task whose inherited config carries the
    # free-trial marker consumes one of the account's two slots the moment
    # the research actually starts -- a draft that is never confirmed costs
    # nothing, and a task the researcher later switched to their own
    # endpoint (task-level explicit config) is not trial-flagged at all.
    # The consume is atomic (UPDATE ... WHERE used < limit RETURNING), so
    # two concurrent confirmations cannot both take the last slot, and it
    # shares this transaction with confirm+queue: any failure rolls the
    # slot back.
    task_config = task.model_config or {}
    if task_config.get("is_free_trial") is True:
        consumed = await ModelSettingsRepository(session).consume_free_trial(
            current_user.id, FREE_TRIAL_LIMIT
        )
        if not consumed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=FREE_TRIAL_EXHAUSTED_MESSAGE,
            )
    try:
        claims = await service.confirm_claims(task_id, request.claim_ids)
        task_status = await service.queue(task_id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except UnconfirmedClaims as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return {
        "task_id": str(task_id),
        "status": task_status,
        # Discarded claims are returned too. CLAUDE.md 5.3 forbids removing what
        # the council once considered, so the caller can see what was set aside.
        "claims": [
            {"id": str(claim.claim_id), "status": claim.status} for claim in claims
        ],
    }


@router.post("/{task_id}/pause")
async def pause_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Keep a queued task from being claimed until it is resumed.

    Only a QUEUED task can be paused: a task already running finishes its one
    uncommitted phase sequence regardless (see ResearchService.pause), and a
    task still awaiting claim confirmation was never going to be claimed in the
    first place.
    """
    await _owned_task(session, task_id, current_user)
    try:
        new_status = await _service(session).pause(task_id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except InvalidPauseState as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {"task_id": str(task_id), "status": new_status}


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Move a paused task back to QUEUED so a worker can claim it again."""
    await _owned_task(session, task_id, current_user)
    try:
        new_status = await _service(session).resume(task_id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except InvalidPauseState as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {"task_id": str(task_id), "status": new_status}


@router.get("/{task_id}/council-preview")
async def council_preview(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Show the 7 seats' BLINDSPOT_BOUNTY-end positions while a task is halted.

    Plan phase 8.2. Read-only, and built from ``_seats()`` -- the exact
    per-seat aggregation the workspace panel already uses -- rather than a
    second implementation, so this view can never drift from what the
    council workspace shows for the same events.
    """
    task = await _owned_task(session, task_id, current_user)
    return {
        "task_id": str(task_id),
        "status": task.status,
        "seats": [dict(seat) for seat in await _seats(session, task_id)],
    }


@router.post("/{task_id}/council-guidance")
async def council_guidance(
    task_id: UUID,
    request: CouncilGuidanceRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Attach the human's advisory steer and let the worker resume the council.

    Plan phase 8.2/8.3. Only valid while the task is AWAITING_COUNCIL_INPUT;
    an empty ``guidance_text`` is a deliberate, honest "no intervention" --
    CLAUDE.md 4/8 forbid this from ever being a vote that decides scientific
    truth, so declining to steer is as valid an answer as steering.
    """
    await _owned_task(session, task_id, current_user)
    try:
        new_status = await _service(session).submit_council_guidance(
            task_id, request.guidance_text
        )
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except InvalidCouncilGuidanceState as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {"task_id": str(task_id), "status": new_status}


@router.delete("/{task_id}")
async def delete_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Permanently delete a task and every record that belongs to it.

    Session-history management (round-6): a researcher may discard a whole
    session -- queued clutter, a mistake, an obsolete run -- claims, ledger
    events, process stream, graph, audit rows and the task row itself go with
    it. Deletion is physical and irreversible, so the frontend confirms
    before calling; nothing here resurrects anything (CLAUDE.md 5.3's
    no-physical-delete rule governs quarantined *evidence*, not a researcher
    destroying their own session).

    The records span every module's tables, which is why this lives in the
    API layer rather than one module's repository: no single module owns the
    task's lifecycle. Order matters only where one child FK-references
    another (event_audits → scientific_events, source_versions → sources,
    scientist_runs/round_outputs → council_rounds); those go first via a
    join, then every task-scoped child, then the task row itself.
    """
    await _owned_task(session, task_id, current_user)
    await session.execute(
        delete(EventAuditModel).where(
            EventAuditModel.event_id.in_(
                select(ScientificEventModel.id).where(
                    ScientificEventModel.task_id == task_id
                )
            )
        )
    )
    await session.execute(
        delete(SourceVersionModel).where(
            SourceVersionModel.source_id.in_(
                select(SourceModel.id).where(SourceModel.task_id == task_id)
            )
        )
    )
    await session.execute(
        delete(ScientistRunModel).where(
            ScientistRunModel.round_id.in_(
                select(CouncilRoundModel.id).where(
                    CouncilRoundModel.task_id == task_id
                )
            )
        )
    )
    await session.execute(
        delete(RoundOutputModel).where(
            RoundOutputModel.round_id.in_(
                select(CouncilRoundModel.id).where(
                    CouncilRoundModel.task_id == task_id
                )
            )
        )
    )
    children: tuple[type[Any], ...] = (
        ScientificEventModel,
        ProcessStreamModel,
        ModelCallModel,
        ToolCallModel,
        ObjectModel,
        SourceModel,
        CouncilRoundModel,
        GraphEdgeModel,
        GraphNodeModel,
        ProjectionCheckpointModel,
        AtomicClaimModel,
        ResearchScopeModel,
    )
    for model in children:
        await session.execute(delete(model).where(model.task_id == task_id))
    await session.execute(
        delete(ResearchTaskModel).where(ResearchTaskModel.task_id == task_id)
    )
    await session.commit()
    return {"deleted": str(task_id)}


@router.get("/{task_id}")
async def get_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    task = await _owned_task(session, task_id, current_user)
    saved = await ModelSettingsRepository(session).get(current_user.id)
    return {
        "task_id": str(task.task_id),
        "question": task.question,
        "status": task.status,
        "created_by": task.created_by,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "effective_model_config": _effective_model_config(
            task.model_config, saved
        ),
    }
