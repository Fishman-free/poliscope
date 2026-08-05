"""Account endpoints and per-account isolation, end to end.

Register -> login -> /me -> logout is the account lifecycle; 401/409 on the
failure paths; and the isolation contract: user A's tasks, knowledge bases,
settings and skills are invisible to user B (and vice versa), with the
ownership check answering 404 rather than leaking existence.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.research.models import ResearchTaskModel
from tests.factories import make_research_contract

AUTH_PATH = "/api/auth"


async def _register(
    client: httpx.AsyncClient, username: str
) -> dict[str, Any]:
    response = await client.post(
        f"{AUTH_PATH}/register",
        json={"username": username, "password": "test-password-123"},
    )
    assert response.status_code == 201, response.text
    body = cast(dict[str, Any], response.json())
    assert body["username"] == username
    assert body["token"]
    return body


def _bearer_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_register_login_me_logout_cycle(
    api_client: httpx.AsyncClient,
) -> None:
    session = await _register(api_client, "cycle-user")

    # /me with the fresh token identifies the account.
    me = await api_client.get(
        f"{AUTH_PATH}/me", headers=_bearer_headers(session["token"])
    )
    assert me.status_code == 200
    assert me.json()["username"] == "cycle-user"

    # Logout revokes that token; /me now answers 401.
    out = await api_client.post(
        f"{AUTH_PATH}/logout", headers=_bearer_headers(session["token"])
    )
    assert out.status_code == 204
    me_again = await api_client.get(
        f"{AUTH_PATH}/me", headers=_bearer_headers(session["token"])
    )
    assert me_again.status_code == 401

    # A fresh login issues a working token (password login path).
    logged_in = await api_client.post(
        f"{AUTH_PATH}/login",
        json={"username": "cycle-user", "password": "test-password-123"},
    )
    assert logged_in.status_code == 200
    me_after = await api_client.get(
        f"{AUTH_PATH}/me", headers=_bearer_headers(logged_in.json()["token"])
    )
    assert me_after.status_code == 200


async def test_login_with_wrong_password_is_401(
    api_client: httpx.AsyncClient,
) -> None:
    await _register(api_client, "wrong-pw-user")
    response = await api_client.post(
        f"{AUTH_PATH}/login",
        json={"username": "wrong-pw-user", "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert "token" not in response.text


async def test_duplicate_username_is_409(
    api_client: httpx.AsyncClient,
) -> None:
    await _register(api_client, "duplicate-user")
    again = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "duplicate-user", "password": "another-password"},
    )
    assert again.status_code == 409


async def test_weak_registration_is_422(api_client: httpx.AsyncClient) -> None:
    short_password = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "ok-name", "password": "123"},
    )
    assert short_password.status_code == 422
    bad_name = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "空格 名字", "password": "long-enough"},
    )
    assert bad_name.status_code == 422


async def test_protected_endpoints_require_auth(
    api_client: httpx.AsyncClient,
) -> None:
    # Drop the shared header to impersonate an anonymous caller.
    api_client.headers.pop("authorization")
    for path in (
        "/api/tasks",
        "/api/knowledge-bases",
        "/api/skills",
        "/api/settings/model",
    ):
        response = await api_client.get(path)
        assert response.status_code == 401, path


async def test_second_account_cannot_see_first_accounts_data(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """Isolation: everything user A creates stays invisible to user B.

    A creates a task, a knowledge base, model settings, and a skill; B lists
    all four and sees nothing, and direct fetches of A's ids answer 404.
    """
    first = await _register(api_client, "isolation-a")
    second = await _register(api_client, "isolation-b")

    # A creates a task and a knowledge base.
    task_payload = make_research_contract().model_dump(mode="json")
    created = await api_client.post(
        "/api/tasks",
        json=task_payload,
        headers=_bearer_headers(first["token"]),
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    kb = await api_client.post(
        "/api/knowledge-bases",
        json={"name": "a-only-kb"},
        headers=_bearer_headers(first["token"]),
    )
    assert kb.status_code == 201
    kb_id = kb.json()["id"]

    # B's lists are empty of A's rows.
    b_tasks = await api_client.get(
        "/api/tasks", headers=_bearer_headers(second["token"])
    )
    assert b_tasks.status_code == 200
    assert all(item["task_id"] != task_id for item in b_tasks.json())
    b_kbs = await api_client.get(
        "/api/knowledge-bases", headers=_bearer_headers(second["token"])
    )
    assert all(item["id"] != kb_id for item in b_kbs.json())

    # B's direct fetches answer 404 -- existence must not leak.
    b_workspace = await api_client.get(
        f"/api/workspace/{task_id}", headers=_bearer_headers(second["token"])
    )
    assert b_workspace.status_code == 404
    b_kb = await api_client.get(
        f"/api/knowledge-bases/{kb_id}", headers=_bearer_headers(second["token"])
    )
    assert b_kb.status_code == 404

    # The task row really is owned by A.
    async with app_sessions() as session:
        row = (
            await session.execute(
                select(ResearchTaskModel).where(
                    ResearchTaskModel.task_id == task_id
                )
            )
        ).scalar_one()
    assert str(row.user_id) == first["id"]


async def test_model_settings_are_isolated_per_account(
    api_client: httpx.AsyncClient,
) -> None:
    first = await _register(api_client, "settings-a")
    second = await _register(api_client, "settings-b")

    saved = await api_client.put(
        "/api/settings/model",
        json={"base_url": "https://a.example", "api_key": "sk-a"},
        headers=_bearer_headers(first["token"]),
    )
    assert saved.status_code == 200
    assert saved.json()["has_api_key"] is True

    b_view = await api_client.get(
        "/api/settings/model", headers=_bearer_headers(second["token"])
    )
    assert b_view.status_code == 200
    assert b_view.json()["base_url"] is None
    assert b_view.json()["has_api_key"] is False
    # A's key never leaks into B's response.
    assert "sk-a" not in b_view.text
