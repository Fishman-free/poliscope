"""Task lifecycle endpoints.

Creating a task never starts research. The task waits in
AWAITING_CLAIM_CONFIRMATION until the researcher confirms which atomic claims
the council will investigate, which is the control point CLAUDE.md 2 requires.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.routers.workspace import _seats
from apps.api.schemas import (
    ConfirmClaimsRequest,
    CouncilGuidanceRequest,
    CreateTaskRequest,
)
from packages.accounts.repository import StoredUser
from packages.knowledge.repository import KnowledgeBaseNotFound, KnowledgeRepository
from packages.models.settings import ModelSettingsRepository
from packages.research.contracts import ResearchContract
from packages.research.repository import ResearchRepository, StoredTask, TaskNotFound
from packages.research.service import (
    InvalidCouncilGuidanceState,
    InvalidPauseState,
    ResearchService,
    UnconfirmedClaims,
)
from packages.skills.repository import SkillsRepository

router = APIRouter()

TASK_NOT_FOUND = "unknown task"


def _service(session: SessionDep) -> ResearchService:
    return ResearchService(ResearchRepository(session))


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
    Summaries only -- no claims, no evidence, no model config.
    """
    tasks = await ResearchRepository(session).list_tasks(current_user.id)
    return [
        {
            "task_id": str(task.task_id),
            "question": task.question,
            "status": task.status,
            "created_by": task.created_by,
            "created_at": task.created_at.isoformat() if task.created_at else None,
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
    await _owned_task(session, task_id, current_user)
    service = _service(session)
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


@router.get("/{task_id}")
async def get_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    task = await _owned_task(session, task_id, current_user)
    return {
        "task_id": str(task.task_id),
        "question": task.question,
        "status": task.status,
        "created_by": task.created_by,
    }
