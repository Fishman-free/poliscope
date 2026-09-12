"""Account self-management API: avatar, username, password, password reset."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

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
