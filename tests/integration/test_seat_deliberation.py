"""The seat-to-Model-Gateway bridge, exercised through the whole pipeline.

CLAUDE.md 8 requires every model call to go through the gateway, and CLAUDE.md 10
requires every call to be recorded with its latency, cost, and retries. Both are
checked here against the real ``model_calls`` table rather than against a mock,
because an audit trail that only exists in a test double audits nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.council.contracts import Seat
from packages.council.deliberation import (
    PHASE_OUTPUT_SCHEMAS,
    GatewayDeliberator,
    deliberator_for,
)
from packages.council.rounds.registry import SEAT_UNAVAILABLE
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.evidence.models import ScientificEventModel
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus
from packages.models.models import ModelCallModel
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED
from packages.tools.contracts import ToolRequest, ToolResult

QUESTION = "Does adolescent social media use cause depressive symptoms?"


class _ScriptedGateway:
    """A gateway that answers every phase with usable, deterministic output.

    Deterministic because the pipeline replays: an answer that varied per call
    would turn an idempotency bug into an intermittent one.
    """

    def __init__(
        self,
        claim_id: UUID,
        blindspot_id: UUID,
        schema_status: SchemaStatus = SchemaStatus.OK,
    ) -> None:
        self.calls: list[ModelRequest] = []
        self._claim_id = claim_id
        self._blindspot_id = blindspot_id
        self._schema_status = schema_status

    def _payload(self, request: ModelRequest) -> dict[str, object]:
        phase = TaskPhase(request.purpose)
        seat = request.actor
        if phase is TaskPhase.PRECOMMITMENT:
            return {
                "initial_judgment": f"{seat}: correlational support only",
                "confidence": 0.4,
                "update_condition": "a preregistered cohort study",
            }
        if phase is TaskPhase.ACQUISITION:
            # A DOI rather than free text: the adapters resolve DOIs, and a
            # request nobody can resolve is correctly reported as a gap.
            return {"requests": ["doi 10.1234/shared-cohort"]}
        if phase is TaskPhase.CROSS_EXAMINATION:
            return {
                "challenges": [
                    {
                        "claim_id": str(self._claim_id),
                        "statement": f"{seat} disputes the exposure measure",
                        "is_fatal": False,
                    }
                ]
            }
        if phase is TaskPhase.BLINDSPOT_BOUNTY:
            return {
                "blindspots": [
                    {
                        "id": str(self._blindspot_id),
                        "statement": "Self-reported screen time is misremembered.",
                        "impact": "0.9",
                        "uncertainty": "0.8",
                        "investigability": "0.7",
                        "novelty": "0.6",
                        "normalized_cost": "0.2",
                    }
                ]
            }
        if phase is TaskPhase.JOINT_MODELING:
            return {
                "strongest_opposition_refs": [str(self._claim_id)],
                "falsification_conditions": ["A null effect in a preregistered RCT."],
                "boundary_conditions": ["Western adolescent samples only."],
                "unresolved_conflicts": ["Effect direction across sexes."],
            }
        if phase is TaskPhase.FINAL_REJUDGMENT:
            return {"final_judgment": f"{seat}: narrowed, not withdrawn"}
        return {}

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.calls.append(request)
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(self._payload(request)),
            input_tokens=100,
            output_tokens=50,
            cost_usd=0,
            latency_ms=12,
            retries=0,
            schema_status=self._schema_status,
        )


class _BrokenGateway:
    """Every call fails. A provider outage must degrade, not abort."""

    async def invoke(self, request: ModelRequest) -> ModelResult:
        raise RuntimeError("provider unreachable")


class _StubProvider:
    """A tool gateway that resolves any DOI, so acquisition can succeed."""

    async def execute(self, request: ToolRequest) -> ToolResult:
        doi = str(request.arguments["doi"])
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict(
                {
                    "id": f"https://openalex.org/{doi}",
                    "title": f"Study of {doi}",
                    "authors": ("A. Researcher",),
                    "year": 2021,
                    "type": "journal-article",
                    "retracted": False,
                }
            ),
            latency_ms=2,
            retries=0,
            error_code=None,
        )


async def _seed_queued_task(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID]:
    task_id = uuid4()
    claim_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="deliberation_test",
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
                id=claim_id,
                task_id=task_id,
                statement="Heavy use predicts higher depressive symptom scores.",
                claim_type="correlational",
                scope={"population": "adolescents"},
                falsification_condition="A preregistered cohort finds a null effect.",
                status=CLAIM_CONFIRMED,
                created_by="deliberation_test",
            )
        )
        await session.commit()
    return task_id, claim_id


async def _event_types(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[str]:
    async with sessions() as session:
        result = await session.execute(
            select(ScientificEventModel.event_type).where(
                ScientificEventModel.task_id == task_id
            )
        )
        return list(result.scalars())


def test_no_gateway_means_no_deliberator() -> None:
    """A deployment with no provider must not silently get a fake one."""
    assert deliberator_for(None) is None
    assert isinstance(deliberator_for(_BrokenGateway()), GatewayDeliberator)


def test_every_deliberating_phase_has_an_output_schema() -> None:
    """A phase with no schema would ask the model for unstructured prose.

    Reporting is excluded on purpose: it reads the graph rather than asking the
    seats anything.
    """
    expected = {phase for phase in TaskPhase if phase is not TaskPhase.REPORTING}
    assert set(PHASE_OUTPUT_SCHEMAS) == expected


async def test_a_full_council_run_reports_no_gaps(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The no-gaps path must be reachable, or COMPLETED_WITH_GAPS means nothing."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await run_task(
        app_sessions, projector_sessions, task_id, gateway=gateway,
        tools=_StubProvider(),
    )

    assert result.run.unfilled_slots == ()
    assert result.run.absent_seats == frozenset()
    assert result.run.final_status == TaskStatus.COMPLETED
    assert SEAT_UNAVAILABLE not in await _event_types(app_sessions, task_id)


async def test_every_model_call_is_audited(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10: latency, cost, and retries are recorded for every call."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    await run_task(app_sessions, projector_sessions, task_id, gateway=gateway)

    async with app_sessions() as session:
        audited = await session.scalar(
            select(func.count())
            .select_from(ModelCallModel)
            .where(ModelCallModel.task_id == task_id)
        )
    # Seven seats across seven deliberating rounds; reporting asks no one.
    assert len(gateway.calls) == 7 * 7
    assert audited == len(gateway.calls)


async def test_the_seven_seats_are_asked_different_questions(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 3 forbids seven copies of one agent sharing one prompt."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    await run_task(app_sessions, projector_sessions, task_id, gateway=gateway)

    systems = {
        request.actor: request.messages[0].content
        for request in gateway.calls
        if TaskPhase(request.purpose) is TaskPhase.PRECOMMITMENT
    }
    assert set(systems) == {seat.value for seat in Seat}
    assert len(set(systems.values())) == 7


async def test_quarantined_output_never_becomes_a_seat_judgment(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10: unrepairable structured output is isolated, not admitted."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(
        claim_id, uuid4(), schema_status=SchemaStatus.QUARANTINED
    )

    result = await run_task(
        app_sessions, projector_sessions, task_id, gateway=gateway
    )

    assert result.run.absent_seats == frozenset(Seat)
    assert result.run.final_status == TaskStatus.COMPLETED_WITH_GAPS
    # The calls still happened and are still audited; only the output was refused.
    assert len(gateway.calls) == 7 * 7


async def test_a_provider_outage_degrades_the_run_instead_of_failing_it(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10: one seat's failure must not abort the whole task."""
    task_id, _ = await _seed_queued_task(app_sessions)

    result = await run_task(
        app_sessions, projector_sessions, task_id, gateway=_BrokenGateway()
    )

    assert result.run.failures == ()
    assert result.run.absent_seats == frozenset(Seat)
    assert result.run.final_status == TaskStatus.COMPLETED_WITH_GAPS


async def test_the_prompt_carries_the_question_and_the_confirmed_claims(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A seat asked without the question would be deliberating about nothing."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    await run_task(app_sessions, projector_sessions, task_id, gateway=gateway)

    user_messages = [request.messages[1].content for request in gateway.calls]
    assert all(QUESTION in message for message in user_messages)
    assert all(str(claim_id) in message for message in user_messages)
    assert all(claim_id in request.evidence_refs for request in gateway.calls)


async def test_a_seat_is_given_its_own_recall_and_no_one_elses(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Private memory that never reaches a prompt is decoration.

    CLAUDE.md 6 puts process memory in MemoBrain so later rounds can use it, and
    CLAUDE.md 3 keeps it private. Both only mean something at the prompt: the
    later rounds must carry recall, and it must be the asking seat's own.
    """
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    await run_task(app_sessions, projector_sessions, task_id, gateway=gateway)

    later = [
        request
        for request in gateway.calls
        if TaskPhase(request.purpose) is TaskPhase.FINAL_REJUDGMENT
    ]
    assert later, "final rejudgment must have asked the seats"
    for request in later:
        prompt = request.messages[1].content
        assert "Your private recall:" in prompt
        # The recall is seeded with the question and grown one line per round,
        # so a seat that had been handed the council's shared memory would show
        # seven times as many phase markers as it ran.
        assert prompt.count(TaskPhase.PRECOMMITMENT.value) <= 2

    first = [
        request
        for request in gateway.calls
        if TaskPhase(request.purpose) is TaskPhase.PRECOMMITMENT
    ]
    assert all(
        "Your private recall:" in request.messages[1].content for request in first
    )


def test_a_scripted_payload_matches_what_the_rounds_read() -> None:
    """Guards the seam between the prompt schema names and the runners' keys.

    The gateway returns free-form JSON, so nothing but this check stops a
    renamed key from silently producing an empty round.
    """
    gateway = _ScriptedGateway(uuid4(), uuid4())
    seen: dict[TaskPhase, Mapping[str, object]] = {}
    for phase in PHASE_OUTPUT_SCHEMAS:
        request = ModelRequest(
            task_id=uuid4(),
            actor=Seat.THEORY_BUILDER.value,
            purpose=phase.value,
            model_class="medium",
            messages=(),
            output_schema=PHASE_OUTPUT_SCHEMAS[phase],
            evidence_refs=(),
        )
        seen[phase] = gateway._payload(request)

    assert "initial_judgment" in seen[TaskPhase.PRECOMMITMENT]
    assert "requests" in seen[TaskPhase.ACQUISITION]
    assert "challenges" in seen[TaskPhase.CROSS_EXAMINATION]
    assert "blindspots" in seen[TaskPhase.BLINDSPOT_BOUNTY]
    assert "falsification_conditions" in seen[TaskPhase.JOINT_MODELING]
    assert "final_judgment" in seen[TaskPhase.FINAL_REJUDGMENT]
