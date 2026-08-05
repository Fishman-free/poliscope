"""Persistence for researcher skills.

One table, one owner dimension: every query is scoped to the account.
Deletion is plain (tasks store skill ids, not foreign keys), so nothing here
ever blocks on evidence -- removing a skill severs the link for future
tasks, and past tasks keep what they were given.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.skills.models import SkillModel


class SkillNotFound(Exception):
    """Raised when a skill id does not exist for this account."""


class SkillAlreadyAdded(Exception):
    """Raised when the same GitHub URL is added twice by one account."""


@dataclass(frozen=True, slots=True)
class StoredSkill:
    id: UUID
    user_id: UUID
    name: str
    github_url: str
    downloaded_path: str
    enabled: bool
    downloaded_at: datetime
    created_at: datetime


class SkillsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: UUID) -> tuple[StoredSkill, ...]:
        rows = (
            await self._session.scalars(
                select(SkillModel)
                .where(SkillModel.user_id == user_id)
                .order_by(SkillModel.created_at.desc(), SkillModel.id)
            )
        ).all()
        return tuple(to_stored(row) for row in rows)

    async def add(
        self,
        user_id: UUID,
        *,
        name: str,
        github_url: str,
        downloaded_path: str,
    ) -> StoredSkill:
        row = SkillModel(
            id=uuid4(),
            user_id=user_id,
            name=name,
            github_url=github_url,
            downloaded_path=downloaded_path,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise SkillAlreadyAdded(github_url) from error
        return to_stored(row)

    async def get_for_user(
        self, user_id: UUID, skill_id: UUID
    ) -> StoredSkill | None:
        row = await self._session.scalar(
            select(SkillModel).where(
                SkillModel.id == skill_id,
                SkillModel.user_id == user_id,
            )
        )
        return to_stored(row) if row is not None else None

    async def set_enabled(
        self, user_id: UUID, skill_id: UUID, enabled: bool
    ) -> StoredSkill:
        row = await self._session.scalar(
            select(SkillModel).where(
                SkillModel.id == skill_id,
                SkillModel.user_id == user_id,
            )
        )
        if row is None:
            raise SkillNotFound(skill_id)
        row.enabled = enabled
        await self._session.flush()
        return to_stored(row)

    async def delete(self, user_id: UUID, skill_id: UUID) -> None:
        row = await self._session.scalar(
            select(SkillModel).where(
                SkillModel.id == skill_id,
                SkillModel.user_id == user_id,
            )
        )
        if row is None:
            raise SkillNotFound(skill_id)
        await self._session.delete(row)
        await self._session.flush()


def to_stored(row: SkillModel) -> StoredSkill:
    """The row-shaped view of a skill, shared by the repository and the
    worker's injection path (which reads skills directly for a task)."""
    return StoredSkill(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        github_url=row.github_url,
        downloaded_path=row.downloaded_path,
        enabled=row.enabled,
        downloaded_at=row.downloaded_at,
        created_at=row.created_at,
    )


__all__ = [
    "SkillAlreadyAdded",
    "SkillNotFound",
    "SkillsRepository",
    "StoredSkill",
    "to_stored",
]
