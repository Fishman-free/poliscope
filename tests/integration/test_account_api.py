"""Account self-management API: avatar, username, password, password reset,
and permanent deletion with full cascade cleanup.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.accounts.models import EmailVerificationModel, UserModel
from packages.knowledge.models import KnowledgeBaseModel, KnowledgeDocumentModel
from packages.models.settings import AppSettingsModel
from packages.research.models import ResearchTaskModel
from tests.conftest import RECORDED_CODES, register_user

ACCOUNT_PATH = "/api/account"
AUTH_PATH = "/api/auth"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


async def _register_fresh(api_client: httpx.AsyncClient) -> dict[str, Any]:
    username = f"acct-{uuid4().hex[:8]}"
    return await register_user(api_client, username, email=f"{username}@poliscope.test")


def _headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_avatar_upload_and_fetch_round_trip(
    api_client: httpx.AsyncClient,
) -> None:
    account = await _register_fresh(api_client)
    headers = _headers(account["token"])

    missing = await api_client.get(f"{ACCOUNT_PATH}/avatar", headers=headers)
    assert missing.status_code == 404

    uploaded = await api_client.post(
        f"{ACCOUNT_PATH}/avatar",
        headers=headers,
        files={"file": ("avatar.png", PNG_BYTES, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["content_type"] == "image/png"

    fetched = await api_client.get(f"{ACCOUNT_PATH}/avatar", headers=headers)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.content == PNG_BYTES

    me = await api_client.get(f"{AUTH_PATH}/me", headers=headers)
    assert me.json()["has_avatar"] is True


async def test_avatar_rejects_bad_type_and_oversize(
    api_client: httpx.AsyncClient,
) -> None:
    account = await _register_fresh(api_client)
    headers = _headers(account["token"])

    bad = await api_client.post(
        f"{ACCOUNT_PATH}/avatar",
        headers=headers,
        files={"file": ("avatar.png", b"not an image", "image/png")},
    )
    assert bad.status_code == 422

    wrong_type = await api_client.post(
        f"{ACCOUNT_PATH}/avatar",
        headers=headers,
        files={"file": ("avatar.gif", PNG_BYTES, "image/gif")},
    )
    assert wrong_type.status_code == 422

    big = await api_client.post(
        f"{ACCOUNT_PATH}/avatar",
        headers=headers,
        files={"file": ("avatar.png", PNG_BYTES * 300000, "image/png")},
    )
    assert big.status_code == 422


async def test_avatar_requires_auth(api_client: httpx.AsyncClient) -> None:
    api_client.headers.pop("authorization")
    response = await api_client.post(
        f"{ACCOUNT_PATH}/avatar",
        files={"file": ("a.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 401


async def test_change_username_requires_old_password(
    api_client: httpx.AsyncClient,
) -> None:
    account = await _register_fresh(api_client)
    headers = _headers(account["token"])

    wrong = await api_client.post(
        f"{ACCOUNT_PATH}/username",
        headers=headers,
        json={"new_username": "new-name", "password": "wrong"},
    )
    assert wrong.status_code == 401

    ok = await api_client.post(
        f"{ACCOUNT_PATH}/username",
        headers=headers,
        json={"new_username": "new-name", "password": "test-password-123"},
    )
    assert ok.status_code == 200
    assert ok.json()["username"] == "new-name"

    me = await api_client.get(f"{AUTH_PATH}/me", headers=headers)
    assert me.json()["username"] == "new-name"


async def test_change_username_rejects_taken_name(
    api_client: httpx.AsyncClient,
) -> None:
    account_a = await _register_fresh(api_client)
    account_b = await _register_fresh(api_client)
    headers = _headers(account_a["token"])

    taken = await api_client.post(
        f"{ACCOUNT_PATH}/username",
        headers=headers,
        json={"new_username": account_b["username"], "password": "test-password-123"},
    )
    assert taken.status_code == 409


async def test_change_password_and_old_password_fails(
    api_client: httpx.AsyncClient,
) -> None:
    account = await _register_fresh(api_client)
    headers = _headers(account["token"])

    wrong_old = await api_client.post(
        f"{ACCOUNT_PATH}/password",
        headers=headers,
        json={"old_password": "wrong", "new_password": "brand-new-password"},
    )
    assert wrong_old.status_code == 401

    ok = await api_client.post(
        f"{ACCOUNT_PATH}/password",
        headers=headers,
        json={
            "old_password": "test-password-123",
            "new_password": "brand-new-password",
        },
    )
    assert ok.status_code == 200

    old_login = await api_client.post(
        f"{AUTH_PATH}/login",
        json={"username": account["username"], "password": "test-password-123"},
    )
    assert old_login.status_code == 401

    new_login = await api_client.post(
        f"{AUTH_PATH}/login",
        json={"username": account["username"], "password": "brand-new-password"},
    )
    assert new_login.status_code == 200


async def test_forgot_and_reset_password_flow(
    api_client: httpx.AsyncClient,
) -> None:
    account = await _register_fresh(api_client)
    email = f"{account['username']}@poliscope.test"

    forgot = await api_client.post(
        f"{AUTH_PATH}/forgot-password", json={"email": email}
    )
    assert forgot.status_code == 202, forgot.text
    code = RECORDED_CODES.get(email)
    assert code, "reset code not recorded"

    reset = await api_client.post(
        f"{AUTH_PATH}/reset-password",
        json={"email": email, "code": code, "password": "reset-new-password"},
    )
    assert reset.status_code == 200, reset.text

    old_login = await api_client.post(
        f"{AUTH_PATH}/login",
        json={"username": account["username"], "password": "test-password-123"},
    )
    assert old_login.status_code == 401
    new_login = await api_client.post(
        f"{AUTH_PATH}/login",
        json={"username": account["username"], "password": "reset-new-password"},
    )
    assert new_login.status_code == 200


async def test_forgot_password_unknown_email_is_202_and_sends_nothing(
    api_client: httpx.AsyncClient,
) -> None:
    before = len(RECORDED_CODES)
    response = await api_client.post(
        f"{AUTH_PATH}/forgot-password", json={"email": "nobody@poliscope.test"}
    )
    assert response.status_code == 202
    assert len(RECORDED_CODES) == before


async def test_reset_password_with_wrong_code_is_422(
    api_client: httpx.AsyncClient,
) -> None:
    account = await _register_fresh(api_client)
    email = f"{account['username']}@poliscope.test"
    response = await api_client.post(
        f"{AUTH_PATH}/reset-password",
        json={"email": email, "code": "000000", "password": "some-new-password"},
    )
    assert response.status_code == 422


async def test_delete_account_requires_password_and_cleans_everything(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    account = await _register_fresh(api_client)
    user_id = UUID(account["id"])
    token = account["token"]
    headers = _headers(token)

    # Seed account-owned rows across the modules the cascade must clear:
    # a task, a knowledge base + document, a settings row, and a
    # verification-code row for the account's email.
    async with app_sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(), task_id=uuid4(), question="q", status="QUEUED",
                created_by=account["username"], user_id=user_id,
                wall_clock_minutes=30, model_cost_usd=0, tool_call_limit=10,
                source_limit=5, user_evidence={},
            )
        )
        kb = KnowledgeBaseModel(
            id=uuid4(), name="kb", created_by=account["username"], user_id=user_id
        )
        session.add(kb)
        await session.flush()
        session.add(
            KnowledgeDocumentModel(
                id=uuid4(),
                knowledge_base_id=kb.id,
                title="doc",
                object_key=f"knowledge/{kb.id}/x.txt",
                content_hash="x" * 64,
                content_type="text/plain",
                size_bytes=1,
                page_count=1,
                text_content="seed",
                created_by=account["username"],
            )
        )
        session.add(
            AppSettingsModel(user_id=user_id, model_name="deepseek-chat")
        )
        # The register code already exists from register_user; a second row
        # with a different purpose proves reset codes are cleaned too.
        session.add(
            EmailVerificationModel(
                id=uuid4(),
                email=f"{account['username']}@poliscope.test",
                purpose="reset",
                code_hash="x" * 64,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                last_sent_at=datetime.now(UTC),
                sent_day=datetime.now(UTC).date(),
                sent_today=1,
            )
        )
        await session.commit()

    # Wrong password -> 401, nothing deleted.
    wrong = await api_client.request(
        "DELETE", ACCOUNT_PATH, headers=headers, json={"password": "wrong"}
    )
    assert wrong.status_code == 401

    # Correct password -> 204.
    deleted = await api_client.request(
        "DELETE", ACCOUNT_PATH, headers=headers, json={"password": "test-password-123"}
    )
    assert deleted.status_code == 204

    async with app_sessions() as session:
        assert (
            await session.get(UserModel, user_id)
        ) is None
        assert (
            (
                await session.execute(
                    select(ResearchTaskModel).where(
                        ResearchTaskModel.user_id == user_id
                    )
                )
            ).scalars().first()
            is None
        )
        assert (
            (
                await session.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.user_id == user_id
                    )
                )
            ).scalars().first()
            is None
        )
        assert (
            await session.get(AppSettingsModel, user_id)
        ) is None
        assert (
            (
                await session.execute(
                    select(EmailVerificationModel).where(
                        EmailVerificationModel.email
                        == f"{account['username']}@poliscope.test"
                    )
                )
            ).scalars().first()
            is None
        )

    # The old token no longer authenticates.
    me = await api_client.get(f"{AUTH_PATH}/me", headers=headers)
    assert me.status_code == 401
