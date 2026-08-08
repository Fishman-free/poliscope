"""GET /api/tasks -- the session-history list endpoint.

The web sidebar replaced the "paste a task id" box with a clickable history,
so the list has to be newest-first, carry enough to label a session, and
never include evidence payloads (the workspace endpoint owns those).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.research.models import ResearchTaskModel


async def _seed_task(
    app_sessions: async_sessionmaker[AsyncSession],
    user_id: UUID,
    question: str,
    created_at: datetime,
) -> str:
    task_id = uuid4()
    async with app_sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=question,
                status="QUEUED",
                created_by="list_test",
                user_id=user_id,
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence={},
                created_at=created_at,
            )
        )
        await session.commit()
    return str(task_id)


async def test_task_list_returns_sessions_newest_first(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    user_id = UUID(account["id"])
    older = await _seed_task(
        app_sessions, user_id, "Older session", datetime.now(UTC) - timedelta(days=1)
    )
    newer = await _seed_task(app_sessions, user_id, "Newer session", datetime.now(UTC))

    response = await api_client.get("/api/tasks")
    assert response.status_code == 200
    ids = [item["task_id"] for item in response.json()]
    # The newest task is always listed (limit 50, newest first); the older one
    # may have been pushed past the limit by other tests' tasks on the shared
    # session database -- when it is present, it must come after the newer.
    assert newer in ids
    if older in ids:
        assert ids.index(newer) < ids.index(older)


async def test_task_list_row_shape(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    task_id = await _seed_task(
        app_sessions, UUID(account["id"]), "Shape check", datetime.now(UTC)
    )

    response = await api_client.get("/api/tasks")
    assert response.status_code == 200
    row = next(item for item in response.json() if item["task_id"] == task_id)
    assert row["question"] == "Shape check"
    assert row["status"] == "QUEUED"
    assert row["created_by"] == "list_test"
    assert row["created_at"] is not None
    # The list is summaries only -- no evidence payloads on the wire. The
    # model-endpoint summary (round-6: "did my settings take effect?") and
    # the last-update timestamp (queue panel) ride along, never the key.
    assert set(row) == {
        "task_id",
        "question",
        "status",
        "created_by",
        "created_at",
        "updated_at",
        "task_type",
        "effective_model_config",
    }
    assert row["task_type"] == "deep_research"
    assert "api_key" not in row["effective_model_config"]
