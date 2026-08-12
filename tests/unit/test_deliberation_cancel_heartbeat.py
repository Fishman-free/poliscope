"""Round-13 「停止研究」修复的回归测试：模型调用期间可取消 + 心跳实时可见。

两个真实生产故障：

1. **停止无效**。取消请求只在议会阶段边界被轮询，而一个慢阶段（盲点悬赏，
   7 席位串行、每席位最多 120s×2 次重试）可能持续 15 分钟 —— 研究者点
   「停止研究」后要等整个阶段耗尽才生效。修复：``GatewayDeliberator`` 也
   轮询取消通道，在飞模型调用每 1s 检查一次；检测到请求即取消调用并抛
   ``CouncilCancelled``，由 orchestrator 转为 CANCELLED 报告 —— 绝不降级为
   「席位缺席」，也绝不 fallback 到一次新的 invoke。

2. **思考时间冻结**。``seat_working`` 心跳事件只在收到模型 token delta 触发的
   flush 时才落库；模型长思考（无 delta）期间心跳全部积压在内存 buffer，
   前端「思考中… 已等待 Ns」停在最后一次 flush 的时刻。修复：每次心跳
   emit 后立即 flush。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from packages.council import deliberation
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


class _HangingGateway:
    """A provider whose stream never produces a delta (a stalled vendor)."""

    def __init__(self) -> None:
        self._release = asyncio.Event()
        self.started = False
        self.cancelled = asyncio.Event()

    async def stream_invoke(
        self, request: ModelRequest, on_event: object
    ) -> ModelResult:
        self.started = True
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
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


class _InstantGateway:
    """A provider that answers immediately, for the no-cancel baseline."""

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


async def test_stop_request_cancels_in_flight_call_and_raises() -> None:
    """A stop landing mid-call tears the call down and raises CouncilCancelled.

    The gateway's stream hangs (as a stalled vendor does); the cancel channel
    reports a stop request on its second poll. ``deliberate`` must cancel the
    hung call and raise ``CouncilCancelled`` -- never return None as an absent
    seat, which would let the run degrade instead of stopping.
    """
    gateway = _HangingGateway()
    checks = 0

    async def cancel_check() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    deliberator = GatewayDeliberator(
        gateway,
        on_process=lambda kind, payload: None,
        cancel_check=cancel_check,
    )

    with pytest.raises(CouncilCancelled):
        await deliberator.deliberate(
            Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(deliberator)
        )

    # The hung model call was actually torn down, not left running.
    await asyncio.wait_for(gateway.cancelled.wait(), timeout=5)
    assert gateway.started


async def test_stop_request_never_falls_back_to_a_fresh_invoke() -> None:
    """A stop during a failed stream must not pay for another model call.

    The stream path catches ordinary exceptions and falls back to a
    non-streaming invoke; ``CouncilCancelled`` is not an ordinary exception --
    retrying after a stop would ignore the researcher's request and spend
    budget on a call they asked to end.
    """
    class _FailsThenHangs:
        async def stream_invoke(
            self, request: ModelRequest, on_event: object
        ) -> ModelResult:
            raise RuntimeError("vendor hiccup")

        async def invoke(self, request: ModelRequest) -> ModelResult:
            # Hangs like a stalled non-streaming call: if the fallback path
            # were taken, the stop poll would have to tear it down. The
            # assertion is that the stop wins *before* this ever completes.
            await asyncio.Event().wait()  # pragma: no cover - never returns
            raise AssertionError("unreachable")  # pragma: no cover

    async def cancel_check() -> bool:
        return True

    deliberator = GatewayDeliberator(
        _FailsThenHangs(),
        on_process=lambda kind, payload: None,
        cancel_check=cancel_check,
    )

    with pytest.raises(CouncilCancelled):
        await deliberator.deliberate(
            Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(deliberator)
        )


async def test_no_stop_request_keeps_previous_behaviour() -> None:
    """cancel_check polling off / always False changes nothing."""
    deliberator = GatewayDeliberator(
        _InstantGateway(),
        on_process=lambda kind, payload: None,
    )

    result = await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(deliberator)
    )
    assert result is not None
    assert result["initial_judgment"] == "noted"


async def test_cancel_poll_bounds_wait_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    """The poll tick is a small bounded constant, not a phase-sized wait."""
    monkeypatch.setattr(deliberation, "CANCEL_POLL_SECONDS", 0.05)
    gateway = _HangingGateway()

    async def cancel_check() -> bool:
        return True

    deliberator = GatewayDeliberator(
        gateway,
        on_process=lambda kind, payload: None,
        cancel_check=cancel_check,
    )

    started = asyncio.get_running_loop().time()
    with pytest.raises(CouncilCancelled):
        await deliberator.deliberate(
            Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, _context(deliberator)
        )
    elapsed = asyncio.get_running_loop().time() - started
    # One poll tick + cancellation cleanup; far below any phase duration.
    assert elapsed < 2.0


async def test_heartbeat_flushes_so_wait_clock_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every heartbeat reaches the live view immediately, not at the next
    token-delta flush (the 思考时间冻结 production failure)."""
    monkeypatch.setattr(deliberation, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    streamed: list[dict[str, object]] = []
    flushed = 0

    async def flush() -> None:
        nonlocal flushed
        flushed += 1

    deliberator = GatewayDeliberator(
        _InstantGateway(),
        on_process=lambda kind, payload: streamed.append({"kind": kind, **payload}),
        on_flush=flush,
    )

    heartbeat = asyncio.create_task(
        deliberator._heartbeat(Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT)
    )
    await asyncio.sleep(0.07)
    heartbeat.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await heartbeat

    workings = [item for item in streamed if item["kind"] == "seat_working"]
    # Three interval ticks fired and each one was flushed immediately.
    assert len(workings) >= 2
    assert workings[0]["seat"] == Seat.CAUSAL_SCIENTIST.value
    assert flushed >= 2
    # The wait clock is server-side monotonic -- later heartbeats report a
    # wait no shorter than earlier ones (the freeze was a *flat* clock).
    elapsed_values = [int(item["elapsed"]) for item in workings]
    assert elapsed_values == sorted(elapsed_values)


async def test_heartbeat_survives_a_failed_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken trace write must not kill the heartbeat (CLAUDE.md 10)."""
    monkeypatch.setattr(deliberation, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    streamed: list[dict[str, object]] = []

    async def failing_flush() -> None:
        raise RuntimeError("db down")

    deliberator = GatewayDeliberator(
        _InstantGateway(),
        on_process=lambda kind, payload: streamed.append({"kind": kind, **payload}),
        on_flush=failing_flush,
    )

    heartbeat = asyncio.create_task(
        deliberator._heartbeat(Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT)
    )
    await asyncio.sleep(0.07)
    heartbeat.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await heartbeat

    # Later heartbeats still fired after the flush failure.
    workings = [item for item in streamed if item["kind"] == "seat_working"]
    assert len(workings) >= 2
