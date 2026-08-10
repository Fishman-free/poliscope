"""Persistence for users and auth tokens.

Two small repositories sharing the same session, because both sit behind the
same ``AuthService`` and neither owns more than one table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.accounts.models import AuthTokenModel, UserModel
from packages.accounts.security import hash_token

TOKEN_TTL = timedelta(days=30)


class UsernameTaken(Exception):
    """Raised when a username already exists (map to 409, not 500)."""


class EmailTaken(Exception):
    """Raised when an email already owns an account (map to 409, not 500)."""


class InvalidCredentials(Exception):
    """Raised on unknown username or wrong password (map to 401)."""


def _violates_email_constraint(error: IntegrityError) -> bool:
    """Whether an IntegrityError came from the users_email_key constraint.

    AsyncPG surfaces the violated constraint through ``error.orig.diag``
    (psycopg/asyncpg-style); a stale or partial message string is the
    fallback so the check stays robust across drivers.
    """
    diag = getattr(getattr(error, "orig", None), "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name:
        return bool(name == "users_email_key")
    text = str(error.orig)
    return "users_email" in text or "users_email_key" in text


@dataclass(frozen=True, slots=True)
class StoredUser:
    id: UUID
    username: str
    password_hash: str
    created_at: datetime
    email: str | None = None
    avatar_key: str | None = None


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

    async def create_user(
        self, username: str, password_hash: str, email: str | None = None
    ) -> StoredUser:
        row = UserModel(
            id=uuid4(), username=username, password_hash=password_hash, email=email
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as error:
            # The unique constraint tells us which duplicate it was; a
            # simultaneous race on both is indistinguishable, so username
            # wins the tie (it is the login key).
            if _violates_email_constraint(error):
                raise EmailTaken(email) from error
            raise UsernameTaken(username) from error
        return StoredUser(
            id=row.id, username=row.username,
            password_hash=row.password_hash, created_at=row.created_at,
            email=row.email,
            avatar_key=row.avatar_key,
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
            email=row.email,
            avatar_key=row.avatar_key,
        )

    async def get_by_email(self, email: str) -> StoredUser | None:
        row = await self._session.scalar(
            select(UserModel).where(UserModel.email == email)
        )
        if row is None:
            return None
        return StoredUser(
            id=row.id, username=row.username,
            password_hash=row.password_hash, created_at=row.created_at,
            email=row.email,
            avatar_key=row.avatar_key,
        )

    async def get_by_id(self, user_id: UUID) -> StoredUser | None:
        row = await self._session.get(UserModel, user_id)
        if row is None:
            return None
        return StoredUser(
            id=row.id, username=row.username,
            password_hash=row.password_hash, created_at=row.created_at,
            email=row.email,
            avatar_key=row.avatar_key,
        )

    async def update_username(self, user_id: UUID, new_username: str) -> StoredUser:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(username=new_username)
        )
        await self._session.flush()
        row = await self._session.get(UserModel, user_id)
        assert row is not None
        return StoredUser(
            id=row.id, username=row.username,
            password_hash=row.password_hash, created_at=row.created_at,
            email=row.email,
            avatar_key=row.avatar_key,
        )

    async def update_password(self, user_id: UUID, new_hash: str) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(password_hash=new_hash)
        )
        await self._session.flush()

    async def update_avatar_key(
        self, user_id: UUID, avatar_key: str | None
    ) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(avatar_key=avatar_key)
        )
        await self._session.flush()

    async def delete(self, user_id: UUID) -> None:
        row = await self._session.get(UserModel, user_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()


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
            email=user.email,
            avatar_key=user.avatar_key,
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

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        """Revoke every session of a user (password reset / account delete)."""
        await self._session.execute(
            delete(AuthTokenModel).where(AuthTokenModel.user_id == user_id)
        )
        await self._session.flush()

    async def revoke_all_except(
        self, user_id: UUID, keep_token: str | None
    ) -> None:
        """Revoke every session but the presented one (change password)."""
        if keep_token is None:
            await self.revoke_all_for_user(user_id)
            return
        await self._session.execute(
            delete(AuthTokenModel).where(
                AuthTokenModel.user_id == user_id,
                AuthTokenModel.token_hash != hash_token(keep_token),
            )
        )
        await self._session.flush()


__all__ = [
    "AuthSession",
    "AuthTokensRepository",
    "EmailTaken",
    "InvalidCredentials",
    "StoredUser",
    "TOKEN_TTL",
    "UsernameTaken",
    "UsersRepository",
]
