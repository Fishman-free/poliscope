"""Email-verification codes for registration and password reset.

Two-phase registration (and later, password reset) prove the caller owns an
inbox before an account is created or a password replaced. The code is a
6-digit number sent by email; its sha256 is what the database stores.

Throttling and replay protection are all atomic single-row operations
(``INSERT ... ON CONFLICT ... DO UPDATE ... WHERE ... RETURNING`` for
sending, ``UPDATE ... WHERE ... RETURNING`` for consuming), never a
read-modify-write -- the same discipline as ``consume_free_trial`` in
packages/models/settings.py -- so concurrent requests cannot both take the
last quota or race the wrong-code counter.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from packages.accounts.email_sender import EmailSender, EmailServiceUnavailable
from packages.accounts.models import EmailVerificationModel
from packages.accounts.security import generate_verification_code, hash_token

VERIFICATION_CODE_TTL = timedelta(minutes=5)
RESEND_INTERVAL = timedelta(seconds=60)
MAX_VERIFY_ATTEMPTS = 5
MAX_DAILY_CODES = 5
RESEND_INTERVAL_SECONDS = 60

# A pragmatic RFC-5321-ish shape without pulling in `email-validator` (KISS,
# zero new dependencies). Local parts and domains are both ASCII; the regex
# only needs to reject obvious garbage, not be a full RFC parser.
EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
MAX_EMAIL_LENGTH = 254


class InvalidEmail(Exception):
    """Raised when an address fails normalize_email (map to 422)."""


class InvalidVerificationCode(Exception):
    """Raised when confirm fails (wrong/expired/exhausted code; map to 422)."""


class RequestCodeOutcome(StrEnum):
    SENT = "sent"
    THROTTLED = "throttled"  # 60s has not elapsed since the last send
    DAILY_LIMIT = "daily_limit"  # today's per-address budget is spent


class VerifyCodeOutcome(StrEnum):
    OK = "ok"
    WRONG_CODE = "wrong_code"
    EXPIRED = "expired"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    NO_CODE = "no_code"  # never requested
    ALREADY_USED = "already_used"  # the code was consumed once already


class VerificationPurpose(StrEnum):
    """Why a code was sent -- registration or password reset. Both can pend
    for the same email at once, so the code table is unique on (email,
    purpose) rather than email alone (migration 0023)."""

    REGISTER = "register"
    RESET = "reset"


def normalize_email(raw: str) -> str:
    """Lowercase, strip, validate. Raises InvalidEmail on bad shape."""
    email = (raw or "").strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        raise InvalidEmail("邮箱格式不正确")
    return email


def _verify_message(outcome: VerifyCodeOutcome) -> str:
    return {
        VerifyCodeOutcome.WRONG_CODE: "验证码错误",
        VerifyCodeOutcome.EXPIRED: "验证码已过期，请重新发送",
        VerifyCodeOutcome.TOO_MANY_ATTEMPTS: "验证码尝试次数过多，请重新发送",
        VerifyCodeOutcome.NO_CODE: "请先获取验证码",
        VerifyCodeOutcome.ALREADY_USED: "验证码已使用，请重新获取",
    }[outcome]


class EmailVerificationRepository:
    """Persistence for email_verifications, all writes atomic."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def request(
        self, email: str, code: str, purpose: VerificationPurpose
    ) -> RequestCodeOutcome:
        """Write a fresh code, or rotate the existing one -- but only if the
        resend-interval and daily caps allow it. Returns the outcome.

        The guard lives in the ON CONFLICT WHERE clause, so a concurrent
        second request within 60s loses the race (its UPDATE matches no row
        and RETURNING is empty) instead of both sending.
        """
        now = datetime.now(UTC)
        today = now.date()
        stmt = (
            pg_insert(EmailVerificationModel)
            .values(
                id=uuid4(),
                email=email,
                purpose=purpose.value,
                code_hash=hash_token(code),
                expires_at=now + VERIFICATION_CODE_TTL,
                attempts=0,
                last_sent_at=now,
                sent_day=today,
                sent_today=1,
            )
            .on_conflict_do_update(
                index_elements=[
                    EmailVerificationModel.email,
                    EmailVerificationModel.purpose,
                ],
                set_={
                    "code_hash": hash_token(code),
                    "expires_at": now + VERIFICATION_CODE_TTL,
                    "attempts": 0,
                    "verified_at": None,
                    "last_sent_at": now,
                    "sent_day": today,
                    "sent_today": case(
                        (EmailVerificationModel.sent_day == today,
                         EmailVerificationModel.sent_today + 1),
                        else_=1,
                    ),
                },
                where=(
                    (EmailVerificationModel.last_sent_at <= now - RESEND_INTERVAL)
                    & (
                        (EmailVerificationModel.sent_day != today)
                        | (EmailVerificationModel.sent_today < MAX_DAILY_CODES)
                    )
                ),
            )
            .returning(EmailVerificationModel.email)
        )
        result = await self._session.execute(stmt)
        if result.first() is not None:
            return RequestCodeOutcome.SENT

        # The guard refused the write. Diagnose which limit it was -- only
        # for the error message; the atomic guard is the actual arbiter.
        row = await self._session.scalar(
            select(EmailVerificationModel).where(
                EmailVerificationModel.email == email,
                EmailVerificationModel.purpose == purpose.value,
            )
        )
        if (
            row is not None
            and row.sent_day == today
            and row.sent_today >= MAX_DAILY_CODES
        ):
            return RequestCodeOutcome.DAILY_LIMIT
        return RequestCodeOutcome.THROTTLED

    async def confirm(
        self, email: str, code: str, purpose: VerificationPurpose
    ) -> VerifyCodeOutcome:
        """Consume the code if it is correct, unexpired, unused and under the
        attempt cap; otherwise count the wrong guess and diagnose why."""
        now = datetime.now(UTC)
        code_hash = hash_token(code)

        # Correct code: atomically mark verified so it cannot be replayed.
        consumed = await self._session.execute(
            update(EmailVerificationModel)
            .where(
                EmailVerificationModel.email == email,
                EmailVerificationModel.purpose == purpose.value,
                EmailVerificationModel.code_hash == code_hash,
                EmailVerificationModel.verified_at.is_(None),
                EmailVerificationModel.expires_at > now,
                EmailVerificationModel.attempts < MAX_VERIFY_ATTEMPTS,
            )
            .values(verified_at=now)
            .returning(EmailVerificationModel.id)
        )
        if consumed.first() is not None:
            return VerifyCodeOutcome.OK

        # Wrong code while still valid: atomically +1 the attempt counter.
        # Flush so the increment is inside the current transaction -- the
        # caller may commit it even when the overall confirm fails (an
        # exhausted-attempts guard must not be rolled back just because the
        # account creation that follows was rejected).
        await self._session.execute(
            update(EmailVerificationModel)
            .where(
                EmailVerificationModel.email == email,
                EmailVerificationModel.purpose == purpose.value,
                EmailVerificationModel.verified_at.is_(None),
                EmailVerificationModel.expires_at > now,
                EmailVerificationModel.attempts < MAX_VERIFY_ATTEMPTS,
                EmailVerificationModel.code_hash != code_hash,
            )
            .values(attempts=EmailVerificationModel.attempts + 1)
        )
        await self._session.flush()

        # Diagnose the failure -- read after the writes, only for messaging.
        row = await self._session.scalar(
            select(EmailVerificationModel).where(
                EmailVerificationModel.email == email,
                EmailVerificationModel.purpose == purpose.value,
            )
        )
        if row is None:
            return VerifyCodeOutcome.NO_CODE
        if row.verified_at is not None:
            return VerifyCodeOutcome.ALREADY_USED
        if row.expires_at <= now:
            return VerifyCodeOutcome.EXPIRED
        if row.attempts >= MAX_VERIFY_ATTEMPTS:
            return VerifyCodeOutcome.TOO_MANY_ATTEMPTS
        return VerifyCodeOutcome.WRONG_CODE


class EmailVerificationService:
    """Sending + verifying orchestration, with an injectable sender."""

    def __init__(
        self, session: AsyncSession, sender: EmailSender | None = None
    ) -> None:
        self._repo = EmailVerificationRepository(session)
        self._sender = sender

    async def request_code(
        self, email: str, purpose: VerificationPurpose
    ) -> RequestCodeOutcome:
        code = generate_verification_code()
        outcome = await self._repo.request(email, code, purpose)
        if outcome is RequestCodeOutcome.SENT:
            if self._sender is None:
                raise EmailServiceUnavailable("邮件服务未配置")
            # A failed send raises -- the caller rolls back the just-written
            # code row, so a code that never reached the inbox does not
            # consume the address's daily quota (honesty, CLAUDE.md 7).
            await self._sender.send_verification_code(email, code, purpose.value)
        return outcome

    async def confirm(
        self, email: str, code: str, purpose: VerificationPurpose
    ) -> VerifyCodeOutcome:
        return await self._repo.confirm(email, code, purpose)


__all__ = [
    "EmailVerificationRepository",
    "EmailVerificationService",
    "InvalidEmail",
    "InvalidVerificationCode",
    "MAX_DAILY_CODES",
    "MAX_VERIFY_ATTEMPTS",
    "RequestCodeOutcome",
    "RESEND_INTERVAL_SECONDS",
    "VerificationPurpose",
    "VerifyCodeOutcome",
    "normalize_email",
]
