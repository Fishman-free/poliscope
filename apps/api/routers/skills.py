"""Researcher skills: download from GitHub, list, enable/disable, remove.

Every endpoint is scoped to the calling account. Adding a skill downloads
its SKILL.md into the server's per-account directory and records it; the
council injects enabled skills into a task's prompts when the task was
created with the skill's id.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.schemas import SkillAddRequest, SkillToggleRequest
from packages.skills.github import SkillFetchError
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


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def add_skill(
    request: SkillAddRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Download a GitHub skill and remember it for the caller's account."""
    service = SkillsService(session)
    try:
        skill = await service.add_from_url(current_user.id, request.github_url)
    except SkillFetchError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error.reason,
        ) from error
    except SkillAlreadyAdded as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this skill is already added",
        ) from error
    await session.commit()
    return _skill_dto(skill)


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
