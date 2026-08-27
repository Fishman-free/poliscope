"""Paper-understanding injection from the PRECOMMITMENT phase (round-7).

A paper_review task's paper-understanding summary must reach the seven seats
from the very first protocol phase. Before this fix, ``_PAPER_PHASES`` in
``packages/council/deliberation.py::_user_prompt`` excluded PRECOMMITMENT, so
the opening round was fed only the placeholder review question and every seat
honestly reported "no paper was provided" -- which read as a failure even
though the paper had been read and understood. This test locks the guard so a
paper-understanding dict renders into the PRECOMMITMENT prompt, while a
deep_research task (no paper) still renders nothing there.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator
from packages.council.rounds.registry import PhaseContext, UnavailableDeliberator
from packages.epistemo.contracts import TaskPhase
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus

_PAPER_MARKER = "论文理解"
_PAPER_TITLE = "屏幕时间与青少年抑郁的纵向研究"
_PAPER_QUESTION = "屏幕时间是否导致青少年抑郁？"


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


def _paper_understanding() -> dict[str, object]:
    return {
        "title": _PAPER_TITLE,
        "research_question": _PAPER_QUESTION,
        "main_claims": [
            {
                "statement": "屏幕时间与抑郁存在正相关",
                "supporting_evidence": ["r=0.25"],
            }
        ],
    }


def _context(phase: TaskPhase, paper: dict[str, object] | None) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=phase,
        seats=(Seat.CAUSAL_SCIENTIST,),
        question="审查论文的论证严谨性与证据充分性",
        confirmed_claims=(),
        deliberator=UnavailableDeliberator(),
        paper_understanding=paper,
    )


async def test_paper_understanding_renders_in_precommitment_prompt() -> None:
    gateway = _RecordingGateway()
    deliberator = GatewayDeliberator(gateway)

    await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST,
        TaskPhase.PRECOMMITMENT,
        _context(TaskPhase.PRECOMMITMENT, _paper_understanding()),
    )

    user_message = gateway.requests[0].messages[1].content
    assert _PAPER_MARKER in user_message
    assert _PAPER_TITLE in user_message
    assert _PAPER_QUESTION in user_message


async def test_no_paper_understanding_renders_no_line_in_precommitment() -> None:
    gateway = _RecordingGateway()
    deliberator = GatewayDeliberator(gateway)

    await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST,
        TaskPhase.PRECOMMITMENT,
        _context(TaskPhase.PRECOMMITMENT, None),
    )

    user_message = gateway.requests[0].messages[1].content
    assert _PAPER_MARKER not in user_message
    assert _PAPER_TITLE not in user_message
