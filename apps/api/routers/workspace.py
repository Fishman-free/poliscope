from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/{task_id}")
async def get_workspace(task_id: str) -> dict:
    return {"task_id": task_id, "status": "running"}
