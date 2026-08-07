"""Permanent model-gateway settings endpoints.

The web workbench keeps the researcher's model endpoint (base URL, API key,
model name) as a right-side permanent setting instead of a per-task form
field. The API key is written exactly like the per-task ``task_model_config``
already is -- stored server-side, never echoed back: responses carry only
``has_api_key`` (CLAUDE.md 16).

**A configuration must work before it may be stored.** A real incident showed
what happens when arbitrary input is accepted verbatim: a researcher saved the
DeepSeek *console portal* as the API endpoint, and the whole council went
absent. So ``PUT /model`` runs ``probe_endpoint`` against the resolved
configuration first and refuses to save when the connection fails (HTTP 422
with the reason), and ``POST /model/test`` lets the UI probe the researcher's
current form values without saving anything.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.schemas import ModelSettingsUpdate
from packages.models.endpoint_config import (
    DEFAULT_MODEL_NAME,
    ProbeResult,
    normalize_base_url,
    probe_endpoint,
)
from packages.models.settings import ModelSettingsRepository, StoredModelSettings

router = APIRouter()


def _dto(
    base_url: str | None,
    model_name: str | None,
    has_api_key: bool,
) -> dict[str, Any]:
    # The key itself never leaves the server (CLAUDE.md 16); the client only
    # learns whether one is configured, so the form can show "已配置" without
    # ever being able to display or leak the secret. ``usable`` is the same
    # condition task creation applies when inheriting: both URL and key must
    # be present, or the saved settings are never applied to any task.
    return {
        "base_url": base_url,
        "model_name": model_name,
        "has_api_key": has_api_key,
        "usable": bool(base_url and has_api_key),
    }


def _resolve_inputs(
    request: ModelSettingsUpdate,
    current: StoredModelSettings,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve the request against what is stored.

    Returns ``(base_url, api_key, model_name, correction_hint)`` with the same
    keep-vs-replace semantics the old handler had: a blank ``api_key`` keeps
    the stored key, ``clear_api_key`` drops it, and a blank ``base_url``/``model_name``
    means "go back to the deployment default" (None is stored). ``base_url``
    is normalised (scheme, trailing slash, portal rewrite) -- never stored as
    typed; ``correction_hint`` is only surfaced by the test endpoint so the
    UI can tell the researcher their portal address was rewritten.
    """
    raw_url = request.base_url.strip() if request.base_url else ""
    if raw_url:
        base_url, correction = normalize_base_url(raw_url)
    else:
        base_url, correction = None, None

    if request.clear_api_key:
        api_key: str | None = None
    elif request.api_key and request.api_key.strip():
        api_key = request.api_key.strip()
    else:
        api_key = current.model_api_key

    raw_name = request.model_name.strip() if request.model_name else ""
    model_name = raw_name or None
    return base_url, api_key, model_name, correction


def _effective_model_name(model_name: str | None) -> str:
    """The name a task would actually run with (see worker's fallback chain)."""
    return model_name or os.environ.get("POLISCOPE_MODEL_NAME") or DEFAULT_MODEL_NAME


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


@router.post("/model/test")
async def test_model_settings(
    request: ModelSettingsUpdate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Probe the researcher's *current form values* without saving anything.

    ``api_key`` keeps its keep-vs-replace semantics so the button works when
    the form's key field is intentionally blank (the stored key is what would
    be used). The response reports success/failure and the latency, plus
    ``corrected_base_url`` when the typed URL was rewritten -- the UI applies
    the correction so the saved value matches what was tested. The key is
    never part of any response.
    """
    repository = ModelSettingsRepository(session)
    current = await repository.get(current_user.id)
    base_url, api_key, model_name, correction = _resolve_inputs(request, current)

    if base_url is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="请先填写 Base URL 再测试连接",
        )
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="没有可测试的 API Key：请填入 Key，或留空使用已保存的 Key",
        )

    result: ProbeResult = await probe_endpoint(
        base_url=base_url,
        api_key=api_key,
        model_name=_effective_model_name(model_name),
    )
    return {
        "ok": result.ok,
        "message": result.message,
        "latency_ms": result.latency_ms,
        "corrected_base_url": base_url if correction else None,
        "correction": correction,
    }


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

    Saving is gated on a successful connectivity probe: only a configuration
    that actually answers may be stored or changed (a blank ``base_url`` means
    "back to the deployment default" and skips the gate, as does clearing the
    key -- neither introduces a new endpoint to verify).
    """
    repository = ModelSettingsRepository(session)
    current = await repository.get(current_user.id)
    base_url, api_key, model_name, _ = _resolve_inputs(request, current)

    # A key without a URL is a configuration that would never be used: task
    # creation inherits the saved settings only when both a base URL and a key
    # are present (apps/api/routers/tasks.py), so saving the half-set would
    # show "已保存 ✓" while every new task silently runs the deployment
    # default. Refuse it with the reason instead of a silent no-op. The one
    # way back to the default is clearing the key (which also drops the URL).
    if base_url is None and api_key and not request.clear_api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "有 API Key 但 Base URL 为空：任务创建时只继承同时包含"
                " Base URL 与 Key 的配置，这样保存不会作用于任何任务。"
                "请填写 Base URL；若要回到系统默认配置，请使用「清除 Key」。"
            ),
        )

    if base_url is not None and not request.clear_api_key:
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="没有 API Key 可保存：请输入 Key，或留空使用已保存的 Key",
            )
        result = await probe_endpoint(
            base_url=base_url,
            api_key=api_key,
            model_name=_effective_model_name(model_name),
        )
        if not result.ok:
            # Refuse the save with the probe's reason: a configuration that
            # fails here would fail every seat later, and the incident that
            # prompted this gate proves the researcher cannot be expected to
            # catch it by eye.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"连接测试未通过，未保存：{result.message}",
            )

    saved = await repository.save(
        current_user.id,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
    )
    await session.commit()
    return _dto(
        base_url=saved.model_base_url,
        model_name=saved.model_name,
        has_api_key=saved.has_api_key,
    )
