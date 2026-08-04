"""Human guidance isolation at the JOINT_MODELING checkpoint (CLAUDE.md 4/8).

Plan phase 8.3: the researcher's advisory steer, collected between
BLINDSPOT_BOUNTY and JOINT_MODELING, must reach the model's prompt in exactly
one phase and be plainly labelled as non-scientific there -- never mistaken
for an eighth seat's judgment, and never carried into any other phase's
prompt. ``packages/epistemo/orchestrator.py`` passes ``council_guidance`` into
every phase's ``PhaseContext.guidance`` unconditionally (see its docstring),
so the isolation guarantee lives entirely in
``packages/council/deliberation.py::_user_prompt``'s own conditional guard --
this test exercises that guard through the real request-building path
(GatewayDeliberator -> ModelRequest.messages), the same way
``test_deliberation_blind_review.py`` locks in the blind-review guard.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator
from packages.council.rounds.registry import (
    PhaseContext,
    UnavailableDeliberator,
    run_final_rejudgment,
    run_joint_modeling,
)
from packages.epistemo.contracts import TaskPhase
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus

_GUIDANCE_MARKER = "[研究者方向性备注，非科学判断]"
_GUIDANCE_TEXT = "重点核查跨国样本的测量等价性"


class _RecordingGateway:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        return ModelResult(
            call_id=uuid4(),
            payload={"initial_judgment": "noted", "confidence": 0.5},
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal("0"),
            latency_ms=0,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


def _context(phase: TaskPhase, guidance: str | None) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=phase,
        seats=(Seat.CAUSAL_SCIENTIST,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(),
        deliberator=UnavailableDeliberator(),
        guidance=guidance,
    )


async def test_guidance_is_rendered_in_joint_modeling_prompt() -> None:
    gateway = _RecordingGateway()
    deliberator = GatewayDeliberator(gateway)

    result = await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST,
        TaskPhase.JOINT_MODELING,
        _context(TaskPhase.JOINT_MODELING, _GUIDANCE_TEXT),
    )

    assert result is not None
    user_message = gateway.requests[0].messages[1].content
    assert f"{_GUIDANCE_MARKER}: {_GUIDANCE_TEXT}" in user_message


async def test_none_or_empty_guidance_renders_no_line_even_in_joint_modeling() -> None:
    for guidance in (None, ""):
        gateway = _RecordingGateway()
        deliberator = GatewayDeliberator(gateway)

        await deliberator.deliberate(
            Seat.CAUSAL_SCIENTIST,
            TaskPhase.JOINT_MODELING,
            _context(TaskPhase.JOINT_MODELING, guidance),
        )

        user_message = gateway.requests[0].messages[1].content
        assert _GUIDANCE_MARKER not in user_message


async def test_guidance_never_leaks_into_any_other_phase_prompt() -> None:
    other_phases = (
        TaskPhase.PRECOMMITMENT,
        TaskPhase.ACQUISITION,
        TaskPhase.EVIDENCE_EXCHANGE,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.BLINDSPOT_BOUNTY,
        TaskPhase.FINAL_REJUDGMENT,
    )
    for phase in other_phases:
        gateway = _RecordingGateway()
        deliberator = GatewayDeliberator(gateway)

        # Same non-empty guidance text the JOINT_MODELING test above proves
        # *does* render -- only the phase differs, isolating the guard.
        await deliberator.deliberate(
            Seat.CAUSAL_SCIENTIST,
            phase,
            _context(phase, _GUIDANCE_TEXT),
        )

        user_message = gateway.requests[0].messages[1].content
        assert _GUIDANCE_MARKER not in user_message
        assert _GUIDANCE_TEXT not in user_message


class _ScriptedDeliberator:
    """Returns fixed structured output no matter what the prompt contains.

    Stands in for a real model: since it never reads ``context.guidance`` (or
    anything derived from it), any difference between a guided and an
    unguided run of ``run_joint_modeling``/``run_final_rejudgment`` can only
    come from those functions themselves reading ``context.guidance`` -- which
    neither does (see registry.py; both build their output purely from
    ``_collect()``'s per-seat mapping).
    """

    def __init__(self, outputs: Mapping[Seat, Mapping[str, object]]) -> None:
        self._outputs = outputs

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        return self._outputs.get(seat)


def _joint_modeling_context(
    claim_id: UUID, opposition_id: UUID, guidance: str | None
) -> PhaseContext:
    output = {
        "strongest_opposition_refs": [str(opposition_id)],
        "falsification_conditions": ["A null effect in a preregistered RCT."],
        "boundary_conditions": ["Western adolescent samples only."],
        "unresolved_conflicts": ["Effect direction across sexes."],
    }
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.JOINT_MODELING,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(claim_id,),
        deliberator=_ScriptedDeliberator({Seat.THEORY_BUILDER: output}),
        guidance=guidance,
    )


def _final_rejudgment_context(claim_id: UUID, guidance: str | None) -> PhaseContext:
    outputs = {
        Seat.THEORY_BUILDER: {
            "final_judgment": "机制成立，但样本局限于西方青少年。",
            "confidence": 0.6,
        },
        Seat.ADVERSARY_FALSIFIER: {
            "final_judgment": "反例证据不足以推翻，但保留异议。",
            "confidence": 0.4,
        },
    }
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.FINAL_REJUDGMENT,
        seats=(Seat.THEORY_BUILDER, Seat.ADVERSARY_FALSIFIER),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(claim_id,),
        deliberator=_ScriptedDeliberator(outputs),
        guidance=guidance,
    )


def _strip_task_scoped_ids(outcome: object) -> list[tuple[str, object, str | None]]:
    """Reduce a PhaseOutcome to what should be guidance-invariant.

    ``task_id`` differs between the two contexts on purpose (each run gets
    its own), so idempotency keys (which embed no id here beyond seat/claim
    already compared via payload) are compared as event_type/payload/
    evidence_level triples rather than raw equality on the dataclass.
    """
    return [
        (event.event_type, event.payload, event.evidence_level)
        for event in outcome.events  # type: ignore[attr-defined]
    ]


async def test_joint_modeling_outcome_is_identical_with_and_without_guidance() -> None:
    """Plan phase 8.3's explicit mandate: same evidence, one run guided, one
    not -- the DebateCapsule/consensus/confidence-marker events this phase
    produces must be byte-for-byte identical, proving the human's advisory
    text cannot influence Evidence-Gate-adjacent structured output, only the
    prompt text a model never gets to see in this fake-deliberator setup."""
    claim_id = uuid4()
    opposition_id = uuid4()

    unguided = await run_joint_modeling(
        _joint_modeling_context(claim_id, opposition_id, None)
    )
    guided = await run_joint_modeling(
        _joint_modeling_context(claim_id, opposition_id, "重点关注跨文化边界条件")
    )

    assert _strip_task_scoped_ids(unguided) == _strip_task_scoped_ids(guided)
    assert unguided.unfilled_slots == guided.unfilled_slots
    assert unguided.absent_seats == guided.absent_seats


async def test_final_rejudgment_outcome_is_identical_with_and_without_guidance() -> (
    None
):
    """Same property one phase later: FINAL_REJUDGMENT's judgments and any
    DissentCertificate it issues must not depend on whether JOINT_MODELING's
    checkpoint carried a human steer."""
    claim_id = uuid4()

    unguided = await run_final_rejudgment(_final_rejudgment_context(claim_id, None))
    guided = await run_final_rejudgment(
        _final_rejudgment_context(claim_id, "重点关注跨文化边界条件")
    )

    assert _strip_task_scoped_ids(unguided) == _strip_task_scoped_ids(guided)
    assert unguided.unfilled_slots == guided.unfilled_slots
    assert unguided.absent_seats == guided.absent_seats
