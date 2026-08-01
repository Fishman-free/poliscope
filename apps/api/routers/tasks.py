from __future__ import annotations

from fastapi import APIRouter

from apps.api.schemas import CreateTaskRequest

router = APIRouter()


@router.post("/")
async def create_task(request: CreateTaskRequest) -> dict:
    return {"status": "created", "question": request.question}


@router.post("/{task_id}/confirm-claims")
async def confirm_claims(task_id: str, claim_ids: list[str]) -> dict:
    return {"status": "confirmed", "task_id": task_id}
