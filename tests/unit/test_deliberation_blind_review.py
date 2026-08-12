"""Blind evidence review (CLAUDE.md 7.4, design spec 7.9, mechanism 2 of 4).

A seat judging evidence must not see the author's identity, the venue, or a
citation count -- any of those can substitute a reputation halo for the
method-quality judgment CLAUDE.md 3 assigns each seat. No round handler in
``packages/council/rounds/registry.py`` writes one of these keys into
``PhaseOutcome.carry`` today, so this test does not catch a live leak; it
locks in a structural guarantee that GatewayDeliberator would still hide such
a key were one ever added, by exercising the real request-building path
(GatewayDeliberator -> ModelRequest.messages) rather than a private helper.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator
from packages.council.rounds.registry import PhaseContext, UnavailableDeliberator
from packages.epistemo.contracts import TaskPhase
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus


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


def _context(
    carried: dict[str, object],
    phase: TaskPhase = TaskPhase.PRECOMMITMENT,
) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=phase,
        seats=(Seat.CAUSAL_SCIENTIST,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(),
        deliberator=UnavailableDeliberator(),
        carried=carried,
    )


async def test_bibliographic_identity_keys_never_reach_the_model_request() -> None:
    gateway = _RecordingGateway()
    deliberator = GatewayDeliberator(gateway)
    carried = {
        "author": "Jane Doe",
        "journal": "Nature",
        "citation_count": 9001,
        "legitimate_finding_summary": "screen time correlates with anxiety",
    }

    result = await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(carried)
    )

    assert result is not None
    assert len(gateway.requests) == 1
    user_message = gateway.requests[0].messages[1].content
    assert "Jane Doe" not in user_message
    assert "Nature" not in user_message
    assert "9001" not in user_message
    assert "legitimate_finding_summary" in user_message
    assert "screen time correlates with anxiety" in user_message


async def test_case_insensitive_and_plural_variants_are_also_blocked() -> None:
    gateway = _RecordingGateway()
    deliberator = GatewayDeliberator(gateway)
    carried: dict[str, object] = {
        "Author": "Should still be blocked",
        "AUTHORS": "Also blocked",
        "Journal": "Also blocked",
    }

    await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(carried)
    )

    user_message = gateway.requests[0].messages[1].content
    assert "Should still be blocked" not in user_message
    assert "Also blocked" not in user_message


async def test_phase_carry_is_rendered_only_for_its_scientific_consumer() -> None:
    source_id = uuid4()
    carried: dict[str, object] = {
        "available_sources": (
            {"source_id": str(source_id), "title": "Acquired paper", "level": "B"},
        ),
        "published_evidence": (
            {
                "seat": Seat.EVIDENCE_AUDITOR.value,
                "source_id": str(source_id),
                "anchor_summary": "metadata-only source",
                "level": "B",
            },
        ),
        "ranked_blindspots": (
            {
                "blindspot_id": str(uuid4()),
                "statement": "A confound remains untested",
                "score": "0.77",
                "rank": 1,
                "status": "pending_investigation",
            },
        ),
        "blindspot_assignments": (),
    }

    prompts: dict[TaskPhase, str] = {}
    for phase in (
        TaskPhase.EVIDENCE_EXCHANGE,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.JOINT_MODELING,
    ):
        gateway = _RecordingGateway()
        result = await GatewayDeliberator(gateway).deliberate(
            Seat.CAUSAL_SCIENTIST, phase, _context(carried, phase)
        )
        assert result is not None
        prompts[phase] = gateway.requests[0].messages[1].content

    assert "Acquired paper" in prompts[TaskPhase.EVIDENCE_EXCHANGE]
    assert "metadata-only source" not in prompts[TaskPhase.EVIDENCE_EXCHANGE]
    assert "A confound remains untested" not in prompts[TaskPhase.EVIDENCE_EXCHANGE]

    assert "metadata-only source" in prompts[TaskPhase.CROSS_EXAMINATION]
    assert "Acquired paper" not in prompts[TaskPhase.CROSS_EXAMINATION]
    assert "A confound remains untested" not in prompts[TaskPhase.CROSS_EXAMINATION]

    assert "A confound remains untested" in prompts[TaskPhase.JOINT_MODELING]
    assert "待调查" in prompts[TaskPhase.JOINT_MODELING]
    assert "Acquired paper" not in prompts[TaskPhase.JOINT_MODELING]
    assert "metadata-only source" not in prompts[TaskPhase.JOINT_MODELING]
