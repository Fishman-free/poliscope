"""Round-7 paper-review pipeline: multi-format upload, worker understanding
step, review-shaped final report.

Covers the parts that need a database: the upload endpoint's format gate,
the worker's one-shot understanding call (a fake gateway answers
``PAPER_REVIEW_UNDERSTANDING`` with a fixed summary), the process-only
ledger events it writes, and the review-shaped FINAL_PAPER_DRAFTED the
synthesizer emits for a ``paper_review`` task.
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.evidence.models import GraphNodeModel, ScientificEventModel
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelRequest,
    ModelResult,
    SchemaStatus,
)
from packages.papers.models import ObjectModel
from packages.papers.object_store import PrivateObjectStore
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED, ResearchRepository
from packages.research.service import ResearchService
from tests.factories import make_research_contract

QUESTION = "Review the uploaded paper's rigor and evidence sufficiency."


def _docx_bytes(text: str) -> bytes:
    """A minimal .docx (zip with word/document.xml -- all extract_text reads)."""
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _txt_bytes(text: str) -> bytes:
    return text.encode("utf-8")


async def _seed_review_task(
    sessions: async_sessionmaker[AsyncSession],
    object_store: PrivateObjectStore,
    *,
    content: bytes,
    filename: str,
    claim_ids: tuple[UUID, ...],
) -> tuple[UUID, UUID]:
    """Seed a QUEUED paper_review task with one uploaded object and the given
    confirmed claims. Returns (task_id, object_id)."""
    task_id = uuid4()
    object_id = uuid4()
    async with sessions() as session:
        suffix = "." + filename.rsplit(".", 1)[-1]
        content_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if suffix == ".docx"
            else "text/plain"
        )
        stored = object_store.store_named(
            f"tasks/{task_id}", content, suffix=suffix, content_type=content_type
        )
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status="QUEUED",
                created_by="paper_review_pipeline_test",
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence={"pdf_object_ids": [str(object_id)]},
                task_type="paper_review",
            )
        )
        await session.flush()
        session.add(
            ObjectModel(
                id=object_id,
                task_id=task_id,
                object_key=stored.object_key,
                content_hash=stored.content_hash,
                encryption=stored.encryption,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
                file_name=filename,
            )
        )
        for claim_id in claim_ids:
            session.add(
                AtomicClaimModel(
                    id=claim_id,
                    task_id=task_id,
                    statement="论证严谨性：审查上传论文",
                    claim_type="measurement",
                    scope={"population": "any"},
                    falsification_condition="论文存在不可修复的逻辑断裂",
                    status=CLAIM_CONFIRMED,
                    created_by="paper_review_pipeline_test",
                )
            )
        await session.commit()
    return task_id, object_id


class _ReviewGateway(ModelGateway):
    """Answers the understanding call with a fixed summary and any later
    synthesis call with a review report, by purpose."""

    async def invoke(self, request: ModelRequest) -> ModelResult:
        if request.purpose == "PAPER_REVIEW_UNDERSTANDING":
            payload: dict[str, object] = {
                "title": "Screen time and adolescent depression",
                "research_question": "Does screen time cause depression?",
                "main_claims": [
                    {
                        "statement": "Screen time correlates with depression",
                        "supporting_evidence": ["cross-sectional r=0.2"],
                        "locations": ["p.5"],
                    }
                ],
                "unverifiable": [],
            }
            assert request.output_schema == "PaperUnderstanding"
            assert request.model_class is ModelClass.MEDIUM
        else:
            payload = {
                "title": "对论文的审查报告",
                "paper_overview": {
                    "title": "Screen time and adolescent depression",
                    "research_question": "Does screen time cause depression?",
                    "main_claims": [
                        {
                            "statement": "Screen time correlates with depression",
                            "supporting_evidence": ["cross-sectional r=0.2"],
                        }
                    ],
                },
                "rigor_issues": [
                    {
                        "claim_ref": "Screen time correlates with depression",
                        "issue": "未控制混杂",
                        "severity": "high",
                    }
                ],
                "evidence_insufficiency": [
                    {
                        "claim_ref": "Screen time correlates with depression",
                        "missing_evidence": "缺少纵向证据",
                        "suggested_evidence": "队列研究",
                    }
                ],
                "improvement_suggestions": [
                    {"claim_ref": "", "issue": "补充测量偏差分析"}
                ],
                "conclusion": "论文证据不足以支持因果结论。",
                "limitations": ["本次审查基于单一上传版本。"],
                "investigation_process": ["7 席全部参与"],
            }
            assert request.output_schema == "PaperReviewReport"
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            input_tokens=10,
            output_tokens=10,
            cost_usd=Decimal("0.0010"),
            latency_ms=5,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


async def _events(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[ScientificEventModel]:
    async with sessions() as session:
        result = await session.execute(
            select(ScientificEventModel)
            .where(ScientificEventModel.task_id == task_id)
            .order_by(ScientificEventModel.sequence)
        )
        return list(result.scalars())


async def _nodes(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[GraphNodeModel]:
    async with sessions() as session:
        result = await session.execute(
            select(GraphNodeModel).where(GraphNodeModel.task_id == task_id)
        )
        return list(result.scalars())


async def _task_status(
    sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> str:
    async with sessions() as session:
        value = await session.scalar(
            select(ResearchTaskModel.status).where(
                ResearchTaskModel.task_id == task_id
            )
        )
        return str(value)


async def _submit_empty_guidance(
    sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> None:
    async with sessions() as session:
        service = ResearchService(ResearchRepository(session))
        await service.submit_council_guidance(task_id, "")
        await session.commit()


# --- upload endpoint format gate ------------------------------------------


async def test_upload_accepts_multi_format_and_records_file_name(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = (
        await api_client.post(
            "/api/tasks", json=make_research_contract().model_dump(mode="json")
        )
    ).json()
    task_id = created["task_id"]

    txt = await api_client.post(
        f"/api/tasks/{task_id}/papers/upload",
        files={"file": ("notes.txt", _txt_bytes("plain text paper"), "text/plain")},
    )
    assert txt.status_code == 201, txt.text

    docx = await api_client.post(
        f"/api/tasks/{task_id}/papers/upload",
        files={
            "file": (
                "paper.docx",
                _docx_bytes("A docx paper body"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert docx.status_code == 201, docx.text

    async with app_sessions() as session:
        rows = (
            await session.execute(
                select(ObjectModel.file_name, ObjectModel.object_key).where(
                    ObjectModel.task_id == UUID(task_id)
                )
            )
        ).all()
    names = {file_name for file_name, _ in rows}
    assert names == {"notes.txt", "paper.docx"}
    # 对象 key 带真实后缀（多格式解析的判据之一）。
    keys = {key for _, key in rows}
    assert any(key.endswith(".docx") for key in keys)


async def test_upload_refuses_legacy_office_and_disguised_files(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = (
        await api_client.post(
            "/api/tasks", json=make_research_contract().model_dump(mode="json")
        )
    ).json()
    task_id = created["task_id"]

    # Legacy .doc: 显式拒绝并提示另存（CLAUDE.md 7）。
    legacy = await api_client.post(
        f"/api/tasks/{task_id}/papers/upload",
        files={"file": ("old.doc", b"\xd0\xcf\x11\xe0 binary", "application/msword")},
    )
    assert legacy.status_code == 422
    assert "legacy office" in legacy.json()["detail"]

    # 伪装成 .pdf 的 docx 字节：magic-bytes 校验拒绝。
    disguised = await api_client.post(
        f"/api/tasks/{task_id}/papers/upload",
        files={
            "file": (
                "fake.pdf",
                _docx_bytes("not a pdf at all"),
                "application/pdf",
            )
        },
    )
    assert disguised.status_code == 422


async def test_upload_refuses_empty_and_oversized_files(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = (
        await api_client.post(
            "/api/tasks", json=make_research_contract().model_dump(mode="json")
        )
    ).json()
    task_id = created["task_id"]

    empty = await api_client.post(
        f"/api/tasks/{task_id}/papers/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert empty.status_code == 422

    from apps.api.routers.papers import MAX_UPLOAD_BYTES

    oversized = await api_client.post(
        f"/api/tasks/{task_id}/papers/upload",
        files={
            "file": (
                "big.txt",
                b"x" * (MAX_UPLOAD_BYTES + 1),
                "text/plain",
            )
        },
    )
    assert oversized.status_code == 422


async def test_paper_review_confirm_requires_an_upload(
    api_client: httpx.AsyncClient,
) -> None:
    contract = make_research_contract()
    payload = contract.model_dump(mode="json")
    payload["task_type"] = "paper_review"
    created = (
        await api_client.post("/api/tasks", json=payload)
    ).json()
    assert created["task_id"]

    response = await api_client.post(
        f"/api/tasks/{created['task_id']}/confirm-claims",
        json={"claim_ids": [created["suggested_claims"][0]["id"]]},
    )
    assert response.status_code == 422
    assert "必须至少上传一篇论文" in response.json()["detail"]


# --- worker understanding step + review report -----------------------------


async def test_paper_review_full_run_writes_understanding_and_review_report(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Any,
) -> None:
    object_store = PrivateObjectStore(root=str(tmp_path))
    claim_ids = (uuid4(), uuid4())
    task_id, _ = await _seed_review_task(
        app_sessions,
        object_store,
        content=_docx_bytes(
            "Screen time and adolescent depression: a cross-sectional study."
        ),
        filename="paper.docx",
        claim_ids=claim_ids,
    )
    gateway = _ReviewGateway()

    first = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        object_store=object_store,
    )
    assert first.run.final_status == "AWAITING_COUNCIL_INPUT"
    await _submit_empty_guidance(app_sessions, task_id)
    second = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        object_store=object_store,
    )
    assert second.run.final_status in ("COMPLETED", "COMPLETED_WITH_GAPS")

    events = await _events(app_sessions, task_id)
    captured = [
        event
        for event in events
        if event.event_type == "PAPER_UNDERSTANDING_CAPTURED"
    ]
    assert len(captured) == 1
    payload = dict(captured[0].payload)
    assert payload["research_question"] == "Does screen time cause depression?"
    main_claims = payload["main_claims"]
    assert isinstance(main_claims, list)
    assert isinstance(main_claims[0], dict)
    assert main_claims[0]["statement"].startswith("Screen time")

    # process_only：理解事件不进证据图（CLAUDE.md 5.1/5.3）。
    nodes = await _nodes(app_sessions, task_id)
    assert all(
        node.node_type != "PAPER_UNDERSTANDING_CAPTURED" for node in nodes
    )

    drafted = [
        event
        for event in events
        if event.event_type == "FINAL_PAPER_DRAFTED"
    ]
    assert len(drafted) == 1
    report = dict(drafted[0].payload)
    assert "paper_overview" in report
    overview = report["paper_overview"]
    assert isinstance(overview, dict)
    assert overview["research_question"] == "Does screen time cause depression?"
    rigor = report["rigor_issues"]
    assert isinstance(rigor, list)
    assert isinstance(rigor[0], dict)
    assert rigor[0]["issue"] == "未控制混杂"

    # 终态与普通任务一致：review 不改变任务终态语义。
    assert await _task_status(app_sessions, task_id) in (
        "COMPLETED",
        "COMPLETED_WITH_GAPS",
    )


async def test_paper_review_resume_does_not_rerun_understanding(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Any,
) -> None:
    """The understanding step's idempotency key is stable: a resumed pass
    reads the captured event back and pays for the call exactly once."""
    object_store = PrivateObjectStore(root=str(tmp_path))
    task_id, _ = await _seed_review_task(
        app_sessions,
        object_store,
        content=_txt_bytes("A plain text paper for the review task."),
        filename="paper.txt",
        claim_ids=(uuid4(), uuid4()),
    )
    gateway = _ReviewGateway()

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        object_store=object_store,
    )
    await _submit_empty_guidance(app_sessions, task_id)
    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        object_store=object_store,
    )

    events = await _events(app_sessions, task_id)
    captured = [
        event
        for event in events
        if event.event_type == "PAPER_UNDERSTANDING_CAPTURED"
    ]
    assert len(captured) == 1
