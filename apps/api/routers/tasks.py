"""Task lifecycle endpoints.

Creating a task never starts research. The task waits in
AWAITING_CLAIM_CONFIRMATION until the researcher confirms which atomic claims
the council will investigate, which is the control point CLAUDE.md 2 requires.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import SessionDep
from apps.api.schemas import ConfirmClaimsRequest, CreateTaskRequest
from packages.research.contracts import ResearchContract
from packages.research.repository import ResearchRepository, TaskNotFound
from packages.research.service import (
    InvalidPauseState,
    ResearchService,
    UnconfirmedClaims,
)

router = APIRouter()

TASK_NOT_FOUND = "unknown task"


def _service(session: SessionDep) -> ResearchService:
    return ResearchService(ResearchRepository(session))


def _not_found(task_id: UUID, error: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{TASK_NOT_FOUND} {task_id}",
    )


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_task(
    request: CreateTaskRequest,
    session: SessionDep,
) -> dict[str, Any]:
    """Create a task and return the atomic claims it suggests."""
    try:
        contract = ResearchContract.model_validate(
            {
                "question": request.question,
                "scope": dict(request.scope),
                "budget": dict(request.budget),
                "user_evidence": dict(request.user_evidence),
            }
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    created = await _service(session).create(contract)
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
) -> dict[str, Any]:
    """Confirm the claims to investigate, then queue the task."""
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
async def pause_task(task_id: UUID, session: SessionDep) -> dict[str, Any]:
    """Keep a queued task from being claimed until it is resumed.

    Only a QUEUED task can be paused: a task already running finishes its one
    uncommitted phase sequence regardless (see ResearchService.pause), and a
    task still awaiting claim confirmation was never going to be claimed in the
    first place.
    """
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
async def resume_task(task_id: UUID, session: SessionDep) -> dict[str, Any]:
    """Move a paused task back to QUEUED so a worker can claim it again."""
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


@router.get("/{task_id}")
async def get_task(task_id: UUID, session: SessionDep) -> dict[str, Any]:
    try:
        task = await _service(session).get_task(task_id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    return {
        "task_id": str(task.task_id),
        "question": task.question,
        "status": task.status,
        "created_by": task.created_by,
    }
