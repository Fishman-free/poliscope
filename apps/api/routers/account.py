"""Account self-management: avatar, username, password, and deletion.

All endpoints require a logged-in account (``CurrentUserDep``). Everything
here mutates the caller's own account; there is no admin surface.

Avatar uploads go to the private object store under ``avatars/{user_id}`` --
only the object key lives on the ``users`` row (CLAUDE.md 16: uploaded
material never leaks through logs or exports), and ``GET /avatar`` serves the
bytes back through the authenticated API rather than exposing a public URL.

Account deletion permanently removes every record that belongs to the
account across every module -- tasks (via the shared cascade), knowledge
bases and documents, skills, settings, auth tokens and the user row itself.
``DELETE /api/account`` requires the current password as proof.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import delete, select

from apps.api.dependencies import (
    CurrentUserDep,
    ObjectStoreDep,
    SessionDep,
)
from apps.api.schemas import (
    ChangePasswordRequest,
    ChangeUsernameRequest,
    DeleteAccountRequest,
)
from apps.api.task_lifecycle import delete_task_cascade
from packages.accounts.repository import (
    InvalidCredentials,
    UsernameTaken,
    UsersRepository,
)
from packages.accounts.service import (
    AccountNotFound,
    AuthService,
    InvalidRegistration,
)
from packages.knowledge.models import KnowledgeBaseModel, KnowledgeDocumentModel
from packages.models.settings import AppSettingsModel
from packages.papers.object_store import ObjectNotFound
from packages.research.models import ResearchTaskModel
from packages.skills.models import SkillModel

router = APIRouter()

MAX_AVATAR_BYTES = 2 * 1024 * 1024

# content_type -> (suffix, magic bytes). Only PNG and JPEG are accepted.
_IMAGE_MAGIC: dict[str, tuple[str, bytes]] = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
}


def _validate_avatar(content: bytes, content_type: str) -> tuple[str, str]:
    """Return (suffix, normalized content_type) or raise 422."""
    entry = _IMAGE_MAGIC.get(content_type)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="头像仅支持 PNG 或 JPG 图片",
        )
    suffix, magic = entry
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="头像不能超过 2MB",
        )
    if not content.startswith(magic):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="文件内容与声明的图片格式不符",
        )
    return suffix, content_type


@router.post("/avatar")
async def upload_avatar(
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    current_user: CurrentUserDep,
    object_store: ObjectStoreDep,
) -> dict[str, Any]:
    """Replace (or set) the account's avatar image."""
    content = await file.read()
    suffix, content_type = _validate_avatar(content, file.content_type or "")
    stored = object_store.store_named(
        f"avatars/{current_user.id}",
        content,
        suffix=suffix,
        content_type=content_type,
    )
    await AuthService(session).set_avatar_key(current_user.id, stored.object_key)
    await session.commit()
    return {"content_type": content_type, "size_bytes": len(content)}


@router.get("/avatar")
async def get_avatar(
    session: SessionDep,
    current_user: CurrentUserDep,
    object_store: ObjectStoreDep,
) -> Response:
    """Serve the avatar bytes. The caller must be authenticated -- the
    object store is private and there is no public URL."""
    avatar_key = current_user.avatar_key
    if not avatar_key:
        raise HTTPException(status_code=404, detail="no avatar set")
    try:
        content = object_store.retrieve(avatar_key)
    except ObjectNotFound as error:
        raise HTTPException(status_code=404, detail="avatar not found") from error
    suffix = avatar_key.rsplit(".", 1)[-1].lower() if "." in avatar_key else ""
    media_type = "image/jpeg" if suffix == "jpg" else "image/png"
    return Response(content=content, media_type=media_type)


@router.post("/username")
async def change_username(
    request: ChangeUsernameRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Rename the account. Requires the current password."""
    service = AuthService(session)
    try:
        updated = await service.change_username(
            current_user.id, request.new_username, request.password
        )
    except AccountNotFound:
        raise HTTPException(status_code=404, detail="账号不存在") from None
    except InvalidCredentials:
        raise HTTPException(status_code=401, detail="原密码错误") from None
    except UsernameTaken:
        raise HTTPException(status_code=409, detail="用户名已被占用") from None
    except InvalidRegistration as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return {"username": updated.username}


@router.post("/password")
async def change_password(
    request: ChangePasswordRequest,
    raw_request: Request,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Replace the password after verifying the old one. Other sessions are
    revoked so the old password stops working on other devices immediately."""
    bearer = raw_request.headers.get("authorization", "")
    keep_token = bearer[7:].strip() if bearer.lower().startswith("bearer ") else None
    service = AuthService(session)
    try:
        await service.change_password(
            current_user.id,
            request.old_password,
            request.new_password,
            keep_token=keep_token,
        )
    except AccountNotFound:
        raise HTTPException(status_code=404, detail="账号不存在") from None
    except InvalidCredentials:
        raise HTTPException(status_code=401, detail="原密码错误") from None
    except InvalidRegistration as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return {"status": "ok"}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    request: DeleteAccountRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
    object_store: ObjectStoreDep,
) -> None:
    """Permanently delete the account and every record that belongs to it."""
    service = AuthService(session)
    if not await service.verify_credentials(current_user.id, request.password):
        raise HTTPException(status_code=401, detail="密码错误")

    user_id: UUID = current_user.id

    # 1) The user's tasks (cascade removes every task-scoped record).
    task_ids = (
        await session.execute(
            select(ResearchTaskModel.task_id).where(
                ResearchTaskModel.user_id == user_id
            )
        )
    ).scalars().all()
    for task_id in task_ids:
        await delete_task_cascade(session, task_id)

    # 2) Knowledge bases (documents first -- sources.knowledge_document_id
    #    pointed into this user's tasks, already removed above).
    kb_ids = (
        await session.execute(
            select(KnowledgeBaseModel.id).where(
                KnowledgeBaseModel.user_id == user_id
            )
        )
    ).scalars().all()
    if kb_ids:
        await session.execute(
            delete(KnowledgeDocumentModel).where(
                KnowledgeDocumentModel.knowledge_base_id.in_(kb_ids)
            )
        )
        await session.execute(
            delete(KnowledgeBaseModel).where(KnowledgeBaseModel.id.in_(kb_ids))
        )

    # 3) Skills, settings, auth tokens, verification codes (by email).
    await session.execute(delete(SkillModel).where(SkillModel.user_id == user_id))
    await session.execute(
        delete(AppSettingsModel).where(AppSettingsModel.user_id == user_id)
    )
    from packages.accounts.models import AuthTokenModel, EmailVerificationModel

    await session.execute(
        delete(AuthTokenModel).where(AuthTokenModel.user_id == user_id)
    )
    if current_user.email:
        await session.execute(
            delete(EmailVerificationModel).where(
                EmailVerificationModel.email == current_user.email
            )
        )

    # 4) Avatar object stays orphaned (the store has no delete API) -- it is
    #    private and content-addressed, so it leaks nothing.

    # 5) The user row itself.
    await UsersRepository(session).delete(user_id)
    await session.commit()


__all__ = ["router"]
