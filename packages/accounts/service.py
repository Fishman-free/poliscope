"""Account lifecycle: register, login, logout, session lookup.

Holds no state of its own -- every fact about an account lives in PostgreSQL
(CLAUDE.md 8), so a restarted API process forgets nothing and a logged-in
browser's token keeps working until it expires or is revoked.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.accounts.email_sender import EmailSender
from packages.accounts.repository import (
    AuthSession,
    AuthTokensRepository,
    EmailTaken,
    InvalidCredentials,
    StoredUser,
    UsernameTaken,
    UsersRepository,
)
from packages.accounts.security import generate_token, hash_password, verify_password
from packages.accounts.verification import (
    EmailVerificationService,
    InvalidEmail,
    InvalidVerificationCode,
    RequestCodeOutcome,
    VerificationPurpose,
    VerifyCodeOutcome,
    _verify_message,
    normalize_email,
)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")
MIN_PASSWORD_LENGTH = 6


class InvalidRegistration(Exception):
    """Raised when username or password fails the format rules (map to 422)."""


class AccountNotFound(Exception):
    """Raised when an account expected to exist does not (map to 404)."""


class AuthService:
    def __init__(
        self, session: AsyncSession, *, sender: EmailSender | None = None
    ) -> None:
        self._session = session
        self._users = UsersRepository(session)
        self._tokens = AuthTokensRepository(session)
        self._verifications = EmailVerificationService(session, sender)

    async def request_verification(
        self, username: str, password: str, email: str
    ) -> RequestCodeOutcome:
        """Phase 1 of registration: validate, then email a 6-digit code.

        Deliberately does NOT reveal whether the email already owns an
        account (anti-enumeration, CLAUDE.md 2.8): the unique check is only
        performed at confirm time, when the caller already holds a valid
        code proving inbox ownership. No user is created here.
        """
        name = username.strip()
        if not USERNAME_PATTERN.fullmatch(name):
            raise InvalidRegistration(
                "username must be 2-64 characters: letters, digits, _ . -"
            )
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidRegistration(
                f"password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        normalized = normalize_email(email)  # raises InvalidEmail
        if await self._users.get_by_username(name) is not None:
            raise UsernameTaken(name)
        return await self._verifications.request_code(
            normalized, VerificationPurpose.REGISTER
        )

    async def register(
        self, username: str, password: str, email: str, code: str
    ) -> AuthSession:
        """Phase 2 of registration: verify the emailed code, then create the
        account with the email bound and issue a bearer token (auto-login)."""
        name = username.strip()
        if not USERNAME_PATTERN.fullmatch(name):
            raise InvalidRegistration(
                "username must be 2-64 characters: letters, digits, _ . -"
            )
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidRegistration(
                f"password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        normalized = normalize_email(email)  # raises InvalidEmail
        # Fast-fail uniqueness now that the caller holds a valid code (no
        # enumeration channel); create_user's unique constraint is the
        # backstop for a concurrent race.
        if await self._users.get_by_username(name) is not None:
            raise UsernameTaken(name)
        if await self._users.get_by_email(normalized) is not None:
            raise EmailTaken(normalized)
        outcome = await self._verifications.confirm(
            normalized, code, VerificationPurpose.REGISTER
        )
        if outcome is not VerifyCodeOutcome.OK:
            # Commit the wrong-attempt counter (and any code rotation) before
            # rejecting, so a caller hammering codes cannot have the counter
            # rolled back by the same rejection it caused (CLAUDE.md 2.8
            # anti-abuse). Account creation never happened, so there is
            # nothing else in this transaction to preserve.
            await self._session.commit()
            raise InvalidVerificationCode(_verify_message(outcome))
        user = await self._users.create_user(
            name, hash_password(password), email=normalized
        )
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

    async def change_username(
        self, user_id: UUID, new_username: str, old_password: str
    ) -> StoredUser:
        """Rename the account. Requires the current password; the new name
        must be unique. Renaming is idempotent when the name is unchanged."""
        user = await self._require_user(user_id)
        if not verify_password(old_password, user.password_hash):
            raise InvalidCredentials("invalid password")
        name = new_username.strip()
        if name == user.username:
            return user  # no-op
        if not USERNAME_PATTERN.fullmatch(name):
            raise InvalidRegistration(
                "username must be 2-64 characters: letters, digits, _ . -"
            )
        if await self._users.get_by_username(name) is not None:
            raise UsernameTaken(name)
        return await self._users.update_username(user_id, name)

    async def change_password(
        self,
        user_id: UUID,
        old_password: str,
        new_password: str,
        keep_token: str | None = None,
    ) -> None:
        """Replace the password after verifying the old one, and revoke every
        other session so old tokens cannot persist on other devices."""
        user = await self._require_user(user_id)
        if not verify_password(old_password, user.password_hash):
            raise InvalidCredentials("invalid password")
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise InvalidRegistration(
                f"password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        if verify_password(new_password, user.password_hash):
            raise InvalidRegistration("新密码不能与原密码相同")
        await self._users.update_password(user_id, hash_password(new_password))
        await self._tokens.revoke_all_except(user_id, keep_token)

    async def set_avatar_key(self, user_id: UUID, avatar_key: str | None) -> None:
        await self._users.update_avatar_key(user_id, avatar_key)

    async def request_password_reset(self, email: str) -> RequestCodeOutcome | None:
        """Send a password-reset code. Returns None when the email is unknown
        -- the caller still answers 202 so addresses cannot be enumerated."""
        normalized = normalize_email(email)  # raises InvalidEmail
        if await self._users.get_by_email(normalized) is None:
            return None
        return await self._verifications.request_code(
            normalized, VerificationPurpose.RESET
        )

    async def reset_password(self, email: str, code: str, new_password: str) -> None:
        """Verify a reset code, set the new password, and revoke every session
        so the old password stops working everywhere immediately."""
        normalized = normalize_email(email)
        user = await self._users.get_by_email(normalized)
        if user is None:
            raise AccountNotFound(email)
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise InvalidRegistration(
                f"password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        outcome = await self._verifications.confirm(
            normalized, code, VerificationPurpose.RESET
        )
        if outcome is not VerifyCodeOutcome.OK:
            await self._session.commit()  # persist the wrong-attempt counter
            raise InvalidVerificationCode(_verify_message(outcome))
        await self._users.update_password(user.id, hash_password(new_password))
        await self._tokens.revoke_all_for_user(user.id)

    async def verify_credentials(self, user_id: UUID, password: str) -> bool:
        """Check a password without side effects (DELETE /api/account gate)."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            return False
        return verify_password(password, user.password_hash)

    async def _require_user(self, user_id: UUID) -> StoredUser:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise AccountNotFound(user_id)
        return user


__all__ = [
    "AccountNotFound",
    "AuthService",
    "EmailTaken",
    "InvalidCredentials",
    "InvalidEmail",
    "InvalidRegistration",
    "InvalidVerificationCode",
    "RequestCodeOutcome",
    "UsernameTaken",
]
