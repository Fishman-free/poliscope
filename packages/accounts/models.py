"""ORM models for user accounts and their auth tokens.

The web workbench's account system replaces the Caddy shared-password gate:
``users`` is who owns what (every isolation query filters on
``user_id``), ``auth_tokens`` is the remember-me session store -- the bearer
token's sha256 lives here, never the token itself (see security.py).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from packages.kernel.database import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Nullable so accounts created before email verification (migration 0022)
    # stay at NULL; the unique constraint means one email can own at most one
    # account, while multiple NULLs remain distinct for legacy rows.
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, unique=True)
    # Object-store key of the account avatar, or NULL when unset. Only the
    # key lives here -- the image bytes live in the private object store
    # (CLAUDE.md 16: uploaded material never leaks through logs or exports).
    avatar_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EmailVerificationModel(Base):
    """One row per (email, purpose) holding the current pending code.

    ``purpose`` distinguishes registration from password reset so both can
    pend for the same address without colliding. The code is stored hashed
    (sha256 via security.hash_token), never in plaintext; ``expires_at``
    bounds it to 5 minutes; ``verified_at`` marks a code as consumed so it
    cannot be replayed. ``attempts``, ``last_sent_at`` and the
    ``sent_day``/``sent_today`` pair throttle resends and wrong guesses --
    all incremented by atomic UPDATE (packages/accounts/repository.py).
    """

    __tablename__ = "email_verifications"
    __table_args__ = (
        UniqueConstraint(
            "email", "purpose", name="email_verifications_email_purpose_key"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    purpose: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="register"
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sent_day: Mapped[date] = mapped_column(Date, nullable=False)
    sent_today: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthTokenModel(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
