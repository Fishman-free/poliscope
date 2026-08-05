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

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.schemas import AuthCredentials
from packages.accounts.repository import InvalidCredentials, UsernameTaken
from packages.accounts.service import AuthService, InvalidRegistration

router = APIRouter()


def _session_dto(user_id: str, username: str, token: str) -> dict[str, Any]:
    # id is exposed so the client (and integration tests) can scope rows to
    # the account; the token itself is what authenticates.
    return {"id": user_id, "username": username, "token": token}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    credentials: AuthCredentials,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        result = await AuthService(session).register(
            credentials.username, credentials.password
        )
    except UsernameTaken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="username is already taken",
        ) from None
    except InvalidRegistration as error:
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
    }
