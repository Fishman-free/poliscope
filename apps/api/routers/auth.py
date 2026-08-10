"""Registration, login, logout, and session verification.

Public endpoints (the whole point is to let someone in). Login returns the
bearer token exactly once; every protected endpoint then validates it via
``dependencies.get_current_user``. The token lives in the browser's
localStorage for remember-me, expires after 30 days, and is stored
server-side only as its sha256 (packages/accounts/security.py).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from apps.api.dependencies import CurrentUserDep, EmailSenderDep, SessionDep
from apps.api.schemas import (
    AuthCredentials,
    ForgotPasswordRequest,
    RegistrationConfirm,
    RegistrationRequest,
    ResetPasswordRequest,
)
from packages.accounts.email_sender import EmailServiceUnavailable
from packages.accounts.repository import (
    EmailTaken,
    InvalidCredentials,
    UsernameTaken,
)
from packages.accounts.service import (
    AccountNotFound,
    AuthService,
    InvalidRegistration,
    InvalidVerificationCode,
    RequestCodeOutcome,
)
from packages.accounts.verification import RESEND_INTERVAL_SECONDS, InvalidEmail

router = APIRouter()


def _session_dto(user_id: str, username: str, token: str) -> dict[str, Any]:
    # id is exposed so the client (and integration tests) can scope rows to
    # the account; the token itself is what authenticates.
    return {"id": user_id, "username": username, "token": token}


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def register(
    credentials: RegistrationRequest,
    session: SessionDep,
    sender: EmailSenderDep,
) -> dict[str, Any]:
    """Phase 1: validate and email a verification code. No account is created
    until /register/confirm proves the code. Honest when SMTP is missing: 503,
    never a silent skip or an account without an email (CLAUDE.md 2.7)."""
    if sender is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="邮件服务未配置，注册已停用（请联系部署方配置 SMTP）",
        )
    service = AuthService(session, sender=sender)
    try:
        outcome = await service.request_verification(
            credentials.username, credentials.password, credentials.email
        )
    except UsernameTaken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已被占用",
        ) from None
    except InvalidEmail as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except InvalidRegistration as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except EmailServiceUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="邮件服务暂不可用，验证码未发送，请稍后再试",
        ) from None
    if outcome is RequestCodeOutcome.THROTTLED:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="发送太频繁，请 60 秒后再试",
        )
    if outcome is RequestCodeOutcome.DAILY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="今日验证码发送次数已达上限",
        )
    return {"status": "code_sent", "retry_after": RESEND_INTERVAL_SECONDS}


@router.post("/register/confirm", status_code=status.HTTP_201_CREATED)
async def register_confirm(
    credentials: RegistrationConfirm,
    session: SessionDep,
) -> dict[str, Any]:
    """Phase 2: verify the emailed code and create the account (auto-login)."""
    service = AuthService(session)  # confirm sends no mail, no sender needed
    try:
        result = await service.register(
            credentials.username,
            credentials.password,
            credentials.email,
            credentials.code,
        )
    except UsernameTaken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已被占用",
        ) from None
    except EmailTaken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已被注册",
        ) from None
    except InvalidEmail as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except InvalidRegistration as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except InvalidVerificationCode as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _session_dto(str(result.user.id), result.user.username, result.token)


@router.post("/login")
async def login(
    credentials: AuthCredentials,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        result = await AuthService(session).login(
            credentials.username, credentials.password
        )
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        ) from None
    return _session_dto(str(result.user.id), result.user.username, result.token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, session: SessionDep) -> None:
    """Revoke the presented token. Idempotent: an already-revoked or unknown
    token still answers 204, because there is nothing left to protect."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        await AuthService(session).logout(header[7:].strip())
    await session.commit()


@router.get("/me")
async def me(
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Who am I? The frontend calls this at startup to decide whether the
    remembered token is still valid (本机免登录)."""
    return {
        "username": current_user.username,
        "created_at": current_user.created_at.isoformat(),
        "has_avatar": bool(current_user.avatar_key),
    }


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request: ForgotPasswordRequest,
    session: SessionDep,
    sender: EmailSenderDep,
) -> dict[str, Any]:
    """Email a password-reset code. Answers 202 even when the email is
    unknown (anti-enumeration, CLAUDE.md 2.8): a missing account simply gets
    no mail, indistinguishable from one that did."""
    if sender is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="邮件服务未配置，无法发送重置邮件",
        )
    service = AuthService(session, sender=sender)
    try:
        outcome = await service.request_password_reset(request.email)
    except InvalidEmail as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except EmailServiceUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="邮件服务暂不可用，验证码未发送，请稍后再试",
        ) from None
    if outcome is RequestCodeOutcome.THROTTLED:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="发送太频繁，请 60 秒后再试",
        )
    if outcome is RequestCodeOutcome.DAILY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="今日验证码发送次数已达上限",
        )
    # outcome is None (unknown email) or SENT: both answer the same 202.
    return {"status": "code_sent", "retry_after": RESEND_INTERVAL_SECONDS}


@router.post("/reset-password")
async def reset_password(
    request: ResetPasswordRequest,
    session: SessionDep,
) -> dict[str, Any]:
    """Verify a reset code and set a new password. All sessions are revoked
    so the old password stops working everywhere immediately."""
    service = AuthService(session)
    try:
        await service.reset_password(
            request.email, request.code, request.password
        )
    except AccountNotFound:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="该邮箱未注册",
        ) from None
    except InvalidRegistration as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except InvalidVerificationCode as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    await session.commit()
    return {"status": "ok"}
