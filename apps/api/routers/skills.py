"""Researcher skills: download from GitHub, list, enable/disable, remove.

Every endpoint is scoped to the calling account. Adding a skill downloads
its SKILL.md into the server's per-account directory and records it; the
council injects enabled skills into a task's prompts when the task was
created with the skill's id.

Smart install (round-4): when the repo has no SKILL.md at any conventional
path, the account's configured model is asked to pick an existing markdown
file or synthesise a skill summary from the repo (packages/skills/llm_assist).
Without a model configured the honest error explains exactly that.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.schemas import SkillAddRequest, SkillToggleRequest
from packages.models.settings import ModelSettingsRepository
from packages.skills.github import SkillFetchError
from packages.skills.llm_assist import SkillLLMError, analyze_repo_for_skill
from packages.skills.repository import (
    SkillAlreadyAdded,
    SkillNotFound,
    SkillsRepository,
    StoredSkill,
)
from packages.skills.service import SkillsService

router = APIRouter()


def _skill_dto(skill: StoredSkill) -> dict[str, Any]:
    return {
        "id": str(skill.id),
        "name": skill.name,
        "github_url": skill.github_url,
        "enabled": skill.enabled,
        "downloaded_at": skill.downloaded_at.isoformat(),
    }


async def _llm_assistant_for(
    session: AsyncSession,
    user_id: UUID,
) -> (
    Callable[..., Awaitable[object]] | None
):
    """Wire the account's model settings into the skill-assist analysis.

    ``None`` when the account has no model configured -- the honest error
    message then tells the researcher to configure one before a no-SKILL.md
    repo can be installed.
    """
    saved = await ModelSettingsRepository(session).get(user_id)
    if not (saved.model_base_url and saved.has_api_key):
        return None
    model_config: dict[str, object] = {
        "base_url": saved.model_base_url,
        "api_key": saved.model_api_key,
        "model_name": saved.model_name,
    }
    return partial(analyze_repo_for_skill, model_config=model_config)


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def add_skill(
    request: SkillAddRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> list[dict[str, Any]]:
    """Download a GitHub skill (or every skill of a collection repository)
    and remember them for the caller's account. A collection installs all of
    its SKILL.md files as separate skills (round-4 request); skills already
    present are skipped and the response lists what is now available."""
    service = SkillsService(session)
    llm_assistant = await _llm_assistant_for(session, current_user.id)
    try:
        skills = await service.add_from_url(
            current_user.id, request.github_url, llm_assistant=llm_assistant
        )
    except (SkillFetchError, SkillLLMError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=getattr(error, "reason", str(error)),
        ) from error
    except SkillAlreadyAdded as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this skill is already added",
        ) from error
    await session.commit()
    return [_skill_dto(skill) for skill in skills]


@router.get("")
@router.get("/", include_in_schema=False)
async def list_skills(
    session: SessionDep,
    current_user: CurrentUserDep,
) -> list[dict[str, Any]]:
    skills = await SkillsRepository(session).list_for_user(current_user.id)
    return [_skill_dto(skill) for skill in skills]


@router.patch("/{skill_id}")
async def toggle_skill(
    skill_id: UUID,
    request: SkillToggleRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Check/uncheck a skill: enabled skills join new tasks by default and
    are injected into their council prompts."""
    repository = SkillsRepository(session)
    try:
        skill = await repository.set_enabled(
            current_user.id, skill_id, request.enabled
        )
    except SkillNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown skill",
        ) from error
    await session.commit()
    return _skill_dto(skill)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> None:
    """Forget a skill. Tasks that already ran keep their injected copy; the
    link is severed for future tasks."""
    repository = SkillsRepository(session)
    try:
        await repository.delete(current_user.id, skill_id)
    except SkillNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown skill",
        ) from error
    await session.commit()
