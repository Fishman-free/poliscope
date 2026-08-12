"""「思考已结束但前端永远显示思考中…」回归测试（round-15 生产故障）。

前端 LiveView 的座位状态由过程事件推导：``seat_deliberation`` 开启一段
（running=true），``model_done`` 结束它（running=false）。一旦某次模型
调用结束后没有发出任何结束事件，该座位就会永久显示「思考中… 已等待
Ns」——elapsed 冻结在心跳停止时的值（用户报告：boundary_scientist 已
结束思考却显示「思考中… 已等待 105s」）。

两条失败路径曾经不发射结束事件：

1. **调用失败（缺席）**。``deliberate()`` 的 except 分支把 gateway 异常
   降级为缺席（返回 None），但过程流上只有 ``seat_deliberation``，没有
   结束事件 —— 前端无从知道这段思考已经结束。
2. **研究者停止（CouncilCancelled）**。停止请求中断在飞调用，异常直接
   向上传播 —— 同样没有结束事件。

修复：两条路径补发 ``seat_absent``（携带原因），与账本 SEAT_UNAVAILABLE
语义一致但走过程流，前端把座位收敛为「缺席」而不是悬在「思考中」。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator
from packages.council.rounds.registry import PhaseContext
from packages.epistemo.contracts import CouncilCancelled, TaskPhase
from packages.models.contracts import (
    ModelRequest,
    ModelResult,
    SchemaStatus,
    StreamEvent,
)


class _FailingGateway:
    """A provider whose every path fails (a real outage, not a hiccup)."""

    async def stream_invoke(
        self, request: ModelRequest, on_event: object
    ) -> ModelResult:
        raise RuntimeError("vendor 500")

    async def invoke(self, request: ModelRequest) -> ModelResult:
        raise RuntimeError("vendor 500")


class _HangingGateway:
    """A provider whose call hangs until the stop lands (as a stalled vendor
    does in production) — the cancel poll must get a chance to fire."""

    async def invoke(self, request: ModelRequest) -> ModelResult:
        return await self.stream_invoke(request, on_event=lambda _event: None)

    async def stream_invoke(
        self, request: ModelRequest, on_event: object
    ) -> ModelResult:
        await asyncio.Event().wait()  # pragma: no cover - cancelled first
        raise AssertionError("unreachable")  # pragma: no cover


class _StreamFailsInvokeSucceeds:
    """The fallback path: streaming dies, the plain invoke recovers."""

    async def stream_invoke(
        self, request: ModelRequest, on_event: object
    ) -> ModelResult:
        raise RuntimeError("stream hiccup")

    async def invoke(self, request: ModelRequest) -> ModelResult:
        return ModelResult(
            call_id=uuid4(),
            payload={"initial_judgment": "noted", "confidence": 0.5},
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal("0"),
            latency_ms=0,
            retries=1,
            schema_status=SchemaStatus.OK,
        )


class _InstantGateway:
    """A provider that streams and finishes normally."""

    async def stream_invoke(
        self, request: ModelRequest, on_event: object
    ) -> ModelResult:
        await on_event(  # type: ignore[operator]
            StreamEvent(kind="token", text='{"initial_judgment": "x"}')
        )
        await on_event(StreamEvent(kind="done"))  # type: ignore[operator]
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


def _captured(
    streamed: list[dict[str, Any]], seat: Seat = Seat.CAUSAL_SCIENTIST
) -> list[dict[str, Any]]:
    return [item for item in streamed if item.get("seat") == seat.value]


async def test_failed_model_call_emits_seat_absent_not_silence() -> None:
    """An absent seat must close its thinking slice, not leave it open.

    Without the fix the stream holds only ``seat_deliberation`` and the live
    view's ``seatStreams`` keeps ``running`` true forever — the 「思考中…
    已等待 105s」 production failure.
    """
    streamed: list[dict[str, Any]] = []
    deliberator = GatewayDeliberator(
        _FailingGateway(),
        on_process=lambda kind, payload: streamed.append({"kind": kind, **payload}),
    )

    result = await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(deliberator)
    )
    assert result is None

    events = _captured(streamed)
    assert events[0]["kind"] == "seat_deliberation"
    # The slice must close with seat_absent carrying the honest reason.
    assert events[-1]["kind"] == "seat_absent"
    assert events[-1]["phase"] == TaskPhase.PRECOMMITMENT.value
    assert "vendor 500" in str(events[-1]["reason"])
    assert all(item["kind"] != "model_done" for item in events)


async def test_cancelled_model_call_emits_seat_absent() -> None:
    """A researcher stop mid-call closes the slice too (task ends CANCELLED)."""
    streamed: list[dict[str, Any]] = []
    checks = 0

    async def cancel_check() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    deliberator = GatewayDeliberator(
        _HangingGateway(),
        on_process=lambda kind, payload: streamed.append({"kind": kind, **payload}),
        cancel_check=cancel_check,
    )

    with pytest.raises(CouncilCancelled):
        await deliberator.deliberate(
            Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(deliberator)
        )

    events = _captured(streamed)
    assert events[-1]["kind"] == "seat_absent"
    assert events[-1]["phase"] == TaskPhase.PRECOMMITMENT.value
    assert "stop" in str(events[-1]["reason"]).lower()


async def test_successful_stream_emits_model_done_not_seat_absent() -> None:
    """The happy path keeps its existing vocabulary — no regression."""
    streamed: list[dict[str, Any]] = []
    deliberator = GatewayDeliberator(
        _InstantGateway(),
        on_process=lambda kind, payload: streamed.append({"kind": kind, **payload}),
    )

    result = await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(deliberator)
    )
    assert result is not None

    events = _captured(streamed)
    assert events[-1]["kind"] == "model_done"
    assert all(item["kind"] != "seat_absent" for item in events)


async def test_fallback_invoke_success_still_emits_model_done() -> None:
    """Stream failure + invoke recovery must keep closing with model_done."""
    streamed: list[dict[str, Any]] = []
    deliberator = GatewayDeliberator(
        _StreamFailsInvokeSucceeds(),
        on_process=lambda kind, payload: streamed.append({"kind": kind, **payload}),
    )

    result = await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(deliberator)
    )
    assert result is not None

    events = _captured(streamed)
    assert events[-1]["kind"] == "model_done"
    assert all(item["kind"] != "seat_absent" for item in events)
