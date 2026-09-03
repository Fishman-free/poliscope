"""Round-7 free-trial usage flow: activate -> task inherits -> confirm consumes.

The quota is enforced server-side on the *moment research starts*
(``confirm-claims``), not on activation: a draft that is never confirmed
costs nothing, and a task later switched to the researcher's own endpoint
(trial marker cleared) stops drawing on the quota. Uses its own registered
account -- the shared session-level ``account`` fixture would leave its
``app_settings`` row trial-flagged for every other test file.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies import AppState
from apps.api.main import app
from packages.models.endpoint_config import ProbeResult
from packages.models.settings import AppSettingsModel
from packages.research.models import ResearchTaskModel
from tests.conftest import APP_PASSWORD, APP_ROLE, _role_url
from tests.factories import make_research_contract

FREE_TRIAL_PATH = "/api/settings/model/free-trial"
CONFIRM_PATH = "/api/tasks/{task_id}/confirm-claims"
EXHAUSTED = "免费额度已用尽，请填写你自己的api-key"


@pytest.fixture(autouse=True)
def _trial_probe_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        client: object | None = None,
    ) -> ProbeResult:
        return ProbeResult(True, "连接成功（1 ms）", 1)

    monkeypatch.setattr("apps.api.routers.settings.probe_endpoint", _ok)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-test")


@pytest_asyncio.fixture
async def trial_account(migrated_db: str) -> AsyncIterator[dict[str, Any]]:
    """A fresh account for the trial tests, so the shared account's settings
    row is never left trial-flagged for other test files."""
    state = AppState(_role_url(migrated_db, APP_ROLE, APP_PASSWORD))
    app.state.poliscope = state
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://poliscope.test"
        ) as client:
            from tests.conftest import register_user

            body = await register_user(
                client,
                f"trial-user-{uuid4().hex[:12]}",
                "trial-password",
                f"trial-{uuid4().hex[:12]}@poliscope.test",
            )
            yield body
    finally:
        await state.dispose()


@pytest_asyncio.fixture
async def trial_client(
    migrated_db: str,
    trial_account: dict[str, Any],
) -> Any:
    """An API client bound to the trial account's token."""
    state = AppState(_role_url(migrated_db, APP_ROLE, APP_PASSWORD))
    app.state.poliscope = state
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://poliscope.test"
        ) as client:
            client.headers["authorization"] = f"Bearer {trial_account['token']}"
            yield client
    finally:
        await state.dispose()


async def _create_task(client: httpx.AsyncClient) -> Any:
    contract = make_research_contract()
    response = await client.post(
        "/api/tasks", json=contract.model_dump(mode="json")
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_trial_task_inherits_marker_and_confirm_consumes_one_slot(
    trial_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    trial_account: dict[str, Any],
) -> None:
    activated = await trial_client.post(FREE_TRIAL_PATH, json={})
    assert activated.status_code == 200

    created = await _create_task(trial_client)
    task_id = created["task_id"]

    # 继承：任务行快照了 trial 标记（worker 用它构造 gateway 的 extra_body）。
    async with app_sessions() as session:
        row = await session.scalar(
            select(ResearchTaskModel).where(ResearchTaskModel.task_id == UUID(task_id))
        )
    assert row is not None
    model_config = row.model_config or {}
    assert model_config["is_free_trial"] is True
    assert model_config["extra_body"] == {"enable_thinking": True}

    # 确认一次：扣减一个额度。
    response = await trial_client.post(
        CONFIRM_PATH.format(task_id=task_id),
        json={"claim_ids": [created["suggested_claims"][0]["id"]]},
    )
    assert response.status_code == 200, response.text
    async with app_sessions() as session:
        settings = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(trial_account["id"])
            )
        )
    assert settings is not None
    assert settings.free_trial_used == 1


async def test_second_confirm_is_refused_without_consuming_again(
    trial_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    trial_account: dict[str, Any],
) -> None:
    """A task can be confirmed exactly once: re-confirming a queued task
    would burn a second slot on the same research."""
    activated = await trial_client.post(FREE_TRIAL_PATH, json={})
    assert activated.status_code == 200
    created = await _create_task(trial_client)
    first = await trial_client.post(
        CONFIRM_PATH.format(task_id=created["task_id"]),
        json={"claim_ids": [created["suggested_claims"][0]["id"]]},
    )
    assert first.status_code == 200

    second = await trial_client.post(
        CONFIRM_PATH.format(task_id=created["task_id"]),
        json={"claim_ids": [created["suggested_claims"][0]["id"]]},
    )

    assert second.status_code == 409
    async with app_sessions() as session:
        settings = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(trial_account["id"])
            )
        )
    assert settings is not None
    assert settings.free_trial_used == 1


async def test_quota_exhausted_blocks_new_tasks_and_confirmations(
    trial_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    trial_account: dict[str, Any],
) -> None:
    """The single confirm uses the only slot; a second task is refused at
    creation (the first gate), and a trial task that somehow survived is
    refused at confirm (the atomic second gate)."""
    activated = await trial_client.post(FREE_TRIAL_PATH, json={})
    assert activated.status_code == 200

    created = await _create_task(trial_client)
    response = await trial_client.post(
        CONFIRM_PATH.format(task_id=created["task_id"]),
        json={"claim_ids": [created["suggested_claims"][0]["id"]]},
    )
    assert response.status_code == 200

    # 第二任务：创建即被拒（创建时拦截）。
    refused = await trial_client.post(
        "/api/tasks", json=make_research_contract().model_dump(mode="json")
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == EXHAUSTED

    # 直接落库一个 trial 标记任务（绕过创建拦截），confirm 仍被原子扣减拒绝。
    async with app_sessions() as session:
        task_id = uuid4()
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question="bypass?",
                status="AWAITING_CLAIM_CONFIRMATION",
                created_by="trial-test",
                user_id=UUID(trial_account["id"]),
                wall_clock_minutes=60,
                model_cost_usd=0,
                tool_call_limit=10,
                source_limit=10,
                user_evidence={},
                model_config={
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "sk-dashscope-test",
                    "is_free_trial": True,
                },
            )
        )
        await session.commit()

    # 注意 claim id 必须是合法 UUID：请求体校验（UUID 解析）发生在 handler
    # 之前，非 UUID 会先 422——这里用不属于任务的合法 UUID，让原子扣减
    # （在 claims 校验之前）先拒绝。
    blocked = await trial_client.post(
        CONFIRM_PATH.format(task_id=task_id),
        json={"claim_ids": [str(uuid4())]},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == EXHAUSTED


async def test_concurrent_confirms_consume_exactly_one_slot(
    trial_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    trial_account: dict[str, Any],
) -> None:
    """The atomic UPDATE ... WHERE free_trial_used < limit gate: with a
    single slot in total, two fresh confirmations racing for it -- exactly
    one wins."""
    activated = await trial_client.post(FREE_TRIAL_PATH, json={})
    assert activated.status_code == 200

    # 唯一额度尚未使用：两个草稿任务同时确认，竞争同一个槽位。
    created_a = await _create_task(trial_client)
    created_b = await _create_task(trial_client)
    body_a = {"claim_ids": [created_a["suggested_claims"][0]["id"]]}
    body_b = {"claim_ids": [created_b["suggested_claims"][0]["id"]]}

    results = await asyncio.gather(
        trial_client.post(
            CONFIRM_PATH.format(task_id=created_a["task_id"]), json=body_a
        ),
        trial_client.post(
            CONFIRM_PATH.format(task_id=created_b["task_id"]), json=body_b
        ),
    )

    codes = sorted(response.status_code for response in results)
    assert codes == [200, 403]
    async with app_sessions() as session:
        settings = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(trial_account["id"])
            )
        )
    assert settings is not None
    # 并发中恰好一次成功；失败的那一次回滚不扣，唯一额度被用掉后 used==1。
    assert settings.free_trial_used == 1


async def test_switching_to_own_key_stops_drawing_on_quota(
    trial_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    trial_account: dict[str, Any],
) -> None:
    """A task created after the researcher saved their own endpoint is not
    trial-flagged, so it neither draws on the quota nor is blocked by it."""
    activated = await trial_client.post(FREE_TRIAL_PATH, json={})
    assert activated.status_code == 200
    saved = await trial_client.put(
        "/api/settings/model",
        json={"base_url": "https://own.example", "api_key": "sk-own"},
    )
    assert saved.status_code == 200
    assert saved.json()["free_trial"]["active"] is False

    created = await _create_task(trial_client)
    assert created["task_id"]
    async with app_sessions() as session:
        row = await session.scalar(
            select(ResearchTaskModel).where(
                ResearchTaskModel.task_id == UUID(created["task_id"])
            )
        )
    assert row is not None
    assert (row.model_config or {}).get("is_free_trial") is not True

    response = await trial_client.post(
        CONFIRM_PATH.format(task_id=created["task_id"]),
        json={"claim_ids": [created["suggested_claims"][0]["id"]]},
    )
    assert response.status_code == 200
    async with app_sessions() as session:
        settings = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(trial_account["id"])
            )
        )
    assert settings is not None
    assert settings.free_trial_used == 0
