"""Task lifecycle endpoints.

Creating a task never starts research. The task waits in
AWAITING_CLAIM_CONFIRMATION until the researcher confirms which atomic claims
the council will investigate, which is the control point CLAUDE.md 2 requires.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from apps.api import task_lifecycle
from apps.api.dependencies import (
    CurrentUserDep,
    ObjectStoreDep,
    SessionDep,
)
from apps.api.routers.workspace import _seats
from apps.api.schemas import (
    ConfirmClaimsRequest,
    CouncilGuidanceRequest,
    CreateTaskRequest,
    FollowUpRequest,
    ReResearchRequest,
)
from packages.accounts.repository import StoredUser
from packages.epistemo.contracts import TaskStatus
from packages.knowledge.repository import KnowledgeBaseNotFound, KnowledgeRepository
from packages.models.endpoint_config import normalize_base_url
from packages.models.free_trial import (
    FREE_TRIAL_EXHAUSTED_MESSAGE,
    FREE_TRIAL_EXTRA_BODY,
    FREE_TRIAL_LIMIT,
)
from packages.models.settings import ModelSettingsRepository, StoredModelSettings
from packages.papers.object_store import PrivateObjectStore
from packages.papers.understanding import load_paper_text, load_paper_understanding
from packages.reports.json_export import to_dict
from packages.reports.service import ReportService
from packages.research.contracts import ResearchContract, TaskModelConfig
from packages.research.language import detect_output_language
from packages.research.repository import ResearchRepository, StoredTask, TaskNotFound
from packages.research.service import (
    InvalidCouncilGuidanceState,
    InvalidPauseState,
    ResearchService,
    UnconfirmedClaims,
)
from packages.skills.repository import SkillsRepository

router = APIRouter()

TASK_NOT_FOUND = "unknown task"

# Round-14 session deletion: how long to wait for a RUNNING task's worker to
# stop and release its row lock. A live worker polls the cancel channel every
# ~1s and halts within seconds, so this budget covers that comfortably plus a
# dead worker's lock being released by PostgreSQL when its connection drops.
DELETE_CANCEL_WAIT_SECONDS = 60.0
DELETE_POLL_SECONDS = 2.0


def _service(session: SessionDep) -> ResearchService:
    return ResearchService(ResearchRepository(session))


def _effective_model_config(
    task_config: dict[str, Any] | None,
    saved: StoredModelSettings,
) -> dict[str, Any]:
    """The model endpoint this task would actually run with, for display.

    Round-6 report: a researcher saved an API key and model name, then saw
    tasks still run the deployment default and could not tell whether their
    settings had ever been applied. This resolves the same inheritance
    create_task applies -- explicit per-task config wins, then the account's
    saved settings (only when both URL and key are present), else the
    deployment default -- so the UI can say which one a task uses. The key
    itself never leaves the server (CLAUDE.md 16), only its presence.
    """
    explicit = task_config or {}
    if explicit.get("base_url") and explicit.get("api_key"):
        return {
            "source": "saved",
            "base_url": explicit["base_url"],
            "model_name": explicit.get("model_name"),
            "has_api_key": True,
        }
    if saved.model_base_url and saved.has_api_key:
        return {
            "source": "saved",
            "base_url": saved.model_base_url,
            "model_name": saved.model_name,
            "has_api_key": True,
        }
    return {
        "source": "default",
        "base_url": None,
        "model_name": None,
        "has_api_key": False,
    }


def _normalized_model_config(config: dict[str, object]) -> dict[str, object]:
    """Normalise the endpoint before it is stored on the task.

    User input is never stored verbatim (see packages/models/endpoint_config.py
    for the incident that proved it): the base_url gets its scheme, trailing
    slash, and any console-portal rewrite applied so the worker later builds a
    working gateway. ``TaskModelConfig`` also rejects scheme-less values, so
    normalising first means a bare ``platform.deepseek.com`` becomes a valid
    ``https://api.deepseek.com`` instead of a 422.
    """
    raw_url = config.get("base_url")
    if isinstance(raw_url, str) and raw_url.strip():
        normalized, _ = normalize_base_url(raw_url)
        config = {**config, "base_url": normalized}
    return config


def _not_found(task_id: UUID, error: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{TASK_NOT_FOUND} {task_id}",
    )


async def _owned_task(
    session: SessionDep, task_id: UUID, user: StoredUser
) -> StoredTask:
    """Fetch a task scoped to the caller; someone else's task is a 404.

    The repository treats an unowned or other-owned task as TaskNotFound, so
    the API answers 404 for both "no such task" and "not your task" -- the
    existence of another account's research must not leak.
    """
    try:
        return await ResearchRepository(session).get_task(task_id, user.id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error


@router.get("")
@router.get("/", include_in_schema=False)
async def list_tasks(
    session: SessionDep,
    current_user: CurrentUserDep,
) -> list[dict[str, Any]]:
    """The caller's sessions, newest first, for the web session-history panel.

    The panel replaces the old "paste a task id" box: the researcher's whole
    history is one click away. Scoped to the calling account -- another
    account's sessions are invisible, and pre-account rows belong to no one.
    Summaries only -- no claims, no evidence. The model endpoint each task
    will use is resolved once for the whole list (the account's saved settings
    are the same for every task).
    """
    tasks = await ResearchRepository(session).list_tasks(current_user.id)
    saved = await ModelSettingsRepository(session).get(current_user.id)
    return [
        {
            "task_id": str(task.task_id),
            "question": task.question,
            "status": task.status,
            "created_by": task.created_by,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "task_type": task.task_type,
            "effective_model_config": _effective_model_config(
                task.model_config, saved
            ),
        }
        for task in tasks
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_task(
    request: CreateTaskRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Create a task (owned by the caller) and return its suggested claims."""
    if request.knowledge_base_id is not None:
        # The contract cannot validate this itself (cross-table), so the
        # router does: a task linked to a knowledge base that does not exist
        # would silently lose the researcher's documents at worker time.
        # Scoped to the caller -- another account's base is "unknown".
        try:
            await KnowledgeRepository(session).get_knowledge_base(
                request.knowledge_base_id, current_user.id
            )
        except KnowledgeBaseNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown knowledge base {request.knowledge_base_id}",
            ) from error
    if request.skill_ids:
        # A task may only enable skills the caller actually owns -- another
        # account's skill id would leak its name into this task's prompts.
        repository = SkillsRepository(session)
        for skill_id in request.skill_ids:
            if await repository.get_for_user(current_user.id, skill_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"unknown skill {skill_id}",
                )
    task_model_config: dict[str, object] | None = (
        dict(request.task_model_config) if request.task_model_config else None
    )
    if task_model_config is None:
        # Permanent model settings: a task that carries no explicit per-task
        # config inherits the account's saved endpoint, so the researcher
        # sets it once on the right-hand settings panel (or via the CLI) and
        # every new task uses it. An explicit per-task config always wins.
        saved = await ModelSettingsRepository(session).get(current_user.id)
        if saved.model_base_url and saved.has_api_key:
            task_model_config = {
                "base_url": saved.model_base_url,
                "api_key": saved.model_api_key,
                "model_name": saved.model_name,
            }
            if saved.is_free_trial:
                # Free-trial marker: the task's own config must remember it
                # came from the trial (confirm-claims consumes a quota slot
                # on this flag), and the vendor's request fields must reach
                # the worker's gateway. The slot is consumed when the task
                # actually starts, so a trial task that is created but never
                # confirmed costs nothing.
                task_model_config["is_free_trial"] = True
                task_model_config["extra_body"] = dict(FREE_TRIAL_EXTRA_BODY)
                if saved.free_trial_used >= FREE_TRIAL_LIMIT:
                    # The first gate: refuse at the moment of asking, before
                    # any draft exists. The second gate is confirm-claims'
                    # atomic consume, which stays authoritative under
                    # concurrency.
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=FREE_TRIAL_EXHAUSTED_MESSAGE,
                    )
    if task_model_config is not None:
        task_model_config = _normalized_model_config(task_model_config)
    # Output language follows the language the researcher asked in: "auto"
    # (the default) is resolved here from the question so the stored row
    # always carries a concrete language and the worker never has to guess.
    # An explicit value from the client (round-4 language switching) wins.
    if request.output_language in (None, "", "auto"):
        output_language = detect_output_language(request.question)
    else:
        output_language = request.output_language
    try:
        contract = ResearchContract.model_validate(
            {
                "question": request.question,
                "scope": dict(request.scope),
                "budget": dict(request.budget),
                "user_evidence": dict(request.user_evidence),
                "task_model_config": task_model_config,
                "knowledge_base_id": request.knowledge_base_id,
                "skill_ids": tuple(request.skill_ids),
                "output_language": output_language,
                "task_type": request.task_type,
            }
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    created = await _service(session).create(
        contract, created_by=current_user.username, user_id=current_user.id
    )
    return {
        "task_id": str(created.task_id),
        "status": created.status,
        "suggested_claims": [
            {
                "id": str(claim.claim_id),
                "statement": claim.statement,
                "claim_type": claim.claim_type,
                "falsification_condition": claim.falsification_condition,
            }
            for claim in created.suggested_claims
        ],
    }


@router.post("/{task_id}/confirm-claims")
async def confirm_claims(
    task_id: UUID,
    request: ConfirmClaimsRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Confirm the claims to investigate, then queue the task."""
    task = await _owned_task(session, task_id, current_user)
    service = _service(session)
    # A task can be confirmed exactly once: a second confirm would re-queue an
    # already-queued task and -- for a free-trial task -- burn a second quota
    # slot on the same research. Refuse rather than double-charge.
    if task.status != "AWAITING_CLAIM_CONFIRMATION":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"task is {task.status}, not awaiting claim confirmation",
        )
    # A paper-review task's whole subject is the uploaded paper: confirming
    # without one would hand the council an empty critique (the worker's
    # understanding step has nothing to read). Refuse up front with the
    # reason rather than let the run degrade silently (CLAUDE.md 7).
    if task.task_type == "paper_review":
        pdf_object_ids = (task.user_evidence or {}).get("pdf_object_ids") or ()
        if not pdf_object_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="论文审查任务必须至少上传一篇论文，请先上传后再确认。",
            )
    # Free-trial quota (round-7): a task whose inherited config carries the
    # free-trial marker consumes one of the account's two slots the moment
    # the research actually starts -- a draft that is never confirmed costs
    # nothing, and a task the researcher later switched to their own
    # endpoint (task-level explicit config) is not trial-flagged at all.
    # The consume is atomic (UPDATE ... WHERE used < limit RETURNING), so
    # two concurrent confirmations cannot both take the last slot, and it
    # shares this transaction with confirm+queue: any failure rolls the
    # slot back.
    task_config = task.model_config or {}
    if task_config.get("is_free_trial") is True:
        consumed = await ModelSettingsRepository(session).consume_free_trial(
            current_user.id, FREE_TRIAL_LIMIT
        )
        if not consumed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=FREE_TRIAL_EXHAUSTED_MESSAGE,
            )
    try:
        claims = await service.confirm_claims(task_id, request.claim_ids)
        task_status = await service.queue(task_id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except UnconfirmedClaims as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return {
        "task_id": str(task_id),
        "status": task_status,
        # Discarded claims are returned too. CLAUDE.md 5.3 forbids removing what
        # the council once considered, so the caller can see what was set aside.
        "claims": [
            {"id": str(claim.claim_id), "status": claim.status} for claim in claims
        ],
    }


def _followup_endpoint(
    task: StoredTask,
) -> tuple[str, str, str] | None:
    """The (base_url, api_key, model_name) the task actually ran with.

    The follow-up must be answered by the same model that produced the
    research (same endpoint, same key, same model name) so it shares the
    task's frame of reference -- asking a *different* model about the run
    would give a stranger's opinion. ``None`` when the task ran the
    deployment default (no per-task config).
    """
    config = task.model_config
    if not config or not config.get("base_url") or not config.get("api_key"):
        return None
    parsed = TaskModelConfig.model_validate(dict(config))
    model_name = (
        parsed.model_name
        or os.environ.get("POLISCOPE_MODEL_NAME")
        or "deepseek-v4-flash"
    )
    base_url, _ = normalize_base_url(parsed.base_url)
    return base_url, parsed.api_key, model_name


def _followup_context(brief: dict[str, object]) -> str:
    """Render the Research Brief into the follow-up's grounding context.

    The model is told what the council actually concluded -- confirmed claims,
    admitted findings, blindspots, dissents, limitations, absent seats -- so
    an answer stays grounded in the run rather than being a fresh opinion
    (CLAUDE.md 2: evidence over fluent prose).
    """
    lines: list[str] = []
    claims = brief.get("confirmed_claims")
    if isinstance(claims, list):
        lines.append("### 已确认的原子主张")
        for claim in claims:
            if isinstance(claim, dict):
                lines.append(f"- {claim.get('statement')}（{claim.get('claim_type')}）")
    findings = brief.get("findings")
    if isinstance(findings, list):
        lines.append("### 已采纳发现")
        for finding in findings:
            if isinstance(finding, dict):
                payload = finding.get("payload")
                text = payload.get("statement") if isinstance(payload, dict) else None
                if text:
                    lines.append(f"- {text}")
    for label, key in (
        ("盲点", "blindspots"),
        ("少数异议", "dissents"),
        ("局限", "limitations"),
    ):
        items = brief.get(key)
        if isinstance(items, list) and items:
            lines.append(f"### {label}")
            for item in items:
                if isinstance(item, dict):
                    text = item.get("statement") or item.get("payload")
                    if isinstance(text, dict):
                        text = text.get("statement")
                    if text:
                        lines.append(f"- {text}")
    absent = brief.get("absent_seats")
    if isinstance(absent, list) and absent:
        lines.append(f"### 缺席席位\n{', '.join(map(str, absent))}")
    if not lines:
        return "（该任务尚无已采纳的结论材料。）"
    return "\n".join(lines)


async def _followup_paper_context(
    session: SessionDep,
    object_store: PrivateObjectStore,
    task: StoredTask,
) -> str:
    """The uploaded paper's understanding and full text, for paper-review follow-ups.

    Round-10 report: a researcher asked a paper-review task "does the paper's
    sample support its conclusion?" and the follow-up model answered "I cannot
    see the paper" -- the follow-up context carried only the Research Brief
    (claims, findings, blindspots) and never the paper itself. A follow-up
    about an uploaded paper must be grounded in that paper, so this injects
    the machine's reading (the same ``PAPER_UNDERSTANDING_CAPTURED`` event the
    council used) plus the extracted text, explicitly labelled as the uploaded
    paper, never as the council's verdict.
    """
    if task.task_type != "paper_review":
        return ""
    object_ids = (task.user_evidence or {}).get("pdf_object_ids") or ()
    if not object_ids:
        return "（论文审查任务，但未找到已上传论文的对象记录。）"
    understanding = await load_paper_understanding(session, task.task_id)
    paper_text, truncated, error = await load_paper_text(
        session, object_store, [UUID(str(oid)) for oid in object_ids]
    )
    lines: list[str] = ["### 研究者上传的论文全文（Level A 证据）"]
    if understanding:
        title = understanding.get("title")
        if isinstance(title, str) and title:
            lines.append(f"标题：{title}")
        research_question = understanding.get("research_question")
        if isinstance(research_question, str) and research_question:
            lines.append(f"论文研究问题：{research_question}")
        main_claims = understanding.get("main_claims")
        if isinstance(main_claims, (list, tuple)):
            lines.append("论文主要观点（机器摘要）：")
            for claim in main_claims:
                # Mapping, not dict: the event payload is JSONB (plain dict on
                # read), but a checkpoint-resumed path can hand back FrozenDict.
                if isinstance(claim, Mapping):
                    statement = claim.get("statement", "?")
                    support = claim.get("supporting_evidence")
                    if isinstance(support, (list, tuple)) and support:
                        lines.append(
                            f"- {statement}（论文佐证：{'；'.join(map(str, support))}）"
                        )
                    else:
                        lines.append(f"- {statement}（论文未提供可辨识佐证）")
    if error:
        lines.append(f"（论文全文未能提取：{error}）")
    elif paper_text:
        lines.append(f"## 论文文本\n\n{paper_text[:MAX_PAPER_FOLLOWUP_CHARS]}")
        if truncated:
            lines.append("\n（论文过长，此处仅引用前一部分。）")
    return "\n".join(lines)


async def _prepare_followup(
    task_id: UUID,
    request: FollowUpRequest,
    session: SessionDep,
    current_user: StoredUser,
    object_store: PrivateObjectStore,
) -> tuple[str, str, str, str, str] | None:
    """Validate a follow-up and build its prompt; ``None`` when no model gateway.

    Shared by the plain and streaming follow-up endpoints so the two cannot
    drift apart on what grounds the answer (round-10: the streaming path must
    carry the same paper context the non-streaming one does).
    """
    question = request.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="补充提问不能为空",
        )
    if len(question) > 2000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="补充提问不能超过 2000 字",
        )
    task = await _owned_task(session, task_id, current_user)
    if task.status not in ("COMPLETED", "COMPLETED_WITH_GAPS"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"任务尚未完成（当前状态 {task.status}），完成后方可补充提问",
        )

    endpoint = _followup_endpoint(task)
    if endpoint is None:
        # Task ran the deployment default: fall back to the deployment's
        # configured gateway if present, else the honest "no model configured"
        # answer (CLAUDE.md 7: admit the gap, never improvise).
        env = os.environ
        if env.get("POLISCOPE_MODEL_API_KEY") and env.get("POLISCOPE_MODEL_BASE_URL"):
            endpoint = (
                env["POLISCOPE_MODEL_BASE_URL"].rstrip("/"),
                env["POLISCOPE_MODEL_API_KEY"],
                env.get("POLISCOPE_MODEL_NAME") or "deepseek-v4-flash",
            )
        else:
            return None

    base_url, api_key, model_name = endpoint
    brief = to_dict(await ReportService(session).build(task_id))
    context = _followup_context(brief)
    # For a paper-review task, ground the answer in the paper itself too --
    # round-10 report: without it the follow-up model answers "I cannot see
    # the uploaded paper" to any question about the paper's content.
    paper_context = await _followup_paper_context(
        session, object_store, task
    )
    language = detect_output_language(task.question)

    system_prompt = (
        "你是七人议会研究成果的讲解员。研究者针对已完成的议会研究向你追问，"
        "你必须基于下方提供的议会产出（已确认主张、已采纳发现、盲点、异议、局限）回答，"
        "不得编造研究中不存在的来源、数字或结论。若研究本身有缺口（缺席、未执行阶段），"
        "如实说明，不要假装完整。回答用"
        + ("中文。" if language.startswith("zh") else "English.")
    )
    user_prompt = (
        f"研究问题：{task.question}\n\n"
        f"议会产出：\n{context}\n\n"
        f"{paper_context}\n\n"
        f"研究者的追问：{question}\n\n"
        "请给出清晰、准确的回答。"
    )
    return base_url, api_key, model_name, system_prompt, user_prompt


@router.post("/{task_id}/followup")
async def follow_up(
    task_id: UUID,
    request: FollowUpRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
    object_store: ObjectStoreDep,
) -> dict[str, Any]:
    """Answer a post-completion question about a finished task (round-9).

    Only a terminal task may be asked: the researcher must see the integrated
    conclusion first, and a running task has no settled state to answer from.
    The answer is grounded in the Research Brief (confirmed claims, findings,
    blindspots, dissents, limitations) -- and, for a paper-review task, the
    uploaded paper itself (round-10) -- and threaded to the same model that
    ran the research. Never the API key, never a fresh-model opinion.
    """
    prepared = await _prepare_followup(
        task_id, request, session, current_user, object_store
    )
    if prepared is None:
        return {
            "answer": (
                "此任务运行的系统默认模型网关未配置，无法回答补充提问。"
                "请在右侧模型设置中配置模型后重试。"
            ),
            "available": False,
        }
    base_url, api_key, model_name, system_prompt, user_prompt = prepared

    request_body = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 1500,
    }
    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=60.0,
            headers={"Authorization": f"Bearer {api_key}"},
        ) as client:
            response = await client.post("/chat/completions", json=request_body)
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as error:
        detail = _followup_error_detail(error.response)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"模型调用失败（HTTP {error.response.status_code}）：{detail}",
        ) from error
    except (httpx.HTTPError, KeyError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"模型调用失败：{error}",
        ) from error

    return {"answer": answer, "available": True}


@router.post("/{task_id}/followup/stream")
async def follow_up_stream(
    task_id: UUID,
    request: FollowUpRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
    object_store: ObjectStoreDep,
    http_request: Request,
) -> StreamingResponse:
    """Stream a follow-up answer as SSE (round-10).

    Same grounding as the plain endpoint (Research Brief + the uploaded paper
    for a paper-review task), delivered as deltas so a long answer renders as
    it is produced instead of after the fact. The stream is SSE with a final
    ``[DONE]`` frame (and an ``error:`` frame when the vendor fails mid-way),
    consumed by ``followUpStream`` on the client; a client that disconnects
    cancels the upstream httpx stream.
    """
    prepared = await _prepare_followup(
        task_id, request, session, current_user, object_store
    )
    if prepared is None:
        error_frame = (
            "event: error\ndata: "
            + json.dumps(
                {"detail": "模型网关未配置，无法回答补充提问。"}, ensure_ascii=False
            )
            + "\n\n"
        )
        return StreamingResponse(
            iter([error_frame]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    base_url, api_key, model_name, system_prompt, user_prompt = prepared

    async def _event_stream() -> AsyncIterator[str]:
        request_body = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1500,
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=120.0,
                headers={"Authorization": f"Bearer {api_key}"},
            ) as client, client.stream(
                "POST", "/chat/completions", json=request_body
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if await http_request.is_disconnected():
                        return
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        frame = json.dumps({"text": text}, ensure_ascii=False)
                        yield f"data: {frame}\n\n"
            yield "data: [DONE]\n\n"
        except httpx.HTTPStatusError as error:
            detail = _followup_error_detail(error.response)
            message = (
                f"模型调用失败（HTTP {error.response.status_code}）：{detail}"
            )
            frame = json.dumps({"detail": message}, ensure_ascii=False)
            yield f"event: error\ndata: {frame}\n\n"
        except (httpx.HTTPError, ValueError) as error:
            message = f"模型调用失败：{error}"
            frame = json.dumps({"detail": message}, ensure_ascii=False)
            yield f"event: error\ndata: {frame}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Without this a buffering reverse proxy holds the whole response
            # and the stream never reaches the browser.
            "X-Accel-Buffering": "no",
        },
    )


# How much of an uploaded paper's extracted text a follow-up question is
# grounded in. The full paper can run far longer than a follow-up answer
# needs; the context labels the truncation explicitly rather than pretending
# the whole text was seen (CLAUDE.md 7).
MAX_PAPER_FOLLOWUP_CHARS = 40_000


def _followup_error_detail(response: httpx.Response) -> str:
    """Extract the vendor's error message for the follow-up endpoint."""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    message = str(error.get("message", "")) if isinstance(error, dict) else str(error)
    return message.strip()[:200]


@router.post("/{task_id}/pause")
async def pause_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Keep a queued task from being claimed until it is resumed.

    Only a QUEUED task can be paused: a task already running finishes its one
    uncommitted phase sequence regardless (see ResearchService.pause), and a
    task still awaiting claim confirmation was never going to be claimed in the
    first place.
    """
    await _owned_task(session, task_id, current_user)
    try:
        new_status = await _service(session).pause(task_id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except InvalidPauseState as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {"task_id": str(task_id), "status": new_status}


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Move a paused task back to QUEUED so a worker can claim it again."""
    await _owned_task(session, task_id, current_user)
    try:
        new_status = await _service(session).resume(task_id)
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except InvalidPauseState as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {"task_id": str(task_id), "status": new_status}


@router.post("/{task_id}/re-research")
async def re_research_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    body: ReResearchRequest | None = None,
) -> dict[str, Any]:
    """Move a FAILED (or CANCELLED) task back to QUEUED for another run.

    「重新研究」(round-8): the worker resumes from the stored council
    checkpoint when one exists (already-run phases are not re-run, and their
    ledger events replay as no-ops via stable idempotency keys); a task that
    failed before reaching the checkpoint re-runs its early phases -- an
    honest restart rather than pretending nothing happened. Round-10: a
    researcher-stopped task is re-runnable the same way.

    Round-12 「重新研究模式」: an optional ``body.mode`` of ``full``, or
    ``first_gap`` (the default when the body is omitted) with no recorded
    gap, cannot re-run *this* task -- its committed ledger events would
    collide with the re-run's (same idempotency key, different payload) and
    fail it (round-13 production failure). Both now create a **fresh task**
    and return its id in ``task_id``; ``first_gap`` with a recorded gap
    rewinds this task to the first unfinished phase and returns the original
    id. Identical semantics for deep-research and paper-review tasks.
    """
    await _owned_task(session, task_id, current_user)
    try:
        new_status, effective_id = await _service(session).re_research(
            task_id, mode=body.mode if body is not None else "first_gap"
        )
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except InvalidPauseState as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {"task_id": effective_id, "status": new_status}


@router.post("/{task_id}/rerun-fresh")
async def rerun_fresh_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """「从头研究」(round-13): start a genuinely fresh task from PRECOMMITMENT.

    Re-running the *same* task from the start cannot be a true restart: the
    ledger's idempotency keys derive from phase and seat, so the previous
    run's events would swallow the new pass's as no-ops -- the council would
    be re-polled but the researcher would still see the old round's evidence.
    This endpoint creates a brand-new task (fresh ledger, fresh evidence
    graph, fresh process stream) carrying over the question, scope, budget,
    confirmed atomic claims, model configuration, and uploaded paper
    references. The original task stays untouched as audit history. The fresh
    task is queued immediately -- the researcher already confirmed these
    claims once, and the result is its own new task id the client opens.
    """
    await _owned_task(session, task_id, current_user)
    try:
        fresh_id = await _service(session).rerun_fresh(
            task_id,
            created_by=current_user.username,
            user_id=current_user.id,
        )
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except (InvalidPauseState, UnconfirmedClaims) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {
        "task_id": str(fresh_id),
        "status": TaskStatus.QUEUED,
        "source_task_id": str(task_id),
    }


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Stop a running or queued task (round-10 「停止研究」).

    A QUEUED/PAUSED task flips straight to CANCELLED; a RUNNING task's stop is
    recorded in ``task_cancel_requests`` and the worker halts it between
    phases. The researcher must never be told "I stopped it" when the worker
    might still be mid-run, so the endpoint returns the status the task *will*
    reach, and the UI refreshes from the stream/snapshot.
    """
    await _owned_task(session, task_id, current_user)
    try:
        new_status = await _service(session).cancel(
            task_id, requested_by=current_user.username
        )
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    return {"task_id": str(task_id), "status": new_status}


@router.get("/{task_id}/council-preview")
async def council_preview(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Show the 7 seats' BLINDSPOT_BOUNTY-end positions while a task is halted.

    Plan phase 8.2. Read-only, and built from ``_seats()`` -- the exact
    per-seat aggregation the workspace panel already uses -- rather than a
    second implementation, so this view can never drift from what the
    council workspace shows for the same events.
    """
    task = await _owned_task(session, task_id, current_user)
    return {
        "task_id": str(task_id),
        "status": task.status,
        "seats": [dict(seat) for seat in await _seats(session, task_id)],
    }


@router.post("/{task_id}/council-guidance")
async def council_guidance(
    task_id: UUID,
    request: CouncilGuidanceRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Attach the human's advisory steer and let the worker resume the council.

    Plan phase 8.2/8.3. Only valid while the task is AWAITING_COUNCIL_INPUT;
    an empty ``guidance_text`` is a deliberate, honest "no intervention" --
    CLAUDE.md 4/8 forbid this from ever being a vote that decides scientific
    truth, so declining to steer is as valid an answer as steering.
    """
    await _owned_task(session, task_id, current_user)
    try:
        new_status = await _service(session).submit_council_guidance(
            task_id, request.guidance_text
        )
    except TaskNotFound as error:
        raise _not_found(task_id, error) from error
    except InvalidCouncilGuidanceState as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {"task_id": str(task_id), "status": new_status}


@router.delete("/{task_id}")
async def delete_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Permanently delete a task and every record that belongs to it.

    Session-history management (round-6): a researcher may discard a whole
    session -- queued clutter, a mistake, an obsolete run -- claims, ledger
    events, process stream, graph, audit rows and the task row itself go with
    it. Deletion is physical and irreversible, so the frontend confirms
    before calling; nothing here resurrects anything (CLAUDE.md 5.3's
    no-physical-delete rule governs quarantined *evidence*, not a researcher
    destroying their own session).

    The cascade lives in apps/api/task_lifecycle.py, shared with account
    deletion: no single module owns the task's lifecycle (CLAUDE.md 9).

    Round-14: a RUNNING task's worker holds the task row ``FOR UPDATE`` for
    its whole run, so deleting straight away would block on that lock
    indefinitely (PostgreSQL's default lock_timeout is unbounded, and the
    browser faithfully spins). The endpoint first asks the worker to stop via
    the side channel it polls every ~1s, waits (bounded) for the status to
    leave RUNNING, then cascades. A dead or wedged worker still cannot hang
    the request: the cascade's own lock_timeout turns any residual lock into
    an explicit 409 instead of a silent 500.
    """
    stored = await _owned_task(session, task_id, current_user)
    repository = ResearchRepository(session)
    try:
        if stored.status == TaskStatus.RUNNING:
            # Round-14: bound *every* lock wait in this delete, including the
            # cancel-request INSERT's FK check on the task row the worker
            # holds FOR UPDATE -- that check waits for the worker's lock and
            # is the first thing the request hits, so an unbounded wait there
            # wedges the endpoint before the cascade's own timeout ever runs.
            # The commit below ends this transaction, so delete_task_cascade
            # re-applies the timeout for its own transaction.
            await session.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{task_lifecycle.DELETE_LOCK_TIMEOUT}'"
                )
            )
            await repository.request_cancel(
                task_id, requested_by=current_user.username
            )
            # The worker's poll must see the stop request: commit our write so
            # its next transaction reads it.
            await session.commit()
            deadline = time.monotonic() + DELETE_CANCEL_WAIT_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(DELETE_POLL_SECONDS)
                status_value = await repository.get_status(task_id)
                if status_value is None or status_value != TaskStatus.RUNNING:
                    break
            # Budget exhausted: still try to delete. A dead worker's lock is
            # gone (its connection dropped, transaction rolled back), so the
            # delete succeeds; a genuinely wedged worker hits the cascade's
            # lock_timeout.
        await task_lifecycle.delete_task_cascade(session, task_id)
        await session.commit()
    # Catch the DBAPIError base: PostgreSQL's lock_timeout surfaces as
    # asyncpg LockNotAvailableError (SQLSTATE 55P03), which SQLAlchemy wraps
    # as a generic DBAPIError -- not OperationalError. Any other database
    # error still re-raises untouched.
    except DBAPIError as error:
        message = str(error).lower()
        if "lock timeout" in message or "deadlock" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "该任务仍被运行中的 worker 占用，暂时无法删除，"
                    "请稍后重试（或先停止研究）。"
                ),
            ) from error
        raise
    return {"deleted": str(task_id)}


@router.get("/{task_id}")
async def get_task(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    task = await _owned_task(session, task_id, current_user)
    saved = await ModelSettingsRepository(session).get(current_user.id)
    return {
        "task_id": str(task.task_id),
        "question": task.question,
        "status": task.status,
        "created_by": task.created_by,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "effective_model_config": _effective_model_config(
            task.model_config, saved
        ),
    }
