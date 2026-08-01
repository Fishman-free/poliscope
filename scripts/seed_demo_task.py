"""Seed one fully-run demo task so the interface has real state to render.

This is a development aid, not part of the product. It uses the same worker
entry point the real system uses, with a scripted model gateway and a scripted
provider standing in for the vendors that are not wired yet -- so what lands in
the database is produced by the real orchestrator, the real evidence gate, and
the real projector, not by fixtures written straight into the tables.

Run it with the three POLISCOPE_*_DATABASE_URL variables set.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.kernel.contracts import FrozenDict
from packages.kernel.database import create_database_engine, create_session_factory
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED
from packages.tools.contracts import ToolRequest, ToolResult

QUESTION = "青少年社交媒体使用是否导致抑郁症状上升？"

CLAIMS = [
    (
        "重度社交媒体使用与更高的抑郁症状量表得分相关。",
        "correlational",
        "一项预注册的纵向队列研究报告零效应。",
    ),
    (
        "减少社交媒体使用可降低抑郁症状。",
        "causal",
        "一项预注册随机对照试验显示干预组与对照组无差异。",
    ),
]

DOIS = ("10.1234/cohort-2021", "10.1234/rct-2022", "10.1234/meta-2023")

BLINDSPOTS = [
    ("屏幕使用时长依赖自我报告，与客观日志系统性偏离。", "0.9", "0.8", "0.7", "0.7"),
    ("绝大多数样本来自西方高收入国家，外部效度未经检验。", "0.8", "0.7", "0.6", "0.5"),
    ("发表偏倚可能压制了零效应研究，元分析基线被抬高。", "0.85", "0.9", "0.4", "0.6"),
]


class _Gateway:
    """A deterministic stand-in for the Model Gateway."""

    def __init__(self, claim_id: UUID, blindspot_ids: list[UUID]) -> None:
        self._claim_id = claim_id
        self._blindspot_ids = blindspot_ids

    def _payload(self, request: ModelRequest) -> dict[str, object]:
        phase = TaskPhase(request.purpose)
        seat = request.actor
        if phase is TaskPhase.PRECOMMITMENT:
            return {
                "initial_judgment": f"{seat}：现有证据以相关性为主，因果结论证据不足。",
                "confidence": 0.4,
                "update_condition": "一项高质量预注册随机对照试验。",
            }
        if phase is TaskPhase.ACQUISITION:
            return {"requests": [f"doi {doi}" for doi in DOIS]}
        if phase is TaskPhase.CROSS_EXAMINATION:
            return {
                "challenges": [
                    {
                        "claim_id": str(self._claim_id),
                        "statement": f"{seat} 质疑暴露变量的测量效度。",
                        "is_fatal": False,
                    }
                ]
            }
        if phase is TaskPhase.BLINDSPOT_BOUNTY and seat == "adversarial_falsifier":
            return {
                "blindspots": [
                    {
                        "id": str(node_id),
                        "statement": statement,
                        "impact": impact,
                        "uncertainty": uncertainty,
                        "investigability": investigability,
                        "novelty": novelty,
                        "normalized_cost": "0.3",
                    }
                    for node_id, (
                        statement,
                        impact,
                        uncertainty,
                        investigability,
                        novelty,
                    ) in zip(self._blindspot_ids, BLINDSPOTS, strict=True)
                ]
            }
        if phase is TaskPhase.JOINT_MODELING:
            return {
                "strongest_opposition_refs": [str(self._claim_id)],
                "falsification_conditions": [
                    "一项预注册 RCT 在意向性治疗分析下报告零效应。"
                ],
                "boundary_conditions": ["仅限西方高收入国家的青少年样本。"],
                "unresolved_conflicts": ["不同性别间效应方向不一致。"],
            }
        if phase is TaskPhase.FINAL_REJUDGMENT:
            return {"final_judgment": f"{seat}：收窄适用范围，不撤回，保留异议。"}
        return {}

    async def invoke(self, request: ModelRequest) -> ModelResult:
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(self._payload(request)),
            input_tokens=800,
            output_tokens=240,
            cost_usd=0,
            latency_ms=430,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


class _Provider:
    """A deterministic stand-in for a scholarly metadata provider."""

    TITLES = {
        DOIS[0]: "Adolescent social media use and depressive symptoms: a cohort study",
        DOIS[1]: "A randomised trial of reduced social media use",
        DOIS[2]: "Screen time and adolescent mental health: a meta-analysis",
    }

    async def execute(self, request: ToolRequest) -> ToolResult:
        doi = str(request.arguments["doi"])
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict(
                {
                    "id": f"https://openalex.org/{doi}",
                    "title": self.TITLES.get(doi, doi),
                    "authors": ("A. Researcher", "B. Coauthor"),
                    "year": 2022,
                    "type": "journal-article",
                    "retracted": False,
                }
            ),
            latency_ms=180,
            retries=0,
            error_code=None,
        )


async def _seed(sessions: async_sessionmaker[AsyncSession]) -> tuple[UUID, UUID]:
    task_id = uuid4()
    first_claim = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="seed_demo_task",
                wall_clock_minutes=90,
                model_cost_usd=Decimal("25.0000"),
                tool_call_limit=200,
                source_limit=80,
                user_evidence={},
            )
        )
        await session.flush()
        for index, (statement, claim_type, falsification) in enumerate(CLAIMS):
            session.add(
                AtomicClaimModel(
                    id=first_claim if index == 0 else uuid4(),
                    task_id=task_id,
                    statement=statement,
                    claim_type=claim_type,
                    scope={"population": "adolescents", "region": "global"},
                    falsification_condition=falsification,
                    status=CLAIM_CONFIRMED,
                    created_by="seed_demo_task",
                )
            )
        await session.commit()
    return task_id, first_claim


async def main() -> None:
    app_url = os.environ["POLISCOPE_APP_DATABASE_URL"]
    projector_url = os.environ["POLISCOPE_PROJECTOR_DATABASE_URL"]
    app_engine = create_database_engine(app_url)
    projector_engine = create_database_engine(projector_url)
    app_sessions = create_session_factory(app_engine)
    projector_sessions = create_session_factory(projector_engine)
    try:
        task_id, claim_id = await _seed(app_sessions)
        blindspot_ids = [uuid4() for _ in BLINDSPOTS]
        result = await run_task(
            app_sessions,
            projector_sessions,
            task_id,
            gateway=_Gateway(claim_id, blindspot_ids),
            tools=_Provider(),
        )
        print(f"task_id={task_id}")
        print(f"status={result.run.final_status}")
        print(f"events={result.run.events_appended}")
        print(f"unfilled={len(result.run.unfilled_slots)}")
        if result.projection is not None:
            print(
                f"nodes={result.projection.nodes_written} "
                f"edges={result.projection.edges_written} "
                f"admitted={len(result.projection.admitted)} "
                f"leads={len(result.projection.leads)}"
            )
    finally:
        await app_engine.dispose()
        await projector_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
