"""Email verification for registration, end to end.

Two-phase registration against the real ASGI app: ``POST /register`` emails
a 6-digit code (202) and creates nothing; ``POST /register/confirm`` verifies
the code and creates the account (201). This file covers the honest failure
paths too: SMTP unconfigured -> 503 with no account created, wrong / expired /
replayed / exhausted codes -> 422, unique email, throttle, and the legacy
no-email account that must keep logging in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from packages.accounts.models import EmailVerificationModel, UserModel
from packages.accounts.verification import MAX_VERIFY_ATTEMPTS
from tests.conftest import RECORDED_CODES, register_user

AUTH_PATH = "/api/auth"


def _now() -> datetime:
    return datetime.now(UTC)


async def test_two_phase_registration_login_me(
    api_client: httpx.AsyncClient,
) -> None:
    email = "phase@poliscope.test"
    sent = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "phase-user", "password": "pw-123456", "email": email},
    )
    assert sent.status_code == 202, sent.text
    assert sent.json()["status"] == "code_sent"
    assert sent.json()["retry_after"] == 60

    code = RECORDED_CODES[email]
    confirmed = await api_client.post(
        f"{AUTH_PATH}/register/confirm",
        json={
            "username": "phase-user",
            "password": "pw-123456",
            "email": email,
            "code": code,
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    token = confirmed.json()["token"]
    assert token

    me = await api_client.get(
        f"{AUTH_PATH}/me", headers={"authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["username"] == "phase-user"


async def test_register_without_smtp_is_503_and_creates_nothing(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    app_sessions: object,
) -> None:
    """Unconfigured SMTP means registration is refused (503), never silently
    skipped, and no user or verification row is left behind."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(app_sessions, async_sessionmaker)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)

    email = "nosmtp@poliscope.test"
    response = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "nosmtp-user", "password": "pw-123456", "email": email},
    )
    assert response.status_code == 503, response.text
    assert "邮件服务未配置" in response.text

    async with app_sessions() as session:
        user = await session.scalar(
            select(UserModel).where(UserModel.username == "nosmtp-user")
        )
        assert user is None
        verification = await session.scalar(
            select(EmailVerificationModel).where(
                EmailVerificationModel.email == email
            )
        )
        assert verification is None


async def test_email_must_be_unique(
    api_client: httpx.AsyncClient,
) -> None:
    email = "unique@poliscope.test"
    await register_user(api_client, "unique-owner", email=email)
    # A second account trying the same email: even with a fresh code, confirm
    # rejects with 409 -- one email owns at most one account.
    confirmed = await api_client.post(
        f"{AUTH_PATH}/register/confirm",
        json={
            "username": "unique-other",
            "password": "pw-123456",
            "email": email,
            "code": RECORDED_CODES[email],
        },
    )
    assert confirmed.status_code == 409, confirmed.text
    assert "该邮箱已被注册" in confirmed.text


async def test_wrong_code_counts_attempts_then_locks(
    api_client: httpx.AsyncClient,
    app_sessions: object,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(app_sessions, async_sessionmaker)
    email = "attempts@poliscope.test"
    sent = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "attempts-user", "password": "pw-123456", "email": email},
    )
    assert sent.status_code == 202
    code = RECORDED_CODES[email]

    for _ in range(MAX_VERIFY_ATTEMPTS):
        wrong = await api_client.post(
            f"{AUTH_PATH}/register/confirm",
            json={
                "username": "attempts-user",
                "password": "pw-123456",
                "email": email,
                "code": "000000",
            },
        )
        assert wrong.status_code == 422, wrong.text

    async with app_sessions() as session:
        row = await session.scalar(
            select(EmailVerificationModel).where(
                EmailVerificationModel.email == email
            )
        )
        assert row is not None
        assert row.attempts == MAX_VERIFY_ATTEMPTS

    # The correct code is now refused too -- the cap is exhausted.
    locked = await api_client.post(
        f"{AUTH_PATH}/register/confirm",
        json={
            "username": "attempts-user",
            "password": "pw-123456",
            "email": email,
            "code": code,
        },
    )
    assert locked.status_code == 422
    assert "尝试次数过多" in locked.text


async def test_expired_code_is_refused(
    api_client: httpx.AsyncClient,
    app_sessions: object,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(app_sessions, async_sessionmaker)
    email = "expired@poliscope.test"
    sent = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "expired-user", "password": "pw-123456", "email": email},
    )
    assert sent.status_code == 202
    code = RECORDED_CODES[email]

    async with app_sessions() as session:
        await session.execute(
            update(EmailVerificationModel)
            .where(EmailVerificationModel.email == email)
            .values(expires_at=_now() - timedelta(minutes=1))
        )
        await session.commit()

    expired = await api_client.post(
        f"{AUTH_PATH}/register/confirm",
        json={
            "username": "expired-user",
            "password": "pw-123456",
            "email": email,
            "code": code,
        },
    )
    assert expired.status_code == 422
    assert "已过期" in expired.text


async def test_code_cannot_be_replayed(
    api_client: httpx.AsyncClient,
) -> None:
    """A consumed code cannot create a second account: the same email is now
    taken (409). The code is bound to the email, and the email to one account,
    so replaying it is blocked at the uniqueness gate."""
    email = "replay@poliscope.test"
    await register_user(api_client, "replay-user", email=email)
    again = await api_client.post(
        f"{AUTH_PATH}/register/confirm",
        json={
            "username": "replay-user-2",
            "password": "pw-123456",
            "email": email,
            "code": RECORDED_CODES[email],
        },
    )
    assert again.status_code == 409
    assert "该邮箱已被注册" in again.text


async def test_resend_within_60s_is_throttled(
    api_client: httpx.AsyncClient,
    app_sessions: object,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(app_sessions, async_sessionmaker)
    email = "throttle@poliscope.test"
    first = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "throttle-user", "password": "pw-123456", "email": email},
    )
    assert first.status_code == 202

    second = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "throttle-user", "password": "pw-123456", "email": email},
    )
    assert second.status_code == 429
    assert "发送太频繁" in second.text

    # Moving last_sent_at into the past lets the resend through.
    async with app_sessions() as session:
        await session.execute(
            update(EmailVerificationModel)
            .where(EmailVerificationModel.email == email)
            .values(last_sent_at=_now() - timedelta(seconds=61))
        )
        await session.commit()
    again = await api_client.post(
        f"{AUTH_PATH}/register",
        json={"username": "throttle-user", "password": "pw-123456", "email": email},
    )
    assert again.status_code == 202


async def test_legacy_account_without_email_still_logs_in(
    api_client: httpx.AsyncClient,
    app_sessions: object,
) -> None:
    """An account created before email verification has email NULL; it must
    keep logging in exactly as before (no forced re-verification)."""
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from packages.accounts.security import hash_password

    assert isinstance(app_sessions, async_sessionmaker)
    username = f"legacy-{uuid4().hex[:8]}"
    async with app_sessions() as session:
        session.add(
            UserModel(
                id=uuid4(),
                username=username,
                password_hash=hash_password("legacy-pw"),
                email=None,
            )
        )
        await session.commit()

    response = await api_client.post(
        f"{AUTH_PATH}/login",
        json={"username": username, "password": "legacy-pw"},
    )
    assert response.status_code == 200
    assert response.json()["token"]


async def test_confirm_unknown_code_is_422(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        f"{AUTH_PATH}/register/confirm",
        json={
            "username": "never-requested",
            "password": "pw-123456",
            "email": "never@poliscope.test",
            "code": "123456",
        },
    )
    assert response.status_code == 422
    assert "请先获取验证码" in response.text
