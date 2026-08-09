"""Persistent model-gateway settings for the whole deployment.

The web workbench keeps the researcher's model endpoint (base URL, API key,
model name) as a permanent setting rather than a per-task form field. This is
a single-row table (id = 1 or nothing). The API key lives here exactly as the
per-task ``task_model_config`` already lives on research_tasks -- stored
server-side, never echoed back by any endpoint (CLAUDE.md 16) -- and
``apps/api/routers/tasks.py`` applies it to new tasks that do not carry an
explicit per-task config, so one saved setting reaches every client (web,
CLI) without each client re-sending the key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from packages.kernel.database import Base
from packages.models.free_trial import FREE_TRIAL_LIMIT

SETTINGS_ROW_ID = 1


class AppSettingsModel(Base):
    __tablename__ = "app_settings"

    # One settings row per account: the account is the primary key (every
    # account writes id = 1, and the CHECK documents that intent).
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_app_settings_single_row"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        primary_key=True,
    )
    id: Mapped[int] = mapped_column(Integer, nullable=False, default=SETTINGS_ROW_ID)
    model_base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    model_api_key: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Free-trial marker (migration 0021): True exactly while the saved
    # endpoint IS the deployment's free-trial vendor. A manual save clears
    # it; the quota itself is ``free_trial_used`` below.
    is_free_trial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # How many free-trial slots this account has consumed (migration 0021),
    # incremented atomically by confirm-claims (never read-modify-write).
    free_trial_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


@dataclass(frozen=True, slots=True)
class StoredModelSettings:
    model_base_url: str | None
    model_api_key: str | None
    model_name: str | None
    # Free-trial marker and quota (migration 0021); defaults keep callers
    # that only care about the endpoint untouched.
    is_free_trial: bool = False
    free_trial_used: int = 0

    @property
    def has_api_key(self) -> bool:
        return self.model_api_key is not None

    @property
    def free_trial_remaining(self) -> int:
        return max(FREE_TRIAL_LIMIT - self.free_trial_used, 0)


class ModelSettingsRepository:
    """Per-account single-row settings store; a missing row reads as empty."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> StoredModelSettings:
        row = await self._session.get(AppSettingsModel, user_id)
        if row is None:
            return StoredModelSettings(None, None, None)
        return StoredModelSettings(
            row.model_base_url,
            row.model_api_key,
            row.model_name,
            row.is_free_trial,
            row.free_trial_used,
        )

    async def save(
        self,
        user_id: UUID,
        *,
        base_url: str | None,
        api_key: str | None,
        model_name: str | None,
        is_free_trial: bool = False,
    ) -> StoredModelSettings:
        """Upsert the account's single settings row and return what is now
        stored. Callers resolve the "keep vs. clear vs. replace" semantics of
        the API key before calling; this method only writes resolved values.
        """
        row = await self._session.get(AppSettingsModel, user_id)
        if row is None:
            row = AppSettingsModel(user_id=user_id)
            self._session.add(row)
        row.model_base_url = base_url
        row.model_api_key = api_key
        row.model_name = model_name
        row.is_free_trial = is_free_trial
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return StoredModelSettings(
            row.model_base_url,
            row.model_api_key,
            row.model_name,
            row.is_free_trial,
            row.free_trial_used,
        )

    async def consume_free_trial(self, user_id: UUID, limit: int) -> bool:
        """Atomically consume one free-trial slot, returning whether one was
        available.

        One UPDATE with the guard in the WHERE clause -- never a
        read-then-write -- so two concurrent confirmations cannot both take
        the last slot. The quota survives only while the row is still marked
        ``is_free_trial``: a user who saved their own endpoint mid-way has
        left the trial, and their old trial-flagged tasks no longer draw on
        the quota.
        """
        result = await self._session.execute(
            update(AppSettingsModel)
            .where(
                AppSettingsModel.user_id == user_id,
                AppSettingsModel.is_free_trial.is_(True),
                AppSettingsModel.free_trial_used < limit,
            )
            .values(free_trial_used=AppSettingsModel.free_trial_used + 1)
            .returning(AppSettingsModel.free_trial_used)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None


__all__ = [
    "SETTINGS_ROW_ID",
    "AppSettingsModel",
    "ModelSettingsRepository",
    "StoredModelSettings",
]
