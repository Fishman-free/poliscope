"""Process-stream 事件的席位归属（实时进展页「尚无输出」bug 的回归测试）。

Regression test for a real production incident: the worker relayed streaming
deltas with only ``{"text": ...}``, so the live view could not attribute a
thinking slice to any scientist -- every seat rendered as "no output yet"
even while the model was streaming 9000+ reasoning rows into
``process_stream``. The relay now stamps every delta with the seat and phase
it belongs to.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator
from packages.council.rounds.registry import PhaseContext
from packages.epistemo.contracts import TaskPhase
from packages.models.contracts import (
    ModelRequest,
    ModelResult,
    SchemaStatus,
    StreamEvent,
)


class _StreamingGateway:
    """A provider that streams a fixed event sequence."""

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events

    async def stream_invoke(
        self,
        request: ModelRequest,
        on_event: object,
    ) -> ModelResult:
        for event in self._events:
            await on_event(event)  # type: ignore[operator]
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

    async def invoke(self, request: ModelRequest) -> ModelResult:
        raise AssertionError("stream_invoke must be used when configured")


def _context(deliberator: GatewayDeliberator) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.PRECOMMITMENT,
        seats=(Seat.CAUSAL_SCIENTIST,),
        question="question",
        confirmed_claims=(),
        deliberator=deliberator,
        carried={},
    )


async def test_relayed_deltas_carry_seat_and_phase() -> None:
    streamed: list[dict[str, object]] = []
    deliberator = GatewayDeliberator(
        _StreamingGateway(
            [
                StreamEvent(kind="reasoning", text="先检查反向因果，"),
                StreamEvent(kind="token", text='{"initial_judgment": "x"}'),
                StreamEvent(kind="done"),
            ]
        ),
        on_process=lambda kind, payload: streamed.append({"kind": kind, **payload}),
    )
    context = _context(deliberator)

    result = await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, context
    )
    assert result is not None

    reasoning = next(item for item in streamed if item["kind"] == "model_reasoning")
    assert reasoning["seat"] == Seat.CAUSAL_SCIENTIST.value
    assert reasoning["phase"] == TaskPhase.PRECOMMITMENT.value
    assert reasoning["text"] == "先检查反向因果，"

    token = next(item for item in streamed if item["kind"] == "model_token")
    assert token["seat"] == Seat.CAUSAL_SCIENTIST.value

    done = next(item for item in streamed if item["kind"] == "model_done")
    assert done["seat"] == Seat.CAUSAL_SCIENTIST.value
    assert done["phase"] == TaskPhase.PRECOMMITMENT.value
