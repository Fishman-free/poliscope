"""Skills API: download, list, toggle, forget -- per account.

The GitHub fetch itself is unit-tested against a mock transport; here the
fetch is stubbed so the endpoint's own story (download -> persist -> list ->
toggle -> delete, 409 on duplicate, 404 on foreign ids) runs against the
real database and HTTP stack.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.skills.models import SkillModel

SKILLS_PATH = "/api/skills"
SKILL_MARKDOWN = """---
name: testing-skill
---
# Testing Skill
Guidance for the council.
"""


async def _register(client: httpx.AsyncClient, username: str) -> dict[str, Any]:
    from tests.conftest import register_user

    return await register_user(client, username)


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_skill_crud_lifecycle(
    api_client: httpx.AsyncClient,
    monkeypatch: Any,
) -> None:
    session = await _register(api_client, "skills-owner")
    headers = _bearer(session["token"])

    async def fake_fetch(client: Any, url: str) -> tuple[tuple[str, str], ...]:
        return (("testing-skill", SKILL_MARKDOWN),)

    monkeypatch.setattr(
        "packages.skills.service.fetch_skills_from_repo", fake_fetch
    )

    added = await api_client.post(
        SKILLS_PATH,
        json={"github_url": "https://github.com/owner/testing-skill"},
        headers=headers,
    )
    assert added.status_code == 201, added.text
    body = added.json()
    assert isinstance(body, list) and len(body) == 1
    assert body[0]["name"] == "testing-skill"
    assert body[0]["enabled"] is True
    skill_id = body[0]["id"]

    listing = await api_client.get(SKILLS_PATH, headers=headers)
    assert listing.status_code == 200
    assert [skill["id"] for skill in listing.json()] == [skill_id]

    # Toggle off via PUT (what the web client sends -- the 405 the round-5
    # bug report surfaced), then on via PATCH.
    off = await api_client.put(
        f"{SKILLS_PATH}/{skill_id}",
        json={"enabled": False},
        headers=headers,
    )
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    on = await api_client.patch(
        f"{SKILLS_PATH}/{skill_id}",
        json={"enabled": True},
        headers=headers,
    )
    assert on.json()["enabled"] is True

    deleted = await api_client.delete(f"{SKILLS_PATH}/{skill_id}", headers=headers)
    assert deleted.status_code == 204
    empty = await api_client.get(SKILLS_PATH, headers=headers)
    assert empty.json() == []


async def test_duplicate_skill_url_is_409(
    api_client: httpx.AsyncClient,
    monkeypatch: Any,
) -> None:
    session = await _register(api_client, "duplicate-skill-owner")
    headers = _bearer(session["token"])

    async def fake_fetch(client: Any, url: str) -> tuple[tuple[str, str], ...]:
        return (("dup-skill", SKILL_MARKDOWN),)

    monkeypatch.setattr(
        "packages.skills.service.fetch_skills_from_repo", fake_fetch
    )
    url = "https://github.com/owner/dup-skill"

    first = await api_client.post(
        SKILLS_PATH, json={"github_url": url}, headers=headers
    )
    assert first.status_code == 201
    second = await api_client.post(
        SKILLS_PATH, json={"github_url": url}, headers=headers
    )
    assert second.status_code == 409


async def test_skill_fetch_failure_is_honest_422(
    api_client: httpx.AsyncClient,
    monkeypatch: Any,
) -> None:
    session = await _register(api_client, "failed-skill-owner")
    headers = _bearer(session["token"])

    async def fake_fetch(client: Any, url: str) -> tuple[tuple[str, str], ...]:
        from packages.skills.github import SkillFetchError

        raise SkillFetchError("no SKILL.md found in the repository")

    monkeypatch.setattr(
        "packages.skills.service.fetch_skills_from_repo", fake_fetch
    )
    response = await api_client.post(
        SKILLS_PATH,
        json={"github_url": "https://github.com/owner/empty-repo"},
        headers=headers,
    )
    assert response.status_code == 422
    assert "no SKILL.md" in response.text


async def test_foreign_skill_id_is_404(
    api_client: httpx.AsyncClient,
) -> None:
    session = await _register(api_client, "foreign-skill-owner")
    headers = _bearer(session["token"])
    response = await api_client.patch(
        f"{SKILLS_PATH}/{uuid4()}",
        json={"enabled": False},
        headers=headers,
    )
    assert response.status_code == 404


async def test_task_creation_stores_skill_ids(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    session = await _register(api_client, "task-skill-owner")
    headers = _bearer(session["token"])

    async def fake_fetch(client: Any, url: str) -> tuple[tuple[str, str], ...]:
        return (("task-skill", SKILL_MARKDOWN),)

    monkeypatch.setattr(
        "packages.skills.service.fetch_skills_from_repo", fake_fetch
    )
    added = await api_client.post(
        SKILLS_PATH,
        json={"github_url": "https://github.com/owner/task-skill"},
        headers=headers,
    )
    skill_id = added.json()[0]["id"]

    from tests.factories import make_research_contract

    payload = make_research_contract().model_dump(mode="json")
    payload["skill_ids"] = [skill_id]
    created = await api_client.post("/api/tasks", json=payload, headers=headers)
    assert created.status_code == 201, created.text

    async with app_sessions() as db_session:
        row = (
            await db_session.execute(
                select(SkillModel).where(SkillModel.id == skill_id)
            )
        ).scalar_one()
        assert row.name == "task-skill"
        assert row.user_id is not None
        assert str(row.user_id) == session["id"]


async def test_task_with_foreign_skill_id_is_404(
    api_client: httpx.AsyncClient,
) -> None:
    session = await _register(api_client, "foreign-task-skill")
    headers = _bearer(session["token"])

    from tests.factories import make_research_contract

    payload = make_research_contract().model_dump(mode="json")
    payload["skill_ids"] = [str(uuid4())]
    response = await api_client.post("/api/tasks", json=payload, headers=headers)
    assert response.status_code == 404
    assert "skill" in response.text
