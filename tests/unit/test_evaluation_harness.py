"""Unit tests for the five ForesightBlindspot baselines and their DB-less ledger.

Design spec 11.3 asks each baseline to differ from the next by exactly one
capability -- seat count, prompt specialisation, memory sharing, or the
evidence gate. These tests assert those specific differences rather than just
"a baseline runs", since a baseline that runs but does not actually
differentiate from its neighbour on the ladder would defeat the comparison's
whole purpose.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.deliberation import _system_prompt, generic_system_prompt
from packages.epistemo.contracts import TaskPhase
from packages.epistemo.orchestrator import ORDERED_SEATS
from packages.evaluation.harness import (
    BaselineVariant,
    EvalLedger,
    SharedLinearMemoryAdapter,
    _seats_for,
    run_baseline,
)
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.gate import FullEvidenceGate
from packages.evidence.sql_projector import (
    STATUS_ADMITTED,
    STATUS_PROCESS_ONLY,
    STATUS_QUARANTINED,
)
from packages.kernel.contracts import FrozenDict
from packages.memory.council_memory import CouncilMemory
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus

# --- _seats_for -------------------------------------------------------------


def test_seats_for_single_agent_is_exactly_one_seat() -> None:
    assert _seats_for(BaselineVariant.SINGLE_AGENT) == (ORDERED_SEATS[0],)


def test_seats_for_every_other_variant_is_all_seven_seats() -> None:
    for variant in (
        BaselineVariant.FIXED_DEBATE,
        BaselineVariant.COUNCIL_LINEAR_CONTEXT,
        BaselineVariant.COUNCIL_MEMOBRAIN_NO_GATE,
        BaselineVariant.FULL_POLISCOPE,
    ):
        seats = _seats_for(variant)
        assert seats == ORDERED_SEATS
        assert len(seats) == 7


# --- generic vs. specialised prompts ----------------------------------------


def test_generic_system_prompt_is_identical_across_seats() -> None:
    a = generic_system_prompt(Seat.THEORY_BUILDER, TaskPhase.PRECOMMITMENT)
    b = generic_system_prompt(Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT)
    assert a == b


def test_specialised_system_prompt_differs_across_seats() -> None:
    a = _system_prompt(Seat.THEORY_BUILDER, TaskPhase.PRECOMMITMENT)
    b = _system_prompt(Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT)
    assert a != b


# --- SharedLinearMemoryAdapter converges seats ------------------------------


async def test_shared_linear_memory_gives_every_seat_the_same_recall() -> None:
    adapter = SharedLinearMemoryAdapter()
    task_id = uuid4()
    memory = CouncilMemory(adapter, task_id)
    seats = ORDERED_SEATS
    await memory.open(seats, "does screen time cause depressive symptoms?")
    await memory.remember(
        Seat.THEORY_BUILDER, "note", "a mechanism proposed by theory_builder"
    )
    await memory.remember(
        Seat.ADVERSARY_FALSIFIER, "note", "a rebuttal proposed by adversarial_falsifier"
    )
    recalled = await memory.recall(seats)
    distinct_texts = set(recalled.values())
    # The whole point of the "Linear Context" baseline: every seat reads back
    # one shared, undifferentiated transcript rather than its own private state.
    assert len(distinct_texts) == 1


# --- EvalLedger gate integration --------------------------------------------


async def test_eval_ledger_process_event_is_never_evidence_regardless_of_gate() -> None:
    for gate in (None, FullEvidenceGate()):
        ledger = EvalLedger(gate=gate)
        entry = await ledger.append(
            uuid4(), "PHASE_STARTED", {"phase": "PRECOMMITMENT"}, "phase-started-1"
        )
        assert entry.status == STATUS_PROCESS_ONLY


async def test_eval_ledger_without_gate_admits_every_formal_event() -> None:
    ledger = EvalLedger(gate=None)
    task_id = uuid4()
    entry = await ledger.append(
        task_id,
        EvidenceNodeType.CLAIM.value,
        {"claim_type": "causal", "study_design": "cross_sectional"},
        "claim-1",
        evidence_level="A",
        claim_id=uuid4(),
    )
    # A disallowed causal/cross_sectional pair would be quarantined by
    # FullEvidenceGate -- the "no Evidence Engine" baselines admit it anyway,
    # which is exactly the gap in reliability design spec 11.3 exists to expose.
    assert entry.status == STATUS_ADMITTED


async def test_eval_ledger_with_full_gate_quarantines_causal_overclaim() -> None:
    ledger = EvalLedger(gate=FullEvidenceGate())
    task_id = uuid4()
    entry = await ledger.append(
        task_id,
        EvidenceNodeType.CLAIM.value,
        {"claim_type": "causal", "study_design": "cross_sectional"},
        "claim-1",
        evidence_level="A",
        claim_id=uuid4(),
    )
    assert entry.status == STATUS_QUARANTINED


async def test_eval_ledger_with_full_gate_admits_a_valid_claim() -> None:
    ledger = EvalLedger(gate=FullEvidenceGate())
    task_id = uuid4()
    entry = await ledger.append(
        task_id,
        EvidenceNodeType.CLAIM.value,
        {"claim_type": "correlational", "study_design": "cross_sectional"},
        "claim-1",
        evidence_level="A",
        claim_id=uuid4(),
    )
    assert entry.status == STATUS_ADMITTED


# --- run_baseline end-to-end smoke test -------------------------------------


class _PrecommitmentOnlyGateway:
    """Answers PRECOMMITMENT only; every other phase gets an empty payload.

    Mirrors ``scripts/seed_demo_task.py``'s own fallback (``return {}``) for
    phases it does not script -- the round runners already treat a missing key
    as an honest unfilled slot, not an error, so this is a legitimate minimal
    gateway rather than a shortcut around real behaviour.
    """

    async def invoke(self, request: ModelRequest) -> ModelResult:
        payload: dict[str, object] = {}
        if TaskPhase(request.purpose) is TaskPhase.PRECOMMITMENT:
            payload = {
                "initial_judgment": f"{request.actor}: correlational evidence only.",
                "confidence": 0.4,
                "update_condition": "a preregistered RCT with adequate power.",
            }
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            input_tokens=10,
            output_tokens=10,
            cost_usd=Decimal("0.01"),
            latency_ms=5,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


async def test_run_baseline_single_agent_produces_a_research_question_event() -> None:
    outcome = await run_baseline(
        BaselineVariant.SINGLE_AGENT,
        "does reducing screen time lower depressive symptoms?",
        _PrecommitmentOnlyGateway(),
    )
    assert outcome.report.task_id == outcome.task_id
    research_questions = [
        entry
        for entry in outcome.events
        if entry.event_type == EvidenceNodeType.RESEARCH_QUESTION.value
    ]
    assert len(research_questions) == 1
    # SINGLE_AGENT is ungated (only FULL_POLISCOPE gates), so the one formal
    # event this run can produce is admitted unconditionally.
    assert research_questions[0].status == STATUS_ADMITTED
