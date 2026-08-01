from __future__ import annotations


async def execute_round(task_id: str, phase: str) -> dict:
    return {"task_id": task_id, "phase": phase, "status": "completed"}
