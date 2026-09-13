"""Integration tests for the research-tools endpoints.

Covers the HTTP surface added for A1-A4 / B6 / C9-C10 / D12:
- A2 read-only share mint / public read / revoke (redaction included);
- A3 replay status gate and the deterministic claim-set compare;
- A4 save-to-knowledge terminal-state gate;
- B6 researcher adjudication appending a PROCESS-only ledger event;
- C10 model hot-swap state gate (draft editable, RUNNING refused 409).

Everything goes over HTTP against the real ASGI app and a real PostgreSQL
container, so routing, dependency injection and role grants are all exercised.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.epistemo.contracts import TaskStatus
from packages.evidence.models import ScientificEventModel
from packages.research.models import ResearchTaskModel
from packages.research.repository import ResearchRepository
from tests.factories import make_research_contract


async def _create_task(client: httpx.AsyncClient) -> str:
    contract = make_research_contract().model_dump(mode="json")
    response = await client.post("/api/tasks", json=contract)
    assert response.status_code == 201, response.text
    return str(response.json()["task_id"])


# --- A2 read-only share ----------------------------------------------------


async def test_share_mint_public_read_and_revoke(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)

    minted = await api_client.post(f"/api/tasks/{task_id}/share", json={})
    assert minted.status_code == 200, minted.text
    token = minted.json()["share_token"]
    assert token and isinstance(token, str)

    # The public endpoint resolves the token WITHOUT authentication semantics
    # (the authed test client still proves the route works; the point is the
    # route uses no CurrentUserDep) and returns a redacted snapshot.
    public = await api_client.get(f"/api/shared/{token}")
    assert public.status_code == 200, public.text
    body = public.json()
    assert body["task"]["question"]
    # Share metadata and model configuration must never reach a public reader.
    assert "share_token" not in body["task"]
    assert "effective_model_config" not in body["task"]
    assert body["usage"] is None

    revoked = await api_client.delete(f"/api/tasks/{task_id}/share")
    assert revoked.status_code == 200, revoked.text
    gone = await api_client.get(f"/api/shared/{token}")
    assert gone.status_code == 404


async def test_unknown_share_token_is_404(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(f"/api/shared/does-not-exist-{uuid4()}")
    assert response.status_code == 404


# --- A3 time travel --------------------------------------------------------


async def test_replay_refuses_a_task_that_is_not_finished(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)  # sits at AWAITING_CLAIM_CONFIRMATION
    response = await api_client.post(
        f"/api/tasks/{task_id}/replay", json={"corpus_cutoff": "2018-12-31"}
    )
    assert response.status_code == 409, response.text


async def test_replay_clones_a_finished_task_with_a_knowledge_base(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """时间旅行复跑必须真的跑得起来（生产 500 回归）。

    ``replay_at_cutoff`` 只是 ``rerun_fresh`` 的薄封装，所以它继承了同一个
    缺陷：从数据库读回的 UUID 是 asyncpg 的子类，重建契约时被
    ``ContractModel`` 的精确类型叶子检查拒绝，接口 500。之前这里只测了
    「未完成任务被拒」（409），成功路径从未跑过，缺陷因此躲过了整套测试。
    """
    knowledge_base = await api_client.post(
        "/api/knowledge-bases", json={"name": "replay-kb"}
    )
    assert knowledge_base.status_code == 201, knowledge_base.text
    knowledge_base_id = knowledge_base.json()["id"]

    contract = make_research_contract().model_dump(mode="json")
    contract["knowledge_base_id"] = knowledge_base_id
    created = await api_client.post("/api/tasks", json=contract)
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    chosen = created.json()["suggested_claims"][0]["id"]
    await api_client.post(
        f"/api/tasks/{task_id}/confirm-claims", json={"claim_ids": [chosen]}
    )
    async with app_sessions() as session:
        await session.execute(
            update(ResearchTaskModel)
            .where(ResearchTaskModel.task_id == task_id)
            .values(status=TaskStatus.COMPLETED)
        )
        await session.commit()

    response = await api_client.post(
        f"/api/tasks/{task_id}/replay", json={"corpus_cutoff": "2018-12-31"}
    )
    assert response.status_code == 200, response.text
    replay_id = response.json()["task_id"]
    assert replay_id != task_id

    async with app_sessions() as session:
        replay = await ResearchRepository(session).get_task(UUID(replay_id))
    assert replay.status == TaskStatus.QUEUED
    assert replay.corpus_cutoff == date(2018, 12, 31)
    assert replay.replay_of_task_id == UUID(task_id)
    assert replay.knowledge_base_id == UUID(knowledge_base_id)


async def test_compare_two_owned_tasks_returns_set_difference(
    api_client: httpx.AsyncClient,
) -> None:
    left = await _create_task(api_client)
    right = await _create_task(api_client)
    response = await api_client.get(f"/api/tasks/{left}/compare/{right}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_a"]["task_id"] == left
    assert body["task_b"]["task_id"] == right
    assert isinstance(body["shared"], list)
    assert isinstance(body["only_in_a"], list)
    assert isinstance(body["only_in_b"], list)


# --- A4 save to knowledge --------------------------------------------------


async def test_save_to_knowledge_requires_terminal_task(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)
    response = await api_client.post(
        f"/api/tasks/{task_id}/save-to-knowledge",
        json={"knowledge_base_id": str(uuid4())},
    )
    # Draft task: the state gate fires before any knowledge-base lookup.
    assert response.status_code == 409, response.text


# --- B6 researcher adjudication -------------------------------------------


async def test_adjudication_appends_a_process_event_only(
    api_client: httpx.AsyncClient,
    app_session: AsyncSession,
) -> None:
    task_id = await _create_task(api_client)
    response = await api_client.post(
        f"/api/tasks/{task_id}/adjudicate",
        json={"target_key": "candidate-1", "decision": "保持分离", "note": "x"},
    )
    assert response.status_code == 200, response.text

    rows = (
        await app_session.execute(
            select(ScientificEventModel).where(
                ScientificEventModel.task_id == UUID(task_id)
            )
        )
    ).scalars().all()
    adjudications = [row for row in rows if row.event_type == "RESEARCHER_ADJUDICATION"]
    assert len(adjudications) == 1
    assert adjudications[0].payload["target_key"] == "candidate-1"
    assert adjudications[0].payload["decision"] == "保持分离"


async def test_adjudication_rejects_blank_target(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)
    response = await api_client.post(
        f"/api/tasks/{task_id}/adjudicate",
        json={"target_key": "  ", "decision": ""},
    )
    assert response.status_code == 422


# --- C10 model hot-swap ----------------------------------------------------


async def test_model_overridable_while_draft_and_cleared(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)  # draft state is swappable
    applied = await api_client.put(
        f"/api/tasks/{task_id}/model-override",
        json={
            "config": {
                "base_url": "https://example.invalid/v1",
                "api_key": "sk-test",
                "model_name": "m1",
            },
            "clear": False,
        },
    )
    assert applied.status_code == 200, applied.text
    cleared = await api_client.put(
        f"/api/tasks/{task_id}/model-override", json={"config": None, "clear": True}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["override"] is None


async def test_model_override_refused_while_running(
    api_client: httpx.AsyncClient,
    app_session: AsyncSession,
    account: dict[str, Any],
) -> None:
    from packages.research.models import ResearchTaskModel

    task_id = uuid4()
    app_session.add(
        ResearchTaskModel(
            id=uuid4(),
            task_id=task_id,
            question="running task",
            status="RUNNING",
            created_by="test_harness",
            user_id=UUID(account["id"]),
            wall_clock_minutes=60,
            model_cost_usd=Decimal("10.0000"),
            tool_call_limit=100,
            source_limit=50,
            user_evidence={},
        )
    )
    # Commit (not just flush): the API reads through a separate role
    # connection and cannot see an uncommitted transaction.
    await app_session.commit()

    response = await api_client.put(
        f"/api/tasks/{task_id}/model-override",
        json={"config": None, "clear": True},
    )
    assert response.status_code == 409, response.text
