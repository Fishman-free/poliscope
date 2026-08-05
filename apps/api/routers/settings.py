"""Permanent model-gateway settings endpoints.

The web workbench keeps the researcher's model endpoint (base URL, API key,
model name) as a right-side permanent setting instead of a per-task form
field. The API key is written exactly like the per-task ``task_model_config``
already is -- stored server-side, never echoed back: responses carry only
``has_api_key`` (CLAUDE.md 16).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.schemas import ModelSettingsUpdate
from packages.models.settings import ModelSettingsRepository

router = APIRouter()


def _dto(
    base_url: str | None,
    model_name: str | None,
    has_api_key: bool,
) -> dict[str, Any]:
    # The key itself never leaves the server (CLAUDE.md 16); the client only
    # learns whether one is configured, so the form can show "已配置" without
    # ever being able to display or leak the secret.
    return {
        "base_url": base_url,
        "model_name": model_name,
        "has_api_key": has_api_key,
    }


@router.get("/model")
async def get_model_settings(
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    settings = await ModelSettingsRepository(session).get(current_user.id)
    return _dto(
        base_url=settings.model_base_url,
        model_name=settings.model_name,
        has_api_key=settings.has_api_key,
    )


@router.put("/model")
async def put_model_settings(
    request: ModelSettingsUpdate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Save the account's permanent model settings (keep-vs-replace for the
    key). ``clear_api_key`` is the only way to remove a stored key -- a blank
    ``api_key`` on its own means "leave the stored key alone", so an
    accidental empty PUT cannot wipe the researcher's credentials.
    """
    repository = ModelSettingsRepository(session)
    current = await repository.get(current_user.id)

    if request.clear_api_key:
        api_key: str | None = None
    elif request.api_key and request.api_key.strip():
        api_key = request.api_key.strip()
    else:
        api_key = current.model_api_key

    saved = await repository.save(
        current_user.id,
        base_url=request.base_url.strip() if request.base_url else None,
        api_key=api_key,
        model_name=request.model_name.strip() if request.model_name else None,
    )
    await session.commit()
    return _dto(
        base_url=saved.model_base_url,
        model_name=saved.model_name,
        has_api_key=saved.has_api_key,
    )
