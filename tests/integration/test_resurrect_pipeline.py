"""Resurrect's full ledger-to-projector round trip, against a real database.

Design spec 5, mechanism 1 of 3: new evidence that satisfies a quarantined
node's recorded resurrection condition should produce a RESURRECTION_GRANTED
event. Unit coverage in tests/unit/test_run_evidence_exchange_resurrect.py
already checks ``_resurrection_events`` in isolation with a scripted
``PhaseContext``; this test checks the part that unit test cannot reach: that
a real quarantined event, loaded back from the ledger by
``apps/worker/jobs.py::_quarantined_nodes``, actually reaches a live council
run and that the resulting RESURRECTION_GRANTED event -- a process-level
status change, not a new Evidence Graph node type -- is admitted onto the
ledger without ever being written into ``graph_nodes`` (CLAUDE.md 5.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.council.contracts import Seat
from packages.council.rounds.registry import RESURRECTION_GRANTED, PhaseContext
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.models import GraphNodeModel, ScientificEventModel
from packages.evidence.sql_ledger import SqlEventLedger
from packages.evidence.sql_projector import STATUS_PROCESS_ONLY, SqlGraphProjector
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED

QUESTION = "Does adolescent social media use cause depressive symptoms?"


async def _seed_queued_task(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    task_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="resurrect_pipeline_test",
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
                created_by="resurrect_pipeline_test",
            )
        )
        await session.commit()
    return task_id


async def _seed_quarantined_claim(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> UUID:
    """Quarantine one event the same way a real gate refusal would.

    Mirrors tests/integration/test_sql_projector.py's own causal-overclaim
    scenario: a ``causal`` claim built on ``cross_sectional`` evidence is the
    gate's own refusal rule (CLAUDE.md 3), not a fixture invented for this
    test.
    """
    claim_id = uuid4()
    async with app_sessions() as app_session:
        await SqlEventLedger(app_session).append(
            task_id,
            EvidenceNodeType.CLAIM.value,
            {"claim_type": "causal", "study_design": "cross_sectional"},
            "quarantine-seed",
            evidence_level="A",
            claim_id=claim_id,
        )
        await app_session.commit()
    async with projector_sessions() as projector_session:
        await SqlGraphProjector(projector_session).project_pending(task_id)
        await projector_session.commit()
    return claim_id


class _ResurrectionRequestingDeliberator:
    """Plays one seat's EVIDENCE_EXCHANGE output; silent everywhere else.

    Every other phase reporting absent is the honest outcome for a scripted
    deliberator that only cares about one round (CLAUDE.md 7) -- the point of
    this test is Resurrect, not a full seven-phase report.
    """

    def __init__(self, node_id: UUID, evidence_ref: UUID) -> None:
        self._node_id = node_id
        self._evidence_ref = evidence_ref

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        if phase is TaskPhase.EVIDENCE_EXCHANGE and seat is Seat.EVIDENCE_AUDITOR:
            return {
                "resurrection_requests": [
                    {
                        "node_id": str(self._node_id),
                        "evidence_refs": [str(self._evidence_ref)],
                    }
                ]
            }
        return None


async def test_new_evidence_resurrects_a_gate_quarantined_claim(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id = await _seed_queued_task(app_sessions)
    quarantined_claim_id = await _seed_quarantined_claim(
        app_sessions, projector_sessions, task_id
    )
    evidence_ref = uuid4()

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ResurrectionRequestingDeliberator(
            quarantined_claim_id, evidence_ref
        ),
    )

    async with app_sessions() as session:
        events = (
            await session.scalars(
                select(ScientificEventModel)
                .where(
                    ScientificEventModel.task_id == task_id,
                    ScientificEventModel.event_type == RESURRECTION_GRANTED,
                )
                .order_by(ScientificEventModel.sequence)
            )
        ).all()

    assert len(events) == 1
    granted = events[0]
    assert granted.payload["node_id"] == str(quarantined_claim_id)
    assert granted.payload["evidence_refs"] == [str(evidence_ref)]
    # A status change, not a new formal node type (CLAUDE.md 5.1): it must
    # never reach graph_nodes, admitted or otherwise.
    assert granted.status == STATUS_PROCESS_ONLY

    async with projector_sessions() as session:
        node = await session.get(GraphNodeModel, granted.id)
    assert node is None
