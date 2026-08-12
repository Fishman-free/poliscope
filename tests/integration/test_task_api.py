"""Tests for the task lifecycle endpoints.

The earlier version constructed the request DTOs and asserted Pydantic had
stored their fields, which exercised Pydantic rather than Poliscope. These tests
drive the real routes and assert the state the database ends up in.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.schemas import ConfirmClaimsRequest, CreateTaskRequest
from packages.epistemo.contracts import TaskStatus
from packages.research.models import ResearchTaskModel
from tests.factories import make_research_contract


def _contract_payload() -> dict[str, Any]:
    payload: dict[str, Any] = make_research_contract().model_dump(mode="json")
    return payload


async def _create(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/tasks", json=_contract_payload())
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_request_dtos_accept_the_contract_shape() -> None:
    """The wire contract and the domain contract must stay compatible."""
    payload = _contract_payload()
    request = CreateTaskRequest(
        question=payload["question"],
        scope=payload["scope"],
        budget=payload["budget"],
        user_evidence=payload["user_evidence"],
    )
    assert request.question == payload["question"]
    assert len(ConfirmClaimsRequest(claim_ids=(uuid4(), uuid4())).claim_ids) == 2


async def test_create_returns_suggested_claims_and_does_not_queue(
    api_client: httpx.AsyncClient,
) -> None:
    """CLAUDE.md 2 keeps the researcher in control of what gets investigated."""
    body = await _create(api_client)
    assert body["status"] == TaskStatus.AWAITING_CLAIM_CONFIRMATION
    assert len(body["suggested_claims"]) >= 2
    assert all(
        {"id", "statement", "claim_type", "falsification_condition"} <= set(claim)
        for claim in body["suggested_claims"]
    )


async def test_created_task_is_readable_back(
    api_client: httpx.AsyncClient,
) -> None:
    """A task that only existed in the creating process would not be resumable."""
    created = await _create(api_client)
    response = await api_client.get(f"/api/tasks/{created['task_id']}")
    assert response.status_code == 200
    assert response.json()["question"] == _contract_payload()["question"]


async def test_confirming_claims_queues_the_task(
    api_client: httpx.AsyncClient,
) -> None:
    created = await _create(api_client)
    chosen = created["suggested_claims"][0]["id"]
    response = await api_client.post(
        f"/api/tasks/{created['task_id']}/confirm-claims",
        json={"claim_ids": [chosen]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == TaskStatus.QUEUED

    readback = await api_client.get(f"/api/tasks/{created['task_id']}")
    assert readback.json()["status"] == TaskStatus.QUEUED


async def test_unconfirmed_claims_are_reported_as_discarded_not_dropped(
    api_client: httpx.AsyncClient,
) -> None:
    """CLAUDE.md 5.3 forbids removing what the council once considered."""
    created = await _create(api_client)
    suggested = created["suggested_claims"]
    chosen = suggested[0]["id"]
    body = (
        await api_client.post(
            f"/api/tasks/{created['task_id']}/confirm-claims",
            json={"claim_ids": [chosen]},
        )
    ).json()
    statuses = {claim["id"]: claim["status"] for claim in body["claims"]}
    assert len(statuses) == len(suggested)
    assert statuses[chosen] == "CONFIRMED"
    assert all(
        statuses[claim["id"]] == "DISCARDED"
        for claim in suggested
        if claim["id"] != chosen
    )


async def test_confirming_a_claim_from_another_task_is_refused(
    api_client: httpx.AsyncClient,
) -> None:
    """A claim id is not a capability to modify an unrelated task."""
    first = await _create(api_client)
    second = await _create(api_client)
    response = await api_client.post(
        f"/api/tasks/{first['task_id']}/confirm-claims",
        json={"claim_ids": [second["suggested_claims"][0]["id"]]},
    )
    assert response.status_code == 422
    assert "do not belong to task" in response.json()["detail"]


async def test_confirming_no_claims_is_refused(
    api_client: httpx.AsyncClient,
) -> None:
    created = await _create(api_client)
    response = await api_client.post(
        f"/api/tasks/{created['task_id']}/confirm-claims",
        json={"claim_ids": []},
    )
    assert response.status_code == 422


async def test_confirming_claims_on_an_unknown_task_returns_404(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        f"/api/tasks/{uuid4()}/confirm-claims",
        json={"claim_ids": [str(uuid4())]},
    )
    assert response.status_code == 404


async def test_unknown_task_returns_404(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get(f"/api/tasks/{uuid4()}")).status_code == 404


async def test_pausing_a_queued_task_then_resuming_returns_it_to_queued(
    api_client: httpx.AsyncClient,
) -> None:
    created = await _create(api_client)
    chosen = created["suggested_claims"][0]["id"]
    await api_client.post(
        f"/api/tasks/{created['task_id']}/confirm-claims",
        json={"claim_ids": [chosen]},
    )

    paused = await api_client.post(f"/api/tasks/{created['task_id']}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == TaskStatus.PAUSED
    readback = await api_client.get(f"/api/tasks/{created['task_id']}")
    assert readback.json()["status"] == TaskStatus.PAUSED

    resumed = await api_client.post(f"/api/tasks/{created['task_id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == TaskStatus.QUEUED
    readback = await api_client.get(f"/api/tasks/{created['task_id']}")
    assert readback.json()["status"] == TaskStatus.QUEUED


async def test_pausing_a_task_awaiting_claim_confirmation_is_refused(
    api_client: httpx.AsyncClient,
) -> None:
    """A task nothing was ever going to claim cannot be meaningfully paused."""
    created = await _create(api_client)
    response = await api_client.post(f"/api/tasks/{created['task_id']}/pause")
    assert response.status_code == 409
    assert "not" in response.json()["detail"]


async def test_resuming_a_task_that_is_not_paused_is_refused(
    api_client: httpx.AsyncClient,
) -> None:
    created = await _create(api_client)
    chosen = created["suggested_claims"][0]["id"]
    await api_client.post(
        f"/api/tasks/{created['task_id']}/confirm-claims",
        json={"claim_ids": [chosen]},
    )
    response = await api_client.post(f"/api/tasks/{created['task_id']}/resume")
    assert response.status_code == 409


async def test_pausing_an_unknown_task_returns_404(
    api_client: httpx.AsyncClient,
) -> None:
    assert (
        await api_client.post(f"/api/tasks/{uuid4()}/pause")
    ).status_code == 404


async def test_resuming_an_unknown_task_returns_404(
    api_client: httpx.AsyncClient,
) -> None:
    assert (
        await api_client.post(f"/api/tasks/{uuid4()}/resume")
    ).status_code == 404


async def test_re_research_moves_a_failed_task_back_to_queued(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """「重新研究」(round-8/13): a FAILED task with no checkpoint has no gap to
    rewind to, so the re-run is a **fresh task** -- the original's committed
    ledger events would collide with a same-task restart (round-13 production
    failure). The response carries the fresh task id and the original keeps
    its FAILED state as audit history."""
    created = await _create(api_client)
    task_id = created["task_id"]
    chosen = created["suggested_claims"][0]["id"]
    await api_client.post(
        f"/api/tasks/{task_id}/confirm-claims",
        json={"claim_ids": [chosen]},
    )

    # Force the task into FAILED the way the worker's watchdog would.
    async with app_sessions() as session:
        await session.execute(
            update(ResearchTaskModel)
            .where(ResearchTaskModel.task_id == task_id)
            .values(status=TaskStatus.FAILED)
        )
        await session.commit()

    response = await api_client.post(f"/api/tasks/{task_id}/re-research")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == TaskStatus.QUEUED
    fresh_id = body["task_id"]
    assert fresh_id != task_id

    # 原任务保持 FAILED；新任务是继承同一问题的全新任务。
    readback = await api_client.get(f"/api/tasks/{task_id}")
    assert readback.json()["status"] == TaskStatus.FAILED
    fresh = await api_client.get(f"/api/tasks/{fresh_id}")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["question"] == _contract_payload()["question"]
    assert fresh.json()["status"] == TaskStatus.QUEUED


async def test_re_research_refused_for_non_failed_task(
    api_client: httpx.AsyncClient,
) -> None:
    """A task that is not FAILED cannot be re-researched (409)."""
    created = await _create(api_client)
    response = await api_client.post(
        f"/api/tasks/{created['task_id']}/re-research"
    )
    assert response.status_code == 409


async def test_re_research_unknown_task_returns_404(
    api_client: httpx.AsyncClient,
) -> None:
    assert (
        await api_client.post(f"/api/tasks/{uuid4()}/re-research")
    ).status_code == 404


async def test_rerun_fresh_creates_a_brand_new_queued_task(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """从头研究（round-13）：不是把原任务重新入队，而是创建**全新任务**
    ——新 task_id、直接 QUEUED、继承问题——从独立预承诺真正重新开始。"""
    created = await _create(api_client)
    task_id = created["task_id"]
    chosen = created["suggested_claims"][0]["id"]
    await api_client.post(
        f"/api/tasks/{task_id}/confirm-claims",
        json={"claim_ids": [chosen]},
    )
    async with app_sessions() as session:
        await session.execute(
            update(ResearchTaskModel)
            .where(ResearchTaskModel.task_id == task_id)
            .values(status=TaskStatus.FAILED)
        )
        await session.commit()

    response = await api_client.post(f"/api/tasks/{task_id}/rerun-fresh")
    assert response.status_code == 200, response.text
    body = response.json()
    fresh_id = body["task_id"]
    assert fresh_id != task_id
    assert body["source_task_id"] == task_id
    assert body["status"] == TaskStatus.QUEUED

    readback = await api_client.get(f"/api/tasks/{fresh_id}")
    assert readback.status_code == 200, readback.text
    assert readback.json()["question"] == _contract_payload()["question"]
    assert readback.json()["status"] == TaskStatus.QUEUED

    # 原任务仍是 FAILED —— 从头研究保留审计历史，不动旧任务。
    original = await api_client.get(f"/api/tasks/{task_id}")
    assert original.json()["status"] == TaskStatus.FAILED


async def test_rerun_fresh_refused_for_non_failed_task(
    api_client: httpx.AsyncClient,
) -> None:
    """A task that is not FAILED/CANCELLED cannot start a fresh rerun (409)."""
    created = await _create(api_client)
    response = await api_client.post(
        f"/api/tasks/{created['task_id']}/rerun-fresh"
    )
    assert response.status_code == 409


async def test_rerun_fresh_unknown_task_returns_404(
    api_client: httpx.AsyncClient,
) -> None:
    assert (
        await api_client.post(f"/api/tasks/{uuid4()}/rerun-fresh")
    ).status_code == 404


async def test_an_invalid_contract_is_rejected_with_422(
    api_client: httpx.AsyncClient,
) -> None:
    payload = _contract_payload()
    payload["budget"]["wall_clock_minutes"] = 0
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 422


async def test_a_scope_with_an_inverted_date_range_is_rejected(
    api_client: httpx.AsyncClient,
) -> None:
    """The contract validator must run on the API path, not only in unit tests."""
    payload = _contract_payload()
    payload["scope"]["date_from"] = "2030-01-01"
    payload["scope"]["date_until"] = "2020-01-01"
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 422


async def test_output_language_is_detected_from_a_chinese_question(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Round-4 language following: a Chinese question stores zh-Hans without
    the client having to say so; an English question stores en."""
    payload = _contract_payload()
    payload["question"] = "中国大陆地区青少年自杀率和学习成绩是否具有显著关系？"
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    async with app_sessions() as session:
        row = await session.scalar(
            select(ResearchTaskModel).where(
                ResearchTaskModel.task_id == task_id
            )
        )
    assert row is not None
    assert row.output_language == "zh-Hans"


async def test_explicit_output_language_wins_over_detection(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """An explicit zh-Hant / en from the client overrides auto-detection."""
    payload = _contract_payload()
    payload["question"] = "Does X cause Y?"
    payload["output_language"] = "zh-Hant"
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    async with app_sessions() as session:
        row = await session.scalar(
            select(ResearchTaskModel).where(
                ResearchTaskModel.task_id == task_id
            )
        )
    assert row is not None
    assert row.output_language == "zh-Hant"


async def test_create_with_model_config_stores_it_and_never_echoes_it(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A researcher's own endpoint is stored on the task row for the worker,
    and the api key is never returned by any endpoint (CLAUDE.md 16)."""
    payload = _contract_payload()
    payload["task_model_config"] = {
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-task-secret",
        "model_name": "deepseek-chat",
    }
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    async with app_sessions() as session:
        row = (
            await session.execute(
                select(ResearchTaskModel).where(
                    ResearchTaskModel.task_id == task_id
                )
            )
        ).scalar_one()
    assert row.model_config is not None
    assert row.model_config["base_url"] == "https://api.deepseek.com"
    assert row.model_config["api_key"] == "sk-task-secret"
    assert row.model_config["model_name"] == "deepseek-chat"

    # 不回显：任何读端点都不出现密钥值（has_api_key 布尔字段允许存在）
    response = await api_client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    assert "sk-task-secret" not in response.text


async def test_model_config_without_a_url_scheme_is_normalized(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A scheme-less endpoint is not rejected -- it is normalised to https://
    before storage. (The old behaviour rejected it; user input is no longer
    stored verbatim since the console-portal incident, see
    packages/models/endpoint_config.py.)"""
    payload = _contract_payload()
    payload["task_model_config"] = {"base_url": "api.deepseek.com", "api_key": "sk-x"}
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    async with app_sessions() as session:
        row = (
            await session.execute(
                select(ResearchTaskModel).where(
                    ResearchTaskModel.task_id == task_id
                )
            )
        ).scalar_one()
    assert row.model_config is not None
    assert row.model_config["base_url"] == "https://api.deepseek.com"


async def test_create_with_dois_and_bibtex_stores_them(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The researcher's own evidence must survive the round trip into the
    task row -- a stored-but-never-read entry is the silent data loss
    CLAUDE.md 7 forbids (the worker consumption is pinned in
    test_user_evidence_consumption.py)."""
    payload = _contract_payload()
    payload["user_evidence"] = {
        "dois": ["10.1000/a", "10.1000/b"],
        "bibtex_entries": ["@article{x, doi = {10.1000/c}}"],
        "pdf_object_ids": [],
    }
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    async with app_sessions() as session:
        row = (
            await session.execute(
                select(ResearchTaskModel).where(
                    ResearchTaskModel.task_id == task_id
                )
            )
        ).scalar_one()
    assert row.user_evidence["dois"] == ["10.1000/a", "10.1000/b"]
    assert row.user_evidence["bibtex_entries"] == [
        "@article{x, doi = {10.1000/c}}"
    ]


async def _upload_pdf(
    api_client: httpx.AsyncClient,
    task_id: str,
    content: bytes,
    filename: str = "paper.pdf",
) -> httpx.Response:
    return await api_client.post(
        f"/api/tasks/{task_id}/papers/upload",
        files={"file": (filename, content, "application/pdf")},
    )


async def test_upload_rejects_empty_file(
    api_client: httpx.AsyncClient,
) -> None:
    body = await _create(api_client)
    response = await _upload_pdf(api_client, body["task_id"], b"")
    assert response.status_code == 422
    assert "为空" in response.text


async def test_upload_rejects_non_pdf_bytes(
    api_client: httpx.AsyncClient,
) -> None:
    """Round-7: the gate is extraction, not a PDF magic-byte claim -- bytes
    that cannot become text (here a fake .pdf body) are refused with the
    parse reason up front instead of a silent gap in the worker."""
    body = await _create(api_client)
    response = await _upload_pdf(
        api_client, body["task_id"], b"PK\x03\x04 not a pdf at all"
    )
    assert response.status_code == 422
    assert "无法读取上传的文件" in response.text


async def test_upload_rejects_oversized_file(
    api_client: httpx.AsyncClient,
) -> None:
    """Above the 20 MB ceiling the endpoint refuses on sight -- the nginx
    layer already 413s the same request in the compose stack, this is the
    authoritative check for deployments without that nginx."""
    body = await _create(api_client)
    response = await _upload_pdf(
        api_client, body["task_id"], b"%PDF" + b"\0" * (21 * 1024 * 1024)
    )
    assert response.status_code == 422
    assert "20 MB" in response.text


async def test_upload_accepts_a_valid_pdf_and_attaches_it(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    import fitz  # type: ignore[import-untyped]

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "A real, parseable PDF.")
    content = bytes(document.tobytes())

    body = await _create(api_client)
    response = await _upload_pdf(api_client, body["task_id"], content)
    assert response.status_code == 201, response.text
    object_id = response.json()["object_id"]

    async with app_sessions() as session:
        row = (
            await session.execute(
                select(ResearchTaskModel).where(
                    ResearchTaskModel.task_id == body["task_id"]
                )
            )
        ).scalar_one()
    assert row.user_evidence["pdf_object_ids"] == [object_id]


async def test_list_reports_effective_model_config(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-6 fix: a task without explicit config reports `source: default`;
    once the account saves a full endpoint, newly created tasks report the
    saved endpoint as `source: saved` -- the researcher can finally verify
    their settings actually take effect."""
    async def _create_task() -> str:
        body = await _create(api_client)
        return str(body["task_id"])

    plain_task = await _create_task()
    listing = (await api_client.get("/api/tasks")).json()
    plain_entry = next(t for t in listing if t["task_id"] == plain_task)
    assert plain_entry["effective_model_config"]["source"] == "default"
    assert plain_entry["effective_model_config"]["has_api_key"] is False
    assert "api_key" not in plain_entry["effective_model_config"]

    # 保存被连接门控：测试环境到不了真实端点，先 stub 探测成功再 PUT。
    from packages.models.endpoint_config import ProbeResult

    async def _ok(
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        client: object | None = None,
    ) -> ProbeResult:
        return ProbeResult(True, "连接成功（1 ms）", 1)

    monkeypatch.setattr("apps.api.routers.settings.probe_endpoint", _ok)

    save = await api_client.put(
        "/api/settings/model",
        json={
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-account",
            "model_name": "deepseek-chat",
        },
    )
    assert save.status_code == 200, save.text

    saved_task = await _create_task()
    listing = (await api_client.get("/api/tasks")).json()
    saved_entry = next(t for t in listing if t["task_id"] == saved_task)
    config = saved_entry["effective_model_config"]
    assert config["source"] == "saved"
    assert config["base_url"] == "https://api.deepseek.com"
    assert config["model_name"] == "deepseek-chat"
    assert config["has_api_key"] is True
    assert "sk-account" not in str(listing)

    # 详情接口同样带上生效配置。
    detail = (await api_client.get(f"/api/tasks/{saved_task}")).json()
    assert detail["effective_model_config"]["source"] == "saved"
    assert detail["effective_model_config"]["base_url"] == "https://api.deepseek.com"

    # 清理：这个测试运行在共享的 session 级账号上，留下的设置会改变其他
    # 测试文件（及其它运行顺序）中任务创建的继承行为——测试不得依赖文件
    # 收集顺序。
    cleared = await api_client.put(
        "/api/settings/model", json={"clear_api_key": True}
    )
    assert cleared.status_code == 200


async def test_delete_task_removes_the_task_and_its_records(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Round-6 session management: deleting a session must remove the task row
    and every child record (claims, ledger events, process stream, graph...),
    and only the owner may delete."""
    from packages.evidence.models import ScientificEventModel

    created = await _create(api_client)
    task_id = created["task_id"]

    async with app_sessions() as session:
        session.add(
            ScientificEventModel(
                id=uuid4(),
                task_id=task_id,
                event_type="PHASE_STARTED",
                payload={"phase": "PRECOMMITMENT"},
                idempotency_key="test:delete:1",
                sequence=1,
                status="accepted",
            )
        )
        await session.commit()

    deleted = await api_client.delete(f"/api/tasks/{task_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] == task_id

    async with app_sessions() as session:
        row = await session.scalar(
            select(ResearchTaskModel).where(
                ResearchTaskModel.task_id == task_id
            )
        )
        assert row is None
        events = await session.scalar(
            select(ScientificEventModel).where(
                ScientificEventModel.task_id == task_id
            )
        )
        assert events is None

    gone = await api_client.get(f"/api/tasks/{task_id}")
    assert gone.status_code == 404

    # 删除不存在的任务 → 404，绝不误删别人的任务。
    unknown = await api_client.delete(f"/api/tasks/{uuid4()}")
    assert unknown.status_code == 404


async def _running_task(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> str:
    """创建 → 确认主张（入队）→ 直接置 RUNNING（worker 认领后的状态）。"""
    created = await _create(api_client)
    task_id = created["task_id"]
    chosen = created["suggested_claims"][0]["id"]
    confirmed = await api_client.post(
        f"/api/tasks/{task_id}/confirm-claims",
        json={"claim_ids": [chosen]},
    )
    assert confirmed.status_code == 200, confirmed.text
    async with app_sessions() as session:
        await session.execute(
            update(ResearchTaskModel)
            .where(ResearchTaskModel.task_id == task_id)
            .values(status=TaskStatus.RUNNING)
        )
        await session.commit()
    return str(task_id)


async def test_delete_running_task_stops_worker_then_deletes(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-14: RUNNING 任务的 worker 持有任务行锁直到 run 结束，直接删会
    无限等待。端点必须先经停止侧信道请 worker 停下，再等锁释放后删除——
    这里 monkeypatch ``get_status`` 模拟 worker 已响应停止（状态离开 RUNNING），
    并记录停止请求确实发出。"""
    from packages.research.repository import ResearchRepository

    task_id = await _running_task(api_client, app_sessions)
    requested: list[str] = []
    original_request_cancel = ResearchRepository.request_cancel

    async def _record_cancel(
        self: ResearchRepository, task_id: UUID, requested_by: str
    ) -> None:
        requested.append(str(task_id))
        await original_request_cancel(self, task_id, requested_by)

    async def _stopped(self: ResearchRepository, task_id: UUID) -> TaskStatus:
        return TaskStatus.CANCELLED  # 模拟 worker 已停止

    monkeypatch.setattr(ResearchRepository, "request_cancel", _record_cancel)
    monkeypatch.setattr(ResearchRepository, "get_status", _stopped)
    monkeypatch.setattr("apps.api.routers.tasks.DELETE_POLL_SECONDS", 0.01)

    deleted = await api_client.delete(f"/api/tasks/{task_id}")
    assert deleted.status_code == 200, deleted.text
    assert requested == [task_id]

    async with app_sessions() as session:
        row = await session.scalar(
            select(ResearchTaskModel).where(
                ResearchTaskModel.task_id == task_id
            )
        )
        assert row is None


async def test_delete_running_task_with_dead_worker_still_deletes(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-14: worker 已死（连接断开、锁已自动释放）但状态仍是 RUNNING——
    轮询耗尽后直接尝试删除，锁不存在所以成功；绝不无限等待。"""
    task_id = await _running_task(api_client, app_sessions)
    monkeypatch.setattr("apps.api.routers.tasks.DELETE_CANCEL_WAIT_SECONDS", 0.1)
    monkeypatch.setattr("apps.api.routers.tasks.DELETE_POLL_SECONDS", 0.01)

    deleted = await api_client.delete(f"/api/tasks/{task_id}")
    assert deleted.status_code == 200, deleted.text

    async with app_sessions() as session:
        row = await session.scalar(
            select(ResearchTaskModel).where(
                ResearchTaskModel.task_id == task_id
            )
        )
        assert row is None


async def test_delete_answers_409_when_worker_lock_does_not_release(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-14 兜底：任务行仍被另一事务锁住（卡死的 worker）时，级联删除的
    lock_timeout 让请求快速失败为 409 明确提示，而不是无限挂起或 500。"""
    task_id = await _running_task(api_client, app_sessions)
    monkeypatch.setattr("apps.api.task_lifecycle.DELETE_LOCK_TIMEOUT", "0.5s")
    # RUNNING 先走停止轮询：这里轮询也必须缩短，否则先等满 60s 才到锁兜底。
    monkeypatch.setattr("apps.api.routers.tasks.DELETE_CANCEL_WAIT_SECONDS", 0.1)
    monkeypatch.setattr("apps.api.routers.tasks.DELETE_POLL_SECONDS", 0.01)

    holder = app_sessions()
    await holder.execute(
        select(ResearchTaskModel)
        .where(ResearchTaskModel.task_id == task_id)
        .with_for_update()
    )
    try:
        deleted = await api_client.delete(f"/api/tasks/{task_id}")
        assert deleted.status_code == 409, deleted.text
    finally:
        await holder.rollback()
        await holder.close()

    # 锁释放后删除即可成功——409 是暂时占用的诚实回答，不是永久拒绝。
    retry = await api_client.delete(f"/api/tasks/{task_id}")
    assert retry.status_code == 200, retry.text
