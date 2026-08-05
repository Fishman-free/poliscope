"""Permanent model settings: save, never echo the key, auto-apply to tasks.

The right-side settings panel persists the researcher's model endpoint once;
`POST /api/tasks` applies it to tasks that carry no explicit per-task config.
The API key is the load-bearing privacy constraint here: it is stored like
the per-task model_config already is, but no response may ever contain it
(CLAUDE.md 16) -- clients learn only `has_api_key`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.models.settings import AppSettingsModel
from packages.research.models import ResearchTaskModel
from tests.factories import make_research_contract

SETTINGS_PATH = "/api/settings/model"


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
    await api_client.put(SETTINGS_PATH, json={"api_key": "sk-temp"})
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
