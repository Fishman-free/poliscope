"""The synthesised final paper, end to end.

Covers the round-5 paper pipeline: ``synthesize_paper`` runs after a terminal
deliberation and writes FINAL_PAPER_DRAFTED / FINAL_PAPER_FAILED ledger events
(process-only, never graph nodes, never touching the task's terminal status);
the report endpoint serves the paper or an honest stub; the workspace
snapshot carries ``paper`` and ``consensus``.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.council.contracts import Seat
from packages.council.rounds.registry import PhaseContext
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.evidence.models import GraphNodeModel, ScientificEventModel
from packages.evidence.sql_ledger import SqlEventLedger
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import (
    ModelGateway,
    ModelRequest,
    ModelResult,
    SchemaStatus,
)
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED, ResearchRepository
from packages.research.service import ResearchService

QUESTION = "Does adolescent social media use cause depressive symptoms?"

MAP_PAYLOAD: dict[str, object] = {
    "bottom_line": "重度使用与抑郁症状呈弱相关，但因果方向未解决。",
    "verdicts": [
        {
            "claim": "重度使用预测更高的抑郁症状得分",
            "verdict": "partially_supported",
            "reason": "方向一致但效应很小。",
            "refs": ["J1"],
        }
    ],
    "steps": [
        {
            "dimension": "causality",
            "question": "能否从关联推到因果？",
            "answer": "混杂与反向因果仍是主要威胁。",
            "support": ["相关性一致。"],
            "limits": ["仅一项纵向研究。"],
            "unknowns": ["反向因果未被排除。"],
            "resolve": "预注册的纵向队列研究。",
            "refs": ["J1", "NOT-AN-ID"],
        },
        {
            "dimension": "evidence",
            "question": "证据从哪来？",
            "answer": "证据来自少量队列研究。",
        },
    ],
}
EXPECTED_TITLE = f"关于「{QUESTION}」的研究结论"


async def _seed_queued_task(
    sessions: async_sessionmaker[AsyncSession],
    user_id: UUID,
) -> UUID:
    task_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="paper_pipeline_test",
                user_id=user_id,
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence={},
            )
        )
        await session.flush()
        session.add(
            AtomicClaimModel(
                id=uuid4(),
                task_id=task_id,
                statement="Heavy use predicts higher depressive symptom scores.",
                claim_type="correlational",
                scope={"population": "adolescents"},
                falsification_condition="A preregistered cohort finds a null effect.",
                status=CLAIM_CONFIRMED,
                created_by="paper_pipeline_test",
            )
        )
        await session.commit()
    return task_id


class _ScriptedDeliberator:
    """Stands in for the model layer with fixed, replay-stable outputs."""

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        if phase is TaskPhase.PRECOMMITMENT:
            return {
                "initial_judgment": f"{seat.value} sees weak correlational support",
                "confidence": 0.4,
                "update_condition": "a preregistered cohort study",
            }
        if phase is TaskPhase.ACQUISITION:
            return {"requests": [f"cohort studies for {seat.value}"]}
        return None


class _PaperGateway:
    """ModelGateway that answers FINAL_SYNTHESIS with a reasoning map."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.seen_purposes: list[str] = []

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.seen_purposes.append(request.purpose)
        if self._fail:
            raise RuntimeError("vendor unreachable during synthesis")
        if request.purpose == "FINAL_SYNTHESIS":
            return ModelResult(
                call_id=uuid4(),
                payload=FrozenDict(MAP_PAYLOAD),
                input_tokens=50,
                output_tokens=120,
                cost_usd=Decimal("0.0010"),
                latency_ms=800,
                retries=0,
                schema_status=SchemaStatus.OK,
            )
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict({"ok": True}),
            input_tokens=10,
            output_tokens=10,
            cost_usd=Decimal("0.0001"),
            latency_ms=10,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


async def _run_to_completion(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    *,
    gateway: ModelGateway | None = None,
) -> None:
    """Run a task past the JOINT_MODELING checkpoint to a terminal status."""
    first = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(),
        gateway=gateway,
    )
    if first.run.final_status != TaskStatus.AWAITING_COUNCIL_INPUT:
        return
    async with app_sessions() as session:
        service = ResearchService(ResearchRepository(session))
        await service.submit_council_guidance(task_id, "")
        await session.commit()
    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(),
        gateway=gateway,
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


async def _status(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> str:
    async with sessions() as session:
        value = await session.scalar(
            select(ResearchTaskModel.status).where(
                ResearchTaskModel.task_id == task_id
            )
        )
        return str(value)


async def test_a_completed_task_writes_the_paper_event(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """FINAL_PAPER_DRAFTED is written, stays process-only, and the run works."""
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    gateway = _PaperGateway()
    await _run_to_completion(app_sessions, projector_sessions, task_id, gateway=gateway)

    assert "FINAL_SYNTHESIS" in gateway.seen_purposes
    events = await _events(app_sessions, task_id)
    paper_events = [
        event for event in events if event.event_type == "FINAL_PAPER_DRAFTED"
    ]
    assert len(paper_events) == 1
    assert paper_events[0].status == "process_only"
    payload = dict(paper_events[0].payload)
    assert payload["title"] == EXPECTED_TITLE
    rmap = payload["reasoning_map"]
    assert rmap["mode"] == "model"
    assert rmap["bottom_line"] == MAP_PAYLOAD["bottom_line"]
    # Steps come back in spine order (evidence before causality), and an id
    # the catalogue never issued is dropped rather than rendered.
    assert [step["dimension"] for step in rmap["steps"]] == [
        "evidence",
        "causality",
    ]
    assert "NOT-AN-ID" not in rmap["steps"][1]["refs"]
    # The prose fields are derived from the map, step for step.
    sections = payload["sections"]
    assert isinstance(sections, list)
    assert sections[0]["heading"].startswith("1. ")

    # The paper is not a graph node: the graph holds only evidence nodes
    # (the ResearchQuestion the run itself projects). The paper events were
    # marked process_only above, so no node for them exists.
    async with app_sessions() as session:
        nodes = (
            await session.execute(
                select(GraphNodeModel).where(
                    GraphNodeModel.task_id == task_id
                )
            )
        ).scalars()
        node_rows = list(nodes)
    assert {node.node_type for node in node_rows} == {"ResearchQuestion"}


async def test_a_run_without_gateway_writes_fallback_paper_and_keeps_the_status(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """No model provider -> a fallback integrated paper, status unchanged.

    Round-9 「最终论文总是整合结论」: with no gateway the researcher still
    asked a question and must see an integrated conclusion, so the worker
    assembles one from the brief alone (``fallback: true``). The terminal
    status stays a function of evidence gaps -- COMPLETED_WITH_GAPS is not a
    failure to admit absence, it is the honest state of the run.
    """
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    await _run_to_completion(app_sessions, projector_sessions, task_id)

    events = await _events(app_sessions, task_id)
    drafted = [
        event for event in events if event.event_type == "FINAL_PAPER_DRAFTED"
    ]
    assert len(drafted) == 1
    assert drafted[0].payload.get("fallback") is True
    assert await _status(app_sessions, task_id) in (
        TaskStatus.COMPLETED_WITH_GAPS,
        TaskStatus.COMPLETED,
    )


async def test_a_failed_synthesis_falls_back_to_an_integrated_paper(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """A vendor failure writes FINAL_PAPER_FAILED *and* a fallback paper.

    Round-9: a failed or quarantined synthesis must not leave the researcher
    with the "综合论文尚未生成" stub. The failure is recorded honestly as
    FINAL_PAPER_FAILED; the integrated conclusion is assembled from the brief
    and written as a fallback FINAL_PAPER_DRAFTED.
    """
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    gateway = _PaperGateway(fail=True)
    await _run_to_completion(app_sessions, projector_sessions, task_id, gateway=gateway)

    events = await _events(app_sessions, task_id)
    failed = [
        event for event in events if event.event_type == "FINAL_PAPER_FAILED"
    ]
    assert len(failed) == 1
    assert "vendor unreachable" in str(failed[0].payload.get("reason", ""))
    drafted = [
        event for event in events if event.event_type == "FINAL_PAPER_DRAFTED"
    ]
    assert len(drafted) == 1
    assert drafted[0].payload.get("fallback") is True
    assert await _status(app_sessions, task_id) in (
        TaskStatus.COMPLETED_WITH_GAPS,
        TaskStatus.COMPLETED,
    )


async def test_the_paper_endpoint_serves_paper_and_stub(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """JSON returns the paper; markdown renders it with a DOI link."""
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    gateway = _PaperGateway()
    await _run_to_completion(app_sessions, projector_sessions, task_id, gateway=gateway)

    json_response = await api_client.get(f"/api/reports/{task_id}/paper")
    assert json_response.status_code == 200, json_response.text
    body = json_response.json()
    assert body["available"] is True
    assert body["paper"]["title"] == EXPECTED_TITLE
    assert body["reason"] is None

    markdown_response = await api_client.get(
        f"/api/reports/{task_id}/paper", params={"format": "markdown"}
    )
    assert markdown_response.status_code == 200
    assert "text/markdown" in markdown_response.headers["content-type"]
    text = markdown_response.text
    assert "## 一句话回答" in text
    assert str(MAP_PAYLOAD["bottom_line"]) in text
    # Conclusion and limits travel together inside the step.
    assert text.index("混杂与反向因果仍是主要威胁") < text.index("仅一项纵向研究")


async def test_the_paper_endpoint_answers_with_a_fallback_when_missing(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """A task whose synthesis never ran gets a fallback paper, not a stub.

    Round-9 「最终论文总是整合结论」: with no model provider the researcher
    still asked a question, so the endpoint serves an integrated paper
    assembled from the brief (``fallback: true``) instead of the old
    "综合论文尚未生成" stub. Honesty is preserved: the paper says it is a
    template integration, and the markdown renders the fallback notice.
    """
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    await _run_to_completion(app_sessions, projector_sessions, task_id)

    json_response = await api_client.get(f"/api/reports/{task_id}/paper")
    assert json_response.status_code == 200
    body = json_response.json()
    assert body["available"] is True
    assert body["paper"] is not None
    assert body["paper"].get("fallback") is True

    markdown_response = await api_client.get(
        f"/api/reports/{task_id}/paper", params={"format": "markdown"}
    )
    assert markdown_response.status_code == 200
    assert "未经模型整合" in markdown_response.text


async def test_the_workspace_snapshot_carries_paper_and_consensus(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """The workspace exposes paper + consensus for the panels and the CLI."""
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    gateway = _PaperGateway()
    await _run_to_completion(app_sessions, projector_sessions, task_id, gateway=gateway)

    response = await api_client.get(f"/api/workspace/{task_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["paper"] is not None
    assert body["paper"]["title"] == EXPECTED_TITLE
    assert body["paper"]["reasoning_map"]["mode"] == "model"
    # consensus is either the joint-modeling text or null -- the key exists.
    assert "consensus" in body


async def test_a_pre_map_paper_is_reorganised_on_read(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """A paper stored before the reasoning map existed gets one on read.

    The stored event is left untouched (append-only ledger); the workspace
    and the paper endpoint file the same catalogue deterministically and mark
    the map ``derived_on_read``.
    """
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    await _run_to_completion(app_sessions, projector_sessions, task_id)
    legacy = {
        "title": "旧版论文",
        "abstract": "旧版摘要。",
        "sections": [{"heading": "发现", "paragraphs": ["相关性一致。"]}],
        "references": [],
        "limitations": [],
        "investigation_process": [],
        "overall_conclusion": "旧版结论：弱相关。",
    }
    async with app_sessions() as session:
        await SqlEventLedger(session).append(
            task_id, "FINAL_PAPER_DRAFTED", legacy, "test:legacy_paper"
        )
        await session.commit()

    response = await api_client.get(f"/api/workspace/{task_id}")
    assert response.status_code == 200
    rmap = response.json()["paper"]["reasoning_map"]
    assert rmap["derived_on_read"] is True
    assert rmap["mode"] == "deterministic"
    assert rmap["bottom_line"] == "旧版结论：弱相关。"

    paper_response = await api_client.get(f"/api/reports/{task_id}/paper")
    assert paper_response.json()["paper"]["reasoning_map"]["derived_on_read"] is True
    stored = [
        event
        for event in await _events(app_sessions, task_id)
        if event.idempotency_key == "test:legacy_paper"
    ]
    assert "reasoning_map" not in stored[0].payload
