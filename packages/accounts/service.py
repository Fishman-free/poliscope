"""Account lifecycle: register, login, logout, session lookup.

Holds no state of its own -- every fact about an account lives in PostgreSQL
(CLAUDE.md 8), so a restarted API process forgets nothing and a logged-in
browser's token keeps working until it expires or is revoked.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.accounts.repository import (
    AuthSession,
    AuthTokensRepository,
    InvalidCredentials,
    StoredUser,
    UsernameTaken,
    UsersRepository,
)
from packages.accounts.security import generate_token, hash_password, verify_password

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")
MIN_PASSWORD_LENGTH = 6


class InvalidRegistration(Exception):
    """Raised when username or password fails the format rules (map to 422)."""


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UsersRepository(session)
        self._tokens = AuthTokensRepository(session)

    async def register(self, username: str, password: str) -> AuthSession:
        name = username.strip()
        if not USERNAME_PATTERN.fullmatch(name):
            raise InvalidRegistration(
                "username must be 2-64 characters: letters, digits, _ . -"
            )
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidRegistration(
                f"password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        user = await self._users.create_user(name, hash_password(password))
        token = generate_token()
        await self._tokens.issue(user.id, token)
        return AuthSession(user=user, token=token)

    async def login(self, username: str, password: str) -> AuthSession:
        user = await self._users.get_by_username(username.strip())
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentials("invalid username or password")
        token = generate_token()
        await self._tokens.issue(user.id, token)
        return AuthSession(user=user, token=token)

    async def logout(self, token: str) -> None:
        await self._tokens.revoke(token)

    async def user_for_token(self, token: str) -> StoredUser | None:
        return await self._tokens.lookup(token)

    async def get_user(self, user_id: UUID) -> StoredUser | None:
        return await self._users.get_by_id(user_id)


__all__ = [
    "AuthService",
    "InvalidCredentials",
    "InvalidRegistration",
    "UsernameTaken",
]
