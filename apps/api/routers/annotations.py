"""Human annotation workflow endpoints (C9).

Backs the missing collection pipeline that
``packages.evaluation.agreement`` was always written to consume: a researcher
freezes the blindspots/claims under review into a batch, several raters label
each item (relevant / not_relevant / unsure), and the batch reports inter-rater
agreement (Cohen's kappa for two raters, Krippendorff's alpha for three or
more). These are human judgments about the system's output -- they never enter
the Evidence Graph and the Graph Projector never reads them (AGENTS.md 5).

Mounted under ``/api`` with task-scoped and batch-scoped routes in one router.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.schemas import AnnotationCreateRequest, AnnotationLabelRequest
from packages.evaluation.annotation_store import (
    AnnotationError,
    NewAnnotationItem,
    create_batch,
    get_batch_detail,
    list_batches,
    upsert_label,
)
from packages.research.repository import ResearchRepository, TaskNotFound

router = APIRouter()


async def _owned(session: SessionDep, task_id: UUID, user: CurrentUserDep) -> None:
    """404 unless the batch's task belongs to the calling account."""
    try:
        await ResearchRepository(session).get_task(task_id, user.id)
    except TaskNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown task {task_id}",
        ) from error


@router.post("/tasks/{task_id}/annotation-batches", status_code=status.HTTP_201_CREATED)
async def create_annotation_batch(
    task_id: UUID,
    body: AnnotationCreateRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    await _owned(session, task_id, current_user)
    items = [
        NewAnnotationItem(
            ref_kind=item.ref_kind,
            ref_node_id=item.ref_node_id,
            statement=item.statement,
            position=dict(item.position) if item.position is not None else {},
        )
        for item in body.items
    ]
    try:
        batch_id = await create_batch(
            session,
            task_id,
            created_by=current_user.username,
            items=items,
            title=body.title,
            note=body.note,
        )
    except AnnotationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return {
        "batch_id": str(batch_id),
        "task_id": str(task_id),
        "item_count": len(items),
    }


@router.get("/tasks/{task_id}/annotation-batches")
async def list_annotation_batches(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> list[dict[str, Any]]:
    await _owned(session, task_id, current_user)
    return await list_batches(session, task_id)


@router.get("/annotation-batches/{batch_id}")
async def annotation_batch_detail(
    batch_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    try:
        detail = await get_batch_detail(session, batch_id)
    except AnnotationError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    await _owned(session, UUID(str(detail["task_id"])), current_user)
    return detail


@router.post("/annotation-batches/{batch_id}/labels")
async def add_annotation_label(
    batch_id: UUID,
    body: AnnotationLabelRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    try:
        detail = await get_batch_detail(session, batch_id)
    except AnnotationError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    await _owned(session, UUID(str(detail["task_id"])), current_user)
    try:
        await upsert_label(
            session,
            batch_id,
            body.item_id,
            body.rater_name,
            body.label,
            body.note,
        )
    except AnnotationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    refreshed = await get_batch_detail(session, batch_id)
    return {"ok": True, "agreement": refreshed["agreement"]}
