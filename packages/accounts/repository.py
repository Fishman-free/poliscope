"""Persistence for users and auth tokens.

Two small repositories sharing the same session, because both sit behind the
same ``AuthService`` and neither owns more than one table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.accounts.models import AuthTokenModel, UserModel
from packages.accounts.security import hash_token

TOKEN_TTL = timedelta(days=30)


class UsernameTaken(Exception):
    """Raised when a username already exists (map to 409, not 500)."""


class InvalidCredentials(Exception):
    """Raised on unknown username or wrong password (map to 401)."""


@dataclass(frozen=True, slots=True)
class StoredUser:
    id: UUID
    username: str
    password_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuthSession:
    """What the client keeps: the user plus the one-time plaintext token.

    The plaintext token exists only in this object -- it is returned to the
    caller (the HTTP response) and never persisted; ``token_hash`` is what
    the database stores.
    """

    user: StoredUser
    token: str


class UsersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_user(self, username: str, password_hash: str) -> StoredUser:
        row = UserModel(id=uuid4(), username=username, password_hash=password_hash)
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise UsernameTaken(username) from error
        return StoredUser(
            id=row.id, username=row.username,
            password_hash=row.password_hash, created_at=row.created_at,
        )

    async def get_by_username(self, username: str) -> StoredUser | None:
        row = await self._session.scalar(
            select(UserModel).where(UserModel.username == username)
        )
        if row is None:
            return None
        return StoredUser(
            id=row.id, username=row.username,
            password_hash=row.password_hash, created_at=row.created_at,
        )

    async def get_by_id(self, user_id: UUID) -> StoredUser | None:
        row = await self._session.get(UserModel, user_id)
        if row is None:
            return None
        return StoredUser(
            id=row.id, username=row.username,
            password_hash=row.password_hash, created_at=row.created_at,
        )


class AuthTokensRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(self, user_id: UUID, token: str) -> None:
        """Persist a hashed token valid for TOKEN_TTL from now."""
        self._session.add(
            AuthTokenModel(
                id=uuid4(),
                user_id=user_id,
                token_hash=hash_token(token),
                expires_at=datetime.now(UTC) + TOKEN_TTL,
            )
        )
        await self._session.flush()

    async def lookup(self, token: str) -> StoredUser | None:
        """Resolve a bearer token to its user, rejecting expired tokens."""
        row = await self._session.scalar(
            select(AuthTokenModel).where(
                AuthTokenModel.token_hash == hash_token(token)
            )
        )
        if row is None:
            return None
        if row.expires_at <= datetime.now(UTC):
            return None
        user = await self._session.get(UserModel, row.user_id)
        if user is None:
            return None
        return StoredUser(
            id=user.id, username=user.username,
            password_hash=user.password_hash, created_at=user.created_at,
        )

    async def revoke(self, token: str) -> None:
        row = await self._session.scalar(
            select(AuthTokenModel).where(
                AuthTokenModel.token_hash == hash_token(token)
            )
        )
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()


__all__ = [
    "AuthSession",
    "AuthTokensRepository",
    "InvalidCredentials",
    "StoredUser",
    "TOKEN_TTL",
    "UsernameTaken",
    "UsersRepository",
]
