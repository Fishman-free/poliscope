"""Plan phase 8.1: CouncilOrchestrator.run()'s stop_before/resume_from gate.

No existing test instantiates CouncilOrchestrator directly -- every other
exercise of it goes through the DB-backed worker/integration path. These tests
are DB-free: a fake EventSink records what the orchestrator appended, and the
default UnavailableDeliberator (no model provider configured) is exactly what
every other unit test in this repo already treats as the honest, no-fabrication
baseline (see packages/council/rounds/registry.py). That default also means no
seat ever produces a request, so ACQUISITION never needs a real SourceAcquirer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    CouncilCheckpoint,
    TaskPhase,
    TaskStatus,
)
from packages.epistemo.orchestrator import CouncilOrchestrator

_GENEROUS_BUDGET = ResearchBudget(
    wall_clock_minutes=1000,
    model_cost_usd=Decimal("1000"),
    tool_call_limit=1000,
    source_limit=1000,
)

_EXHAUSTED_BUDGET = ResearchBudget(
    wall_clock_minutes=1000,
    model_cost_usd=Decimal("1000"),
    tool_call_limit=0,
    source_limit=1000,
)


@dataclass(frozen=True, slots=True)
class _RecordedAppend:
    task_id: UUID
    event_type: str
    payload: dict[str, object]
    idempotency_key: str
    status: str = "pending"
    evidence_level: str | None = None
    source_id: UUID | None = None
    finding_id: UUID | None = None
    claim_id: UUID | None = None


@dataclass
class _FakeEventSink:
    """Records every append; never rejects a duplicate key itself.

    Idempotency enforcement is the real ledger's job (packages/evidence/
    sql_ledger.py), not the orchestrator's -- so this fake, like the
    orchestrator, does not deduplicate. Tests instead assert on *how many
    times* a given phase's events were appended, which is what actually
    proves a resumed run does not re-run an already-completed phase.
    """

    calls: list[_RecordedAppend] = field(default_factory=list)

    async def append(
        self,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
        status: str = "pending",
        *,
        evidence_level: str | None = None,
        source_id: UUID | None = None,
        finding_id: UUID | None = None,
        claim_id: UUID | None = None,
    ) -> object:
        self.calls.append(
            _RecordedAppend(
                task_id=task_id,
                event_type=event_type,
                payload=dict(payload),
                idempotency_key=idempotency_key,
                status=status,
                evidence_level=evidence_level,
                source_id=source_id,
                finding_id=finding_id,
                claim_id=claim_id,
            )
        )
        return None

    def recorded_keys(self) -> list[str]:
        return [call.idempotency_key for call in self.calls]


async def test_default_run_is_unchanged_and_carries_no_checkpoint() -> None:
    """Neither stop_before nor resume_from passed -> prior behaviour exactly."""
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)

    report = await orchestrator.run(task_id=uuid4(), question="does X cause Y?")

    assert report.phases_run == PHASE_SEQUENCE
    assert report.phases_skipped == ()
    assert report.checkpoint is None
    assert report.final_status == TaskStatus.COMPLETED_WITH_GAPS
    assert report.stop_reason.value == "CONTINUE"


async def test_stop_before_halts_exactly_before_joint_modeling() -> None:
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)

    report = await orchestrator.run(
        task_id=uuid4(),
        question="does X cause Y?",
        stop_before=TaskPhase.JOINT_MODELING,
    )

    expected_phases = (
        TaskPhase.PRECOMMITMENT,
        TaskPhase.ACQUISITION,
        TaskPhase.EVIDENCE_EXCHANGE,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.BLINDSPOT_BOUNTY,
    )
    assert report.final_status == TaskStatus.AWAITING_COUNCIL_INPUT
    assert report.phases_run == expected_phases
    assert report.checkpoint is not None
    assert report.checkpoint.run_phases == expected_phases

    # JOINT_MODELING never ran: no event carries its idempotency-key prefix.
    assert not any(key.startswith("JOINT_MODELING:") for key in sink.recorded_keys())
    # Every earlier phase's PHASE_STARTED marker fired exactly once.
    for phase in expected_phases:
        assert sink.recorded_keys().count(f"{phase.value}:started") == 1


async def test_resume_from_checkpoint_completes_without_rerunning_earlier_phases() -> (
    None
):
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)
    task_id = uuid4()

    halted = await orchestrator.run(
        task_id=task_id,
        question="does X cause Y?",
        stop_before=TaskPhase.JOINT_MODELING,
    )
    assert halted.checkpoint is not None
    keys_after_halt = list(sink.recorded_keys())

    resumed = await orchestrator.run(
        task_id=task_id,
        question="does X cause Y?",
        resume_from=halted.checkpoint,
    )

    assert resumed.phases_run == PHASE_SEQUENCE
    assert resumed.final_status == TaskStatus.COMPLETED_WITH_GAPS

    # Nothing from before the checkpoint was appended a second time.
    for key in keys_after_halt:
        assert sink.recorded_keys().count(key) == 1

    # The three phases the checkpoint had not yet reached did run.
    for phase in (
        TaskPhase.JOINT_MODELING,
        TaskPhase.FINAL_REJUDGMENT,
        TaskPhase.REPORTING,
    ):
        assert any(key.startswith(f"{phase.value}:") for key in sink.recorded_keys())


async def test_checkpoint_round_trips_through_json() -> None:
    """CouncilCheckpoint must survive the JSONB column (plan phase 8.1)."""
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)

    halted = await orchestrator.run(
        task_id=uuid4(),
        question="does X cause Y?",
        stop_before=TaskPhase.JOINT_MODELING,
    )
    assert halted.checkpoint is not None

    dumped = halted.checkpoint.model_dump(mode="json")
    restored = CouncilCheckpoint.model_validate(dumped)
    assert restored == halted.checkpoint


async def test_budget_exhaustion_takes_priority_over_checkpoint_halt() -> None:
    """A run with no budget left has nothing for a human to steer.

    It must finish as COMPLETED_WITH_GAPS rather than getting stuck in
    AWAITING_COUNCIL_INPUT waiting for input that cannot restore the budget.
    """
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_EXHAUSTED_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)

    report = await orchestrator.run(
        task_id=uuid4(),
        question="does X cause Y?",
        stop_before=TaskPhase.JOINT_MODELING,
    )

    assert report.final_status == TaskStatus.COMPLETED_WITH_GAPS
    assert report.checkpoint is None
    assert report.phases_run == ()
    assert report.phases_skipped == PHASE_SEQUENCE
    assert report.stop_reason.value == "BUDGET_EXHAUSTED"
