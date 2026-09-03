"""B7: per-phase durable checkpoint hooks in CouncilOrchestrator.run().

A worker process can die mid-council. Before B7 the whole eight-phase
deliberation lived in one uncommitted transaction, so a crash discarded every
phase and the reclaim re-ran (and re-billed) the whole council. The
orchestrator now offers an ``on_phase_checkpoint`` hook the worker uses to
commit after each phase; these DB-free tests pin the hook contract:

* it fires once per phase that ran, with a prefix-growing checkpoint;
* a mid-stream checkpoint is never marked ``gate_reached`` -- only the fixed
  BLINDSPOT_BOUNTY -> JOINT_MODELING human halt carries that marker, so a crash
  before the gate halts at the gate again on resume instead of bypassing it;
* resuming from a mid-stream checkpoint re-runs neither completed phases nor
  their ledger events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    TaskPhase,
    TaskStatus,
)
from packages.epistemo.orchestrator import CouncilOrchestrator, SeatRunRecord

_GENEROUS_BUDGET = ResearchBudget(
    wall_clock_minutes=1000,
    model_cost_usd=Decimal("1000"),
    tool_call_limit=1000,
    source_limit=1000,
)


@dataclass
class _FakeEventSink:
    calls: list[object] = field(default_factory=list)

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
        self.calls.append(idempotency_key)
        return None

    def recorded_keys(self) -> list[str]:
        return [str(call) for call in self.calls]


async def test_phase_checkpoint_hook_fires_once_per_phase_in_order() -> None:
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    seen_prefixes: list[tuple[TaskPhase, ...]] = []
    seen_flags: list[bool] = []
    seen_run_counts: list[int] = []

    async def on_phase_checkpoint(
        checkpoint: object, seat_runs: tuple[SeatRunRecord, ...]
    ) -> None:
        seen_prefixes.append(tuple(checkpoint.run_phases))  # type: ignore[attr-defined]
        seen_flags.append(bool(checkpoint.gate_reached))  # type: ignore[attr-defined]
        seen_run_counts.append(len(seat_runs))

    orchestrator = CouncilOrchestrator(
        ledger=sink,
        budget=budget,
        on_phase_checkpoint=on_phase_checkpoint,
    )

    report = await orchestrator.run(task_id=uuid4(), question="does X cause Y?")

    # One hook call per phase that ran, each checkpoint's run_phases is exactly
    # the protocol prefix completed so far.
    assert report.phases_run == PHASE_SEQUENCE
    expected_prefixes = [
        tuple(PHASE_SEQUENCE[: index + 1]) for index in range(len(PHASE_SEQUENCE))
    ]
    assert seen_prefixes == expected_prefixes
    # A mid-stream checkpoint never claims the human gate was reached.
    assert seen_flags == [False] * len(PHASE_SEQUENCE)
    # REPORTING polls no seats, so its hook carries no seat run records; every
    # deliberating phase carries exactly seven (one record per seat).
    assert seen_run_counts[-1] == 0
    assert all(count == 7 for count in seen_run_counts[:-1])


async def test_hook_is_optional_and_default_run_is_unchanged() -> None:
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)

    report = await orchestrator.run(task_id=uuid4(), question="does X cause Y?")

    assert report.phases_run == PHASE_SEQUENCE
    assert report.checkpoint is not None
    # A fully-finished run never reached a halt: gate_reached stays False.
    assert report.checkpoint.gate_reached is False


async def test_gate_halt_marks_only_its_checkpoint_gate_reached() -> None:
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    hook_flags: list[bool] = []

    async def on_phase_checkpoint(
        checkpoint: object, seat_runs: tuple[SeatRunRecord, ...]
    ) -> None:
        hook_flags.append(bool(checkpoint.gate_reached))  # type: ignore[attr-defined]

    orchestrator = CouncilOrchestrator(
        ledger=sink,
        budget=budget,
        on_phase_checkpoint=on_phase_checkpoint,
    )

    report = await orchestrator.run(
        task_id=uuid4(),
        question="does X cause Y?",
        stop_before=TaskPhase.JOINT_MODELING,
    )

    assert report.final_status is TaskStatus.AWAITING_COUNCIL_INPUT
    assert report.checkpoint is not None
    # The returned halt checkpoint marks the gate; the five per-phase hook
    # checkpoints written while running towards it do not.
    assert report.checkpoint.gate_reached is True
    assert hook_flags == [False] * 5


async def test_midstream_checkpoint_resumes_then_halts_at_the_gate_once() -> None:
    """A crash after two phases resumes at the third and still honours the gate.

    The first pass halts (as a killed worker would) before EVIDENCE_EXCHANGE;
    its checkpoint carries only PRECOMMITMENT/ACQUISITION and is not gate-marked.
    The resumed pass skips those two phases -- their events are not re-appended
    -- and halts at JOINT_MODELING with a gate-marked checkpoint.
    """
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)
    task_id = uuid4()

    first = await orchestrator.run(
        task_id=task_id,
        question="does X cause Y?",
        stop_before=TaskPhase.EVIDENCE_EXCHANGE,
    )
    assert first.checkpoint is not None
    midstream = first.checkpoint
    assert midstream.run_phases == (
        TaskPhase.PRECOMMITMENT,
        TaskPhase.ACQUISITION,
    )
    assert midstream.gate_reached is False
    keys_before_resume = sink.recorded_keys()

    second = await orchestrator.run(
        task_id=task_id,
        question="does X cause Y?",
        resume_from=midstream,
        stop_before=TaskPhase.JOINT_MODELING,
    )

    assert second.final_status is TaskStatus.AWAITING_COUNCIL_INPUT
    assert second.checkpoint is not None
    assert second.checkpoint.gate_reached is True
    assert second.phases_run == (
        TaskPhase.PRECOMMITMENT,
        TaskPhase.ACQUISITION,
        TaskPhase.EVIDENCE_EXCHANGE,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.BLINDSPOT_BOUNTY,
    )
    # Nothing the crashed pass already appended is appended a second time.
    for key in keys_before_resume:
        assert sink.recorded_keys().count(key) == 1, key
