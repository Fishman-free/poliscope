"""Permanent model settings: save, never echo the key, auto-apply to tasks.

The right-side settings panel persists the researcher's model endpoint once;
`POST /api/tasks` applies it to tasks that carry no explicit per-task config.
The API key is the load-bearing privacy constraint here: it is stored like
the per-task model_config already is, but no response may ever contain it
(CLAUDE.md 16) -- clients learn only `has_api_key`.

Since the connection-gate incident (a console-portal URL saved as the API
endpoint sent the whole council absent), saving is additionally gated on a
successful connectivity probe: ``PUT /model`` refuses configurations that do
not answer. Tests therefore default the probe to success and cover the
failure and correction paths explicitly.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.models.endpoint_config import ProbeResult
from packages.models.settings import AppSettingsModel
from packages.research.models import ResearchTaskModel
from tests.factories import make_research_contract

SETTINGS_PATH = "/api/settings/model"
TEST_PATH = "/api/settings/model/test"


@pytest.fixture(autouse=True)
def _probe_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The save gate's happy shape: the connectivity probe passes.

    ``PUT /model`` now probes the resolved configuration against the real
    endpoint and refuses to save when the connection fails. The test
    environment cannot reach the example hosts the tests use, so the probe is
    stubbed to succeed here; the failure path is covered by the dedicated
    ``test_*_probe_fails`` test, which replaces this stub.
    """

    async def _ok(
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        client: object | None = None,
    ) -> ProbeResult:
        return ProbeResult(True, "连接成功（1 ms）", 1)

    monkeypatch.setattr("apps.api.routers.settings.probe_endpoint", _ok)


async def test_settings_round_trip_never_echoes_the_key(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    initial = await api_client.get(SETTINGS_PATH)
    assert initial.status_code == 200
    body = initial.json()
    assert body["base_url"] is None
    assert body["has_api_key"] is False
    # 回显红线：key 这个字段本身都不出现在响应里。
    assert "api_key" not in body
    assert "sk-" not in initial.text

    save = await api_client.put(
        SETTINGS_PATH,
        json={
            "base_url": "https://api.example.com",
            "api_key": "sk-secret-123",
            "model_name": "gpt-test",
        },
    )
    assert save.status_code == 200
    body = save.json()
    assert body["base_url"] == "https://api.example.com"
    assert body["model_name"] == "gpt-test"
    assert body["has_api_key"] is True
    assert "api_key" not in body
    assert "sk-secret" not in save.text

    again = (await api_client.get(SETTINGS_PATH)).json()
    assert again["has_api_key"] is True
    assert "api_key" not in again

    # The key is stored server-side (matching the per-task model_config
    # precedent) -- the store is the server, not the browser.
    async with app_sessions() as session:
        row = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(account["id"]),
                AppSettingsModel.id == 1,
            )
        )
    assert row is not None
    assert row.model_api_key == "sk-secret-123"


async def test_blank_api_key_keeps_the_stored_key(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    await api_client.put(
        SETTINGS_PATH, json={"base_url": "https://a.example", "api_key": "sk-keep"}
    )
    update = await api_client.put(
        SETTINGS_PATH, json={"base_url": "https://b.example", "api_key": ""}
    )
    assert update.status_code == 200
    assert update.json()["has_api_key"] is True

    async with app_sessions() as session:
        row = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(account["id"]),
                AppSettingsModel.id == 1,
            )
        )
    assert row is not None
    assert row.model_api_key == "sk-keep"
    assert row.model_base_url == "https://b.example"


async def test_clear_api_key_is_deliberate_and_removes_it(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "https://a.example", "api_key": "sk-temp"},
    )
    cleared = await api_client.put(SETTINGS_PATH, json={"clear_api_key": True})
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False

    async with app_sessions() as session:
        row = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(account["id"]),
                AppSettingsModel.id == 1,
            )
        )
    assert row is not None
    assert row.model_api_key is None


async def test_create_task_applies_saved_settings_when_no_explicit_config(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await api_client.put(
        SETTINGS_PATH,
        json={
            "base_url": "https://api.example.com",
            "api_key": "sk-auto",
            "model_name": "auto-model",
        },
    )
    payload = make_research_contract().model_dump(mode="json")
    assert payload.get("task_model_config") is None

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
    assert row.model_config["base_url"] == "https://api.example.com"
    assert row.model_config["api_key"] == "sk-auto"
    assert row.model_config["model_name"] == "auto-model"


async def test_explicit_per_task_config_wins_over_saved_settings(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "https://saved.example", "api_key": "sk-saved"},
    )
    payload = make_research_contract().model_dump(mode="json")
    payload["task_model_config"] = {
        "base_url": "https://explicit.example",
        "api_key": "sk-explicit",
        "model_name": None,
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
    assert row.model_config["base_url"] == "https://explicit.example"
    assert row.model_config["api_key"] == "sk-explicit"


# ---------------------------------------------------------------------------
# 连接门控（connection gate）：测试端点 + 保存前的强制探测
# ---------------------------------------------------------------------------


async def test_test_endpoint_probes_current_values_without_saving(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /model/test 用表单当前值探测，不落任何库。"""
    probed: list[dict[str, str]] = []
    # 记录探测参数（autouse fixture 已把 probe 换成成功 stub；这里再覆盖
    # 一层以捕获参数），并确认 key 被传给探测——但绝不会出现在响应里。

    async def _recording_probe(
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        client: object | None = None,
    ) -> ProbeResult:
        probed.append(
            {"base_url": base_url, "api_key": api_key, "model_name": model_name}
        )
        return ProbeResult(True, "连接成功（1 ms）", 1)

    monkeypatch.setattr("apps.api.routers.settings.probe_endpoint", _recording_probe)

    # 先存一组已知配置，作为「测试端点不得改动」的参照。
    seeded = await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "https://known.example", "api_key": "sk-known"},
    )
    assert seeded.status_code == 200

    response = await api_client.post(
        TEST_PATH,
        json={"base_url": "https://api.example.com", "api_key": "sk-probe-me"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["message"]
    # seed PUT 也是一次探测（known.example）；最后一次探测必须是
    # 测试请求携带的值。
    assert probed[-1] == {
        "base_url": "https://api.example.com",
        "api_key": "sk-probe-me",
        "model_name": "deepseek-v4-flash",
    }
    # 未保存：已存配置保持原样（key 与地址都没有被测试请求覆盖）。
    async with app_sessions() as session:
        row = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(account["id"]),
                AppSettingsModel.id == 1,
            )
        )
    assert row is not None
    assert row.model_base_url == "https://known.example"
    assert row.model_api_key == "sk-known"


async def test_test_endpoint_reports_portal_correction(
    api_client: httpx.AsyncClient,
) -> None:
    """门户地址在探测前被纠正，并在响应里告知前端。"""
    response = await api_client.post(
        TEST_PATH,
        json={"base_url": "https://platform.deepseek.com", "api_key": "sk-x"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["corrected_base_url"] == "https://api.deepseek.com"
    assert body["correction"]
    # 回显红线：key 不出现在任何响应字段或文本里。
    assert "api_key" not in body
    assert "sk-x" not in response.text


async def test_test_endpoint_requires_a_probeable_key(
    api_client: httpx.AsyncClient,
) -> None:
    """没有 Key（表单与已存都没有）时测试端点 422，且不发起探测。"""
    # 先清掉可能残留的 key，保证「没有已存 Key」成立。
    cleared = await api_client.put(SETTINGS_PATH, json={"clear_api_key": True})
    assert cleared.status_code == 200
    response = await api_client.post(
        TEST_PATH,
        json={"base_url": "https://api.example.com", "api_key": ""},
    )
    assert response.status_code == 422
    assert "API Key" in response.json()["detail"]


async def test_put_refuses_save_when_probe_fails(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """探测失败 → 422，配置不落库：只有连得通的配置才允许保存。"""

    async def _fail(
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        client: object | None = None,
    ) -> ProbeResult:
        return ProbeResult(False, "API Key 无效或无权访问（HTTP 401）：boom")

    # 先存一组已知配置（probe 默认成功），作为「失败保存不得改动」的参照。
    seeded = await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "https://known.example", "api_key": "sk-known"},
    )
    assert seeded.status_code == 200

    monkeypatch.setattr("apps.api.routers.settings.probe_endpoint", _fail)
    response = await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "https://api.example.com", "api_key": "sk-bad"},
    )
    assert response.status_code == 422
    assert "连接测试未通过" in response.json()["detail"]
    assert "boom" in response.json()["detail"]

    async with app_sessions() as session:
        row = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(account["id"]),
                AppSettingsModel.id == 1,
            )
        )
    assert row is not None
    assert row.model_base_url == "https://known.example"
    assert row.model_api_key == "sk-known"


async def test_put_normalizes_portal_url_before_saving(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户输入门户地址也会被规范化后落库——绝不原样保存。"""
    probed: list[str] = []

    async def _recording(
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        client: object | None = None,
    ) -> ProbeResult:
        probed.append(base_url)
        return ProbeResult(True, "连接成功（1 ms）", 1)

    monkeypatch.setattr("apps.api.routers.settings.probe_endpoint", _recording)
    response = await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "platform.deepseek.com", "api_key": "sk-x"},
    )
    assert response.status_code == 200, response.text
    # 探测的就是纠正后的地址。
    assert probed == ["https://api.deepseek.com"]
    # 落库的也是纠正后的地址。
    async with app_sessions() as session:
        row = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(account["id"]),
                AppSettingsModel.id == 1,
            )
        )
    assert row is not None
    assert row.model_base_url == "https://api.deepseek.com"


async def test_put_requires_an_api_key(
    api_client: httpx.AsyncClient,
) -> None:
    """有 Base URL 但没有 Key（表单与已存都没有）→ 422，不保存。"""
    cleared = await api_client.put(SETTINGS_PATH, json={"clear_api_key": True})
    assert cleared.status_code == 200
    response = await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "https://api.example.com", "api_key": ""},
    )
    assert response.status_code == 422
    assert "API Key" in response.json()["detail"]


async def test_put_refuses_a_key_without_a_base_url(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """Round-6 fix: a key without a URL is a configuration no task would
    ever inherit -- task creation copies the saved settings only when both
    are present. Saving it would show "已保存 ✓" while every new task ran
    the deployment default, so the server refuses with the reason."""
    refused = await api_client.put(
        SETTINGS_PATH, json={"api_key": "sk-orphan", "model_name": "m"}
    )
    assert refused.status_code == 422
    assert "Base URL" in refused.json()["detail"]

    async with app_sessions() as session:
        row = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(account["id"]),
                AppSettingsModel.id == 1,
            )
        )
    # The half-set configuration must not be stored (row may exist from an
    # earlier test in the shared session -- the orphan key is the thing that
    # must never land).
    assert row is None or row.model_api_key != "sk-orphan"

    # The deliberate way back to the deployment default (clear the key) is
    # still allowed, and a complete save still works.
    saved = await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "https://a.example", "api_key": "sk-ok"},
    )
    assert saved.status_code == 200
    assert saved.json()["usable"] is True
    cleared = await api_client.put(SETTINGS_PATH, json={"clear_api_key": True})
    assert cleared.status_code == 200
    assert cleared.json()["usable"] is False


async def test_get_reports_usable_only_for_complete_configurations(
    api_client: httpx.AsyncClient,
) -> None:
    """``usable`` answers the round-6 question "did my settings take effect"
    up front: it is the same both-present condition task creation applies
    when inheriting."""
    initial = (await api_client.get(SETTINGS_PATH)).json()
    assert initial["usable"] is False

    saved = await api_client.put(
        SETTINGS_PATH,
        json={
            "base_url": "https://a.example",
            "api_key": "sk-ok",
        },
    )
    assert saved.json()["usable"] is True
    again = (await api_client.get(SETTINGS_PATH)).json()
    assert again["usable"] is True


async def test_clear_api_key_skips_the_probe(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清除 Key 是移除操作，不引入新端点，无需连接测试。"""
    calls = 0

    async def _counting(
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        client: object | None = None,
    ) -> ProbeResult:
        nonlocal calls
        calls += 1
        return ProbeResult(True, "连接成功（1 ms）", 1)

    monkeypatch.setattr("apps.api.routers.settings.probe_endpoint", _counting)
    saved = await api_client.put(
        SETTINGS_PATH,
        json={"base_url": "https://api.example.com", "api_key": "sk-temp"},
    )
    assert saved.status_code == 200
    assert calls == 1  # 保存本身探测了一次

    cleared = await api_client.put(SETTINGS_PATH, json={"clear_api_key": True})
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False
    assert calls == 1  # 清除没有触发探测

    async with app_sessions() as session:
        row = await session.scalar(
            select(AppSettingsModel).where(
                AppSettingsModel.user_id == UUID(account["id"]),
                AppSettingsModel.id == 1,
            )
        )
    assert row is not None
    assert row.model_api_key is None
