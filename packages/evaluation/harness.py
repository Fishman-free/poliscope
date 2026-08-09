"""ForesightBlindspot's five baselines, as configurations of the real council.

Design spec 11.3 asks for five points of comparison, not five separate
implementations: Single-Agent Deep Research, Fixed Multi-Agent Debate,
Council + Linear Context, Council + MemoBrain without an Evidence Engine, and
the full Poliscope. Read as a progression, each adds exactly one capability
over the last -- seat specialisation, then the shared protocol and per-seat
memory, then the evidence gate -- so this module builds all five from the
same three collaborators the production system already has:
:class:`~packages.epistemo.orchestrator.CouncilOrchestrator`,
:class:`~packages.council.deliberation.GatewayDeliberator`, and
:class:`~packages.memory.council_memory.CouncilMemory`. Nothing here
reimplements the protocol; it only chooses which real pieces each variant is
wired from, which is what CLAUDE.md 9's module boundaries and the standing
"wire up, don't duplicate" rule both ask for.

No database is touched. :class:`EvalLedger` is a DB-less stand-in for
:class:`packages.evidence.sql_ledger.SqlEventLedger` plus
:class:`packages.evidence.sql_projector.SqlGraphProjector`'s status
assignment (not its node/edge construction -- the code-computable scores in
:mod:`packages.evaluation.scoring` only need admission status). A baseline
run is therefore deterministic and fast enough to run in a unit test with a
scripted :class:`~packages.models.contracts.ModelGateway`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator, generic_system_prompt
from packages.council.rounds.registry import (
    FindingExtractor,
    SeatDeliberator,
    SourceAcquirer,
)
from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.epistemo.orchestrator import (
    ORDERED_SEATS,
    CouncilOrchestrator,
    TaskRunReport,
)
from packages.evidence.contracts import AdmissionDisposition, ScientificEventCandidate
from packages.evidence.gate import FullEvidenceGate
from packages.evidence.ledger import EventLedger, LedgerEntry
from packages.evidence.sql_projector import (
    LEAD_ONLY_DISPOSITIONS,
    NODE_EVENT_TYPES,
    STATUS_ADMITTED,
    STATUS_PROCESS_ONLY,
    STATUS_QUARANTINED,
)
from packages.kernel.contracts import FrozenDict
from packages.memory.adapter import create_memory_adapter
from packages.memory.contracts import Episode, RecallResult
from packages.memory.council_memory import CouncilMemory
from packages.models.contracts import ModelGateway


class BaselineVariant(StrEnum):
    """The five points on design spec 11.3's comparison ladder, ordered."""

    SINGLE_AGENT = "single_agent_deep_research"
    FIXED_DEBATE = "fixed_multi_agent_debate"
    COUNCIL_LINEAR_CONTEXT = "council_linear_context"
    COUNCIL_MEMOBRAIN_NO_GATE = "council_memobrain_no_evidence_engine"
    FULL_POLISCOPE = "full_poliscope"


class SharedLinearMemoryAdapter:
    """One shared transcript that ignores which seat is asking.

    Models the "Linear Context" baseline: seven seats reading and writing one
    undifferentiated conversation history, the way a single long context
    window would work without MemoBrain's per-seat isolation. Unlike
    :class:`packages.memory.in_memory_adapter.InMemoryMemoryAdapter`, which
    strictly separates one ``agent_id`` from another (CLAUDE.md 3's private
    state requirement), every method here deliberately reads and writes the
    same list regardless of ``agent_id`` -- that convergence is the property
    the baseline exists to demonstrate.
    """

    def __init__(self) -> None:
        self._episodes: list[Episode] = []

    async def init_private_memory(self, agent_id: str, task: str) -> None:
        self._episodes.append(Episode(kind="task", summary=task))

    async def memorize_episode(self, agent_id: str, episode: Episode) -> None:
        self._episodes.append(episode)

    async def recall_private(self, agent_id: str, token_budget: int) -> RecallResult:
        text = " ".join(episode.summary for episode in self._episodes)
        return RecallResult(text=text[:token_budget])

    async def save_snapshot(self, agent_id: str) -> dict[str, object]:
        return {"episodes": [episode.model_dump() for episode in self._episodes]}

    async def load_snapshot(self, agent_id: str, snapshot: dict[str, object]) -> None:
        raw = snapshot.get("episodes", [])
        if not isinstance(raw, (list, tuple)):
            raise ValueError("snapshot has no episode list")
        self._episodes = [Episode.model_validate(item) for item in raw]


class EvalLedger:
    """Deterministic, DB-less ``EventSink`` for the evaluation harness.

    Satisfies :class:`packages.epistemo.orchestrator.EventSink`, so a
    :class:`CouncilOrchestrator` runs against it exactly as it would against
    :class:`packages.evidence.sql_ledger.SqlEventLedger`. Status assignment
    mirrors :func:`packages.evidence.sql_projector.SqlGraphProjector._project_one`
    stage for stage -- process-only, quarantined, lead-only, or admitted --
    with one deliberate difference: when ``gate`` is ``None`` every formal
    node event is admitted unconditionally, modelling design spec 11.3's "no
    Evidence Engine" baselines. That is a real behavioural difference the
    evaluator needs, not a duplicate of the production dispatch, which never
    runs without a gate.
    """

    def __init__(self, gate: FullEvidenceGate | None = None) -> None:
        self._ledger = EventLedger()
        self._gate = gate

    def list_for_task(self, task_id: UUID) -> list[LedgerEntry]:
        return self._ledger.list_for_task(task_id)

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
    ) -> LedgerEntry:
        entry = self._ledger.append(
            task_id, event_type, payload, idempotency_key, status
        )
        final_status = await self._finalize_status(
            entry, evidence_level, source_id, finding_id, claim_id
        )
        if final_status != entry.status:
            entry = self._ledger.set_status(entry.event_id, final_status)
        return entry

    async def _finalize_status(
        self,
        entry: LedgerEntry,
        evidence_level: str | None,
        source_id: UUID | None,
        finding_id: UUID | None,
        claim_id: UUID | None,
    ) -> str:
        if entry.event_type not in NODE_EVENT_TYPES:
            # CLAUDE.md 5.1: a process event never automatically becomes
            # evidence, gate or no gate.
            return STATUS_PROCESS_ONLY
        if self._gate is None:
            return STATUS_ADMITTED
        candidate = ScientificEventCandidate(
            id=entry.event_id,
            task_id=entry.task_id,
            event_type=entry.event_type,
            payload=FrozenDict(entry.payload),
            evidence_level=evidence_level,
            source_id=source_id,
            finding_id=finding_id,
            claim_id=claim_id,
        )
        decision = await self._gate.audit(candidate)
        if decision.disposition == AdmissionDisposition.QUARANTINE:
            return STATUS_QUARANTINED
        if decision.disposition in LEAD_ONLY_DISPOSITIONS:
            return decision.disposition.value.lower()
        return STATUS_ADMITTED


def _gate_for(variant: BaselineVariant) -> FullEvidenceGate | None:
    """Only the full system gates. Every earlier rung is ungated by design."""
    return FullEvidenceGate() if variant is BaselineVariant.FULL_POLISCOPE else None


def _memory_for(variant: BaselineVariant, task_id: UUID) -> CouncilMemory | None:
    if variant in (BaselineVariant.SINGLE_AGENT, BaselineVariant.FIXED_DEBATE):
        return None
    if variant is BaselineVariant.COUNCIL_LINEAR_CONTEXT:
        return CouncilMemory(SharedLinearMemoryAdapter(), task_id)
    return CouncilMemory(create_memory_adapter(), task_id)


def _seats_for(variant: BaselineVariant) -> tuple[Seat, ...]:
    # Single-Agent Deep Research is one researcher, not seven -- the whole
    # point of the comparison is what a lone agent misses. The theory builder
    # is the generic researcher standing in for that lone agent: ORDERED_SEATS
    # is alphabetical and its first member is the adversarial falsifier,
    # whose specialised role is exactly what a single researcher is not -- and
    # whose two BLINDSPOT_BOUNTY nominations in the demo case (see
    # packages/evaluation/demo_case.py's DemoGateway, which scripts one
    # specialist blindspot per seat) would hand a single-agent run two
    # falsification-flavoured gold matches instead of the theory builder's
    # one generic mechanism blindspot.
    if variant is BaselineVariant.SINGLE_AGENT:
        return (Seat.THEORY_BUILDER,)
    return ORDERED_SEATS


def generic_debate_deliberator(
    gateway: ModelGateway,
    budget: BudgetTracker | None = None,
) -> GatewayDeliberator:
    """The undifferentiated deliberator shared by the two non-council baselines.

    Reuses :class:`GatewayDeliberator` wholesale via its injectable
    ``system_prompt`` parameter rather than re-implementing request shaping,
    budget consumption, and schema-quarantine handling a second time.
    """
    return GatewayDeliberator(gateway, budget, system_prompt=generic_system_prompt)


def _deliberator_for(
    variant: BaselineVariant,
    gateway: ModelGateway,
    budget: BudgetTracker | None,
) -> SeatDeliberator:
    if variant in (BaselineVariant.SINGLE_AGENT, BaselineVariant.FIXED_DEBATE):
        return generic_debate_deliberator(gateway, budget)
    return GatewayDeliberator(gateway, budget)


_DEFAULT_BUDGET = ResearchBudget(
    wall_clock_minutes=90,
    model_cost_usd=Decimal("25"),
    tool_call_limit=200,
    source_limit=80,
)


@dataclass(frozen=True, slots=True)
class BaselineOutcome:
    """What one baseline run produced, ready for :mod:`packages.evaluation.scoring`."""

    variant: BaselineVariant
    task_id: UUID
    report: TaskRunReport
    events: tuple[LedgerEntry, ...]
    budget: BudgetTracker


async def run_baseline(
    variant: BaselineVariant,
    question: str,
    gateway: ModelGateway,
    *,
    budget: ResearchBudget | None = None,
    task_id: UUID | None = None,
    confirmed_claims: tuple[UUID, ...] = (),
    acquirer: SourceAcquirer | None = None,
    finding_extractor: FindingExtractor | None = None,
) -> BaselineOutcome:
    """Run one of the five baselines end to end, against no database.

    Each variant is one configuration of the real orchestrator; see the module
    docstring for which of seat specialisation, memory, and the evidence gate
    each rung adds. ``acquirer``/``finding_extractor`` are optional and shared
    across every variant unchanged -- design spec 11.3 varies seat
    specialisation, memory, and the gate across the five baselines, not how
    sources are fetched, so the same scripted tool-layer stand-in is the
    correct thing to hand every rung of the ladder.
    """
    resolved_task_id = task_id if task_id is not None else uuid4()
    tracker = BudgetTracker(limits=budget if budget is not None else _DEFAULT_BUDGET)
    ledger = EvalLedger(gate=_gate_for(variant))
    orchestrator = CouncilOrchestrator(
        ledger=ledger,
        budget=tracker,
        deliberator=_deliberator_for(variant, gateway, tracker),
        seats=_seats_for(variant),
        memory=_memory_for(variant, resolved_task_id),
        acquirer=acquirer,
        finding_extractor=finding_extractor,
    )
    report = await orchestrator.run(
        resolved_task_id, question, confirmed_claims=confirmed_claims
    )
    return BaselineOutcome(
        variant=variant,
        task_id=resolved_task_id,
        report=report,
        events=tuple(ledger.list_for_task(resolved_task_id)),
        budget=tracker,
    )


__all__ = [
    "BaselineOutcome",
    "BaselineVariant",
    "EvalLedger",
    "SharedLinearMemoryAdapter",
    "generic_debate_deliberator",
    "run_baseline",
]
