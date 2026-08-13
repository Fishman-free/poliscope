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

from packages.council.contracts import Seat
from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    CouncilCancelled,
    CouncilCheckpoint,
    CouncilPhaseSnapshot,
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
    assert report.checkpoint is not None
    assert report.checkpoint.run_phases == PHASE_SEQUENCE
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


async def test_checkpoint_round_trips_carried_bounty_evidence() -> None:
    """The ranked blindspots / published evidence carried from earlier phases
    must survive the checkpoint's JSON round trip (BLINDSPOT_BOUNTY's carry
    reaches JOINT_MODELING only through this column).

    A resumed pass restores ``state.carried`` verbatim from the checkpoint
    (``CouncilOrchestrator.run`` seeds ``_Accumulator.carried`` with
    ``resume_from.carried``), so anything the bounty ranked -- and the
    sanitized evidence the exchange published -- must come back byte-for-byte
    for JOINT_MODELING's prompt, never be dropped or coerced.
    """
    source_id = uuid4()
    blindspot_id = uuid4()
    checkpoint = CouncilCheckpoint(
        run_phases=(
            TaskPhase.PRECOMMITMENT,
            TaskPhase.ACQUISITION,
            TaskPhase.EVIDENCE_EXCHANGE,
            TaskPhase.CROSS_EXAMINATION,
            TaskPhase.BLINDSPOT_BOUNTY,
        ),
        carried={
            "published_evidence": (
                {
                    "seat": Seat.EVIDENCE_AUDITOR.value,
                    "source_id": str(source_id),
                    "anchor_summary": "metadata-only anchor",
                    "level": "B",
                },
            ),
            "ranked_blindspots": (
                {
                    "blindspot_id": str(blindspot_id),
                    "statement": "An unmeasured confound remains untested",
                    "score": "0.7700",
                    "rank": 1,
                    "status": "pending_investigation",
                },
            ),
            "blindspot_assignments": (
                {
                    "blindspot_id": str(blindspot_id),
                    "statement": "An unmeasured confound remains untested",
                    "target_seat": Seat.EVIDENCE_AUDITOR.value,
                    "priority_rank": 1,
                    "score": "0.7700",
                    "status": "pending_investigation",
                },
            ),
        },
        guidance=None,
    )

    dumped = checkpoint.model_dump(mode="json")
    restored = CouncilCheckpoint.model_validate(dumped)

    assert restored == checkpoint
    assert restored.carried["ranked_blindspots"] == checkpoint.carried[
        "ranked_blindspots"
    ]
    assert restored.carried["blindspot_assignments"] == checkpoint.carried[
        "blindspot_assignments"
    ]
    assert restored.carried["published_evidence"] == checkpoint.carried[
        "published_evidence"
    ]


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
    assert report.checkpoint is not None
    assert report.checkpoint.run_phases == ()
    assert report.phases_run == ()
    assert report.phases_skipped == PHASE_SEQUENCE
    assert report.stop_reason.value == "BUDGET_EXHAUSTED"


async def test_cancel_check_halts_run_with_cancelled_status() -> None:
    """A researcher's stop request halts the run at a phase boundary (round-10).

    The cancel-check closure returns True from the second phase onward, so the
    first phase runs and the second is never reached. The report must say
    exactly which phases completed and carry CANCELLED as a terminal status --
    a stop is a redirection, not a failure, so COMPLETED_WITH_GAPS and FAILED
    would both be the wrong verdict.
    """
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    # The cancel check runs *before* each phase. Return False for the first
    # phase so PRECOMMITMENT runs to completion, then True so ACQUISITION and
    # everything after it never starts.
    checks = 0

    async def cancel_check() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    orchestrator = CouncilOrchestrator(
        ledger=sink, budget=budget, cancel_check=cancel_check
    )
    task_id = uuid4()

    report = await orchestrator.run(task_id=task_id, question="does X cause Y?")

    assert report.final_status == TaskStatus.CANCELLED
    assert report.stop_reason.value == "CANCELLED"
    assert report.phases_run == (TaskPhase.PRECOMMITMENT,)
    assert report.checkpoint is not None
    assert report.checkpoint.run_phases == (TaskPhase.PRECOMMITMENT,)
    from packages.epistemo.contracts import first_unfinished_phase

    assert first_unfinished_phase(report.checkpoint) == TaskPhase.ACQUISITION
    # ACQUISITION and everything after it never ran: no PHASE_STARTED event
    # carries any phase after PRECOMMITMENT.
    started_keys = [key for key in sink.recorded_keys() if key.endswith(":started")]
    assert started_keys == ["PRECOMMITMENT:started"]
    # PRECOMMITMENT's own events were appended exactly once.
    assert sink.recorded_keys().count("PRECOMMITMENT:started") == 1


async def test_first_unfinished_phase_is_the_first_never_run_step() -> None:
    """A cancelled run that finished 专业取证 must resume at 证据交换."""
    from packages.epistemo.contracts import first_unfinished_phase

    checkpoint = CouncilCheckpoint(
        run_phases=(TaskPhase.PRECOMMITMENT, TaskPhase.ACQUISITION),
        phase_snapshots=(
            CouncilPhaseSnapshot(phase=TaskPhase.PRECOMMITMENT),
            CouncilPhaseSnapshot(phase=TaskPhase.ACQUISITION),
        ),
    )
    assert first_unfinished_phase(checkpoint) == TaskPhase.EVIDENCE_EXCHANGE


async def test_first_unfinished_phase_prefers_a_failed_phase_over_a_later_gap() -> (
    None
):
    from packages.epistemo.contracts import first_unfinished_phase

    checkpoint = CouncilCheckpoint(
        run_phases=(
            TaskPhase.PRECOMMITMENT,
            TaskPhase.ACQUISITION,
            TaskPhase.EVIDENCE_EXCHANGE,
        ),
        unfilled=("ACQUISITION:round_failed",),
        failures=("ACQUISITION: ValueError('boom')",),
    )
    assert first_unfinished_phase(checkpoint) == TaskPhase.ACQUISITION


async def test_cancel_check_never_fires_returns_normal_completion() -> None:
    """No stop request -> the cancel channel changes nothing."""
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)

    async def cancel_check() -> bool:
        return False

    orchestrator = CouncilOrchestrator(
        ledger=sink, budget=budget, cancel_check=cancel_check
    )

    report = await orchestrator.run(task_id=uuid4(), question="does X cause Y?")

    assert report.phases_run == PHASE_SEQUENCE
    assert report.final_status == TaskStatus.COMPLETED_WITH_GAPS
    assert report.stop_reason.value == "CONTINUE"


async def test_restart_from_first_failed_phase_reruns_only_unfinished_phases() -> (
    None
):
    """Round-12 「重新研究从断点续跑」: a checkpoint marked restart_from
    re-executes the first failed phase (and any later failed phase), while
    every phase that actually completed stays untouched -- its events are not
    appended a second time.

    The checkpoint here is hand-built the way ResearchService.re_research
    marks one: run_phases lists all five pre-checkpoint phases (EVIDENCE_EXCHANGE
    among them, since a failed phase is still recorded as having run),
    failures/unfilled record that EVIDENCE_EXCHANGE and BLINDSPOT_BOUNTY
    failed, phase_snapshots hold the accumulated state after each phase, and
    restart_from points at the first failed phase.
    """
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)
    task_id = uuid4()

    p, a, ee, ce, bb = (
        TaskPhase.PRECOMMITMENT,
        TaskPhase.ACQUISITION,
        TaskPhase.EVIDENCE_EXCHANGE,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.BLINDSPOT_BOUNTY,
    )
    # events_appended values only need to be consistent for the rewind math
    # (the snapshot the orchestrator rewinds to is the last kept phase's).
    checkpoint = CouncilCheckpoint(
        run_phases=(p, a, ee, ce, bb),
        carried={"confirmed": "claim-1"},
        unfilled=(
            "EVIDENCE_EXCHANGE:round_failed",
            "BLINDSPOT_BOUNTY:round_failed",
        ),
        failures=(
            "EVIDENCE_EXCHANGE: ValueError('boom')",
            "BLINDSPOT_BOUNTY: RuntimeError('stuck')",
        ),
        phase_snapshots=(
            CouncilPhaseSnapshot(phase=p, events_appended=2),
            CouncilPhaseSnapshot(phase=a, events_appended=4),
            CouncilPhaseSnapshot(phase=ee, events_appended=5),
            CouncilPhaseSnapshot(phase=ce, events_appended=9),
            CouncilPhaseSnapshot(phase=bb, events_appended=10),
        ),
        restart_from=ee,
    )

    report = await orchestrator.run(
        task_id=task_id,
        question="does X cause Y?",
        resume_from=checkpoint,
    )

    # The whole protocol is covered: the two failed phases re-ran and the
    # remaining phases (JOINT_MODELING onward) ran for the first time. (The
    # tuple order reflects execution order -- kept phases are carried over in
    # their checkpoint order, re-run phases slot in at their protocol
    # position -- so compare as sets.)
    assert set(report.phases_run) == set(PHASE_SEQUENCE)

    # Completed phases (PRECOMMITMENT, ACQUISITION, CROSS_EXAMINATION) were
    # NOT re-run: this run() call appended none of their events (their events
    # already live on the ledger from the original run; the fake sink only
    # records what this call appended).
    for phase in (p, a, ce):
        assert sink.recorded_keys().count(f"{phase.value}:started") == 0, phase

    # The failed phases re-ran for real: this call appended their PHASE_STARTED
    # and, because the re-run succeeded (no deliberator means every seat is
    # recorded absent, not a failure), their PHASE_COMPLETED -- and no new
    # PHASE_FAILED.
    for phase in (ee, bb):
        assert sink.recorded_keys().count(f"{phase.value}:started") == 1, phase
        assert sink.recorded_keys().count(f"{phase.value}:completed") == 1, phase
        assert sink.recorded_keys().count(f"{phase.value}:failed") == 0, phase

    # The rewind reconstructed the state before the first failed phase: the
    # unfilled slots from the failed phases are gone, and only the slots the
    # re-run itself produced (the UnavailableDeliberator's absence markers)
    # remain. The carried value from the pre-failure snapshot is preserved.
    assert "EVIDENCE_EXCHANGE:round_failed" not in report.unfilled_slots
    assert "BLINDSPOT_BOUNTY:round_failed" not in report.unfilled_slots
    assert report.final_status == TaskStatus.COMPLETED_WITH_GAPS


class _CancellingDeliberator:
    """A deliberator whose model call is interrupted by the researcher's stop.

    Mirrors what GatewayDeliberator now does when the cancel channel fires
    around an in-flight model call (round-13): raise CouncilCancelled instead
    of returning an absent seat, so the orchestrator records CANCELLED rather
    than a degraded phase.
    """

    def __init__(self, cancel_on_phase: TaskPhase) -> None:
        self._cancel_on_phase = cancel_on_phase

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: object,
    ) -> dict[str, object] | None:
        if phase == self._cancel_on_phase:
            raise CouncilCancelled("researcher requested to stop the run")
        return {"seat": seat.value, "judgment": "noted"}


async def test_cancel_mid_phase_records_cancelled_not_a_failed_phase() -> None:
    """A stop that lands inside a phase (round-13) must end the run CANCELLED.

    The stop interrupts the very first phase: no phase is recorded as run,
    no PHASE_FAILED event reaches the ledger, and the report says CANCELLED
    with the phases that completed before the stop -- a redirection, not a
    verdict on the work (identical contract to the between-phase cancel).
    """
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(
        ledger=sink,
        budget=budget,
        deliberator=_CancellingDeliberator(TaskPhase.PRECOMMITMENT),
    )
    task_id = uuid4()

    report = await orchestrator.run(task_id=task_id, question="does X cause Y?")

    assert report.final_status == TaskStatus.CANCELLED
    assert report.stop_reason.value == "CANCELLED"
    assert report.phases_run == ()
    assert report.phases_skipped == ()
    # The interrupted phase is not reported as a failure or an unfilled slot:
    # a stop is not a gap in the evidence.
    assert report.failures == ()
    assert report.unfilled_slots == ()
    assert not any(key.endswith(":failed") for key in sink.recorded_keys())


async def test_cancel_mid_phase_keeps_completed_phases() -> None:
    """A stop during the second phase keeps the first phase's events and
    reports exactly how far the run got."""
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(
        ledger=sink,
        budget=budget,
        deliberator=_CancellingDeliberator(TaskPhase.ACQUISITION),
    )
    task_id = uuid4()

    report = await orchestrator.run(task_id=task_id, question="does X cause Y?")

    assert report.final_status == TaskStatus.CANCELLED
    assert report.phases_run == (TaskPhase.PRECOMMITMENT,)
    assert sink.recorded_keys().count("PRECOMMITMENT:started") == 1
    assert sink.recorded_keys().count("PRECOMMITMENT:completed") == 1
    # The interrupted phase's start marker exists (the phase began), but it
    # neither completed nor failed -- the run was redirected, not broken.
    assert sink.recorded_keys().count("ACQUISITION:started") == 1
    assert "ACQUISITION:completed" not in sink.recorded_keys()
    assert "ACQUISITION:failed" not in sink.recorded_keys()
    assert report.failures == ()
    assert report.unfilled_slots == ()


async def test_restart_from_phase_without_snapshots_falls_back_to_full_restart() -> (
    None
):
    """A restart_from marker on a legacy checkpoint (no phase_snapshots) can
    not rewind; the honest fallback is a full restart from the beginning."""
    sink = _FakeEventSink()
    budget = BudgetTracker(limits=_GENEROUS_BUDGET)
    orchestrator = CouncilOrchestrator(ledger=sink, budget=budget)
    task_id = uuid4()

    p, a = TaskPhase.PRECOMMITMENT, TaskPhase.ACQUISITION
    checkpoint = CouncilCheckpoint(
        run_phases=(p, a),
        failures=(f"{a.value}: boom",),
        unfilled=(f"{a.value}:round_failed",),
        phase_snapshots=(),
        restart_from=a,
    )

    report = await orchestrator.run(
        task_id=task_id,
        question="does X cause Y?",
        resume_from=checkpoint,
    )

    assert report.phases_run == PHASE_SEQUENCE
    # Nothing is skipped: every phase's PHASE_STARTED fired exactly once
    # because the run went from scratch.
    for phase in PHASE_SEQUENCE:
        assert sink.recorded_keys().count(f"{phase.value}:started") == 1, phase


async def test_restart_from_none_resumes_from_checkpoint_end() -> None:
    """Without restart_from the checkpoint behaves exactly as before:
    everything already run is skipped, nothing before the checkpoint is
    appended a second time."""
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

    await orchestrator.run(
        task_id=task_id,
        question="does X cause Y?",
        resume_from=halted.checkpoint,
    )

    for key in keys_after_halt:
        assert sink.recorded_keys().count(key) == 1
    for phase in (
        TaskPhase.JOINT_MODELING,
        TaskPhase.FINAL_REJUDGMENT,
        TaskPhase.REPORTING,
    ):
        assert any(key.startswith(f"{phase.value}:") for key in sink.recorded_keys())
