"""ORM model for researcher skills.

A skill is a GitHub repository the account added; the server downloaded its
SKILL.md into a per-account directory (``downloaded_path``) and the council
injects its text into prompts when the skill is enabled (``enabled``) and
the task lists its id.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from packages.kernel.database import Base


class SkillModel(Base):
    __tablename__ = "skills"

    # One skill per account per (URL, name) -- a *collection* repository
    # carries several SKILL.md files, each installed as its own row under the
    # same URL (migration 0018); adding the same repository+name twice would
    # just duplicate the download.
    __table_args__ = (
        UniqueConstraint(
            "user_id", "github_url", "name", name="uq_skills_user_name"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    github_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    downloaded_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=func.true(), nullable=False, default=True
    )
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
