"""Deadline and wall-clock guards added in round 4 (卡死防护).

The worker used to be able to hang forever: a thinking-mode stream that keeps
the connection alive never trips the per-read httpx timeout, the wall-clock
budget was defined but never enforced, and acquisition stretched serial
queries across a rate-limited vendor without a whole-pass ceiling. These tests
pin the guards that turned each of those into a bounded, honestly-reported
failure.

* model stream/invoke total deadlines (``openai_compatible.py``)
* wall-clock enforcement (``budget.record_elapsed`` + orchestrator)
* acquisition per-query timeout and whole-pass deadline
  (``packages/papers/acquisition.py``)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest

from apps.worker.main import WorkerContext, run_one
from packages.council.contracts import Seat
from packages.epistemo.budget import BudgetExhausted, BudgetTracker, ResearchBudget
from packages.epistemo.contracts import PHASE_SEQUENCE, TaskStatus
from packages.epistemo.orchestrator import CouncilOrchestrator
from packages.epistemo.stopping import StopReason
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import (
    ModelClass,
    ModelMessage,
    ModelRequest,
)
from packages.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleModelGateway,
)
from packages.papers.acquisition import (
    ACQUISITION_CONCURRENCY,
    ACQUISITION_PER_QUERY_SECONDS,
    ACQUISITION_TOTAL_SECONDS,
    SourceAcquisition,
)
from packages.tools.contracts import ToolRequest, ToolResult

FULL_ENV = {
    "POLISCOPE_MODEL_API_KEY": "test-key",
    "POLISCOPE_MODEL_BASE_URL": "https://api.example.test/v1",
    "POLISCOPE_MODEL_NAME": "deepseek-chat",
}


def _model_request() -> ModelRequest:
    return ModelRequest(
        task_id=uuid4(),
        actor="theory_builder",
        purpose="final_rejudgment",
        model_class=ModelClass.STRONG_REASONING,
        messages=(ModelMessage(role="user", content="give your final judgment"),),
        output_schema="FinalJudgment",
        evidence_refs=(),
    )


# --------------------------------------------------------------------------
# Model gateway: whole-call deadlines
# --------------------------------------------------------------------------


class _EndlessDripStream(httpx.AsyncByteStream):
    """A streamed response body that never sends the [DONE] terminator."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            await asyncio.sleep(0.01)
            yield b'data: {"choices": [{"delta": {"reasoning_content": "x"}}]}\n\n'


async def test_stream_invoke_hits_total_deadline_when_vendor_drips_forever() -> None:
    """A stream that keeps sending (but never completes) must time out.

    This is the exact shape of the round-4 incident: thinking-mode deltas keep
    arriving, so the per-read httpx timeout never fires and the seat sits in
    "thinking…" forever. A small injected total deadline proves the guard
    fires where the read timeout cannot.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_EndlessDripStream(),
        )

    config = OpenAICompatibleConfig.from_env(FULL_ENV)
    gateway = OpenAICompatibleModelGateway(
        config,
        client=httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),
        ),
    )
    gateway._config = OpenAICompatibleConfig(
        api_key=config.api_key,
        base_url=config.base_url,
        model_names=config.model_names,
        stream_total_timeout_seconds=0.15,
    )

    with pytest.raises(TimeoutError, match="total deadline"):
        await gateway.stream_invoke(_model_request(), lambda _e: asyncio.sleep(0))


async def test_invoke_hits_total_deadline_across_repair_attempts() -> None:
    """A vendor that answers garbage forever is bounded by the invoke ceiling.

    The repair/retry matrix (up to 16 sixty-second attempts) used to mean
    minutes of silence; the total deadline turns it into one bounded failure.
    """
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)  # slow but not timeout-clean: returns junk
        return httpx.Response(200, text='{"choices": [{"message": {"content": "not json"}}]}')  # noqa: E501

    config = OpenAICompatibleConfig.from_env(FULL_ENV)
    gateway = OpenAICompatibleModelGateway(
        config,
        client=httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),
        ),
    )
    gateway._config = OpenAICompatibleConfig(
        api_key=config.api_key,
        base_url=config.base_url,
        model_names=config.model_names,
        invoke_total_timeout_seconds=0.1,
    )

    with pytest.raises(TimeoutError, match="total deadline"):
        await gateway.invoke(_model_request())


async def test_invoke_deadline_cancels_a_slow_valid_first_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"final_judgment":"valid"}'}}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    config = OpenAICompatibleConfig.from_env(FULL_ENV)
    config = OpenAICompatibleConfig(
        api_key=config.api_key,
        base_url=config.base_url,
        model_names=config.model_names,
        invoke_total_timeout_seconds=0.05,
    )
    gateway = OpenAICompatibleModelGateway(
        config,
        client=httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),
        ),
    )
    started = asyncio.get_running_loop().time()

    with pytest.raises(TimeoutError, match="model invoke.*total deadline"):
        await gateway.invoke(_model_request())

    assert asyncio.get_running_loop().time() - started < 0.25


async def test_invoke_deadline_cancels_transport_retry_after_backoff() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "30"})

    config = OpenAICompatibleConfig.from_env(FULL_ENV)
    config = OpenAICompatibleConfig(
        api_key=config.api_key,
        base_url=config.base_url,
        model_names=config.model_names,
        invoke_total_timeout_seconds=0.05,
    )
    gateway = OpenAICompatibleModelGateway(
        config,
        client=httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),
        ),
    )

    async with asyncio.timeout(0.5):
        with pytest.raises(TimeoutError, match="model invoke.*total deadline"):
            await gateway.invoke(_model_request())

    assert calls == 1


async def test_worker_watchdog_waits_for_cancellation_before_emergency_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    cancelled = asyncio.Event()

    async def short_watchdog(*args: object, **kwargs: object) -> float:
        return 0.02

    async def hanging_task(*args: object, **kwargs: object) -> object:
        try:
            return await asyncio.Future()
        finally:
            order.append("cancelled")
            cancelled.set()

    async def emergency(*args: object, **kwargs: object) -> bool:
        assert cancelled.is_set()
        order.append("fallback")
        return True

    monkeypatch.setattr("apps.worker.main._watchdog_timeout", short_watchdog)
    monkeypatch.setattr("apps.worker.main.run_task", hanging_task)
    monkeypatch.setattr("apps.worker.main.emergency_finalize_task", emergency)
    context = cast(
        WorkerContext,
        SimpleNamespace(
            app_sessions=object(),
            projector_sessions=object(),
            gateway=None,
            tools=None,
            fulltext_fetcher=None,
            object_store=None,
        ),
    )

    assert await run_one(context, uuid4()) is None
    assert order == ["cancelled", "fallback"]


async def test_worker_unexpected_exception_triggers_emergency_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reasons: list[str] = []

    async def watchdog(*args: object, **kwargs: object) -> float:
        return 1.0

    async def broken_task(*args: object, **kwargs: object) -> object:
        raise RuntimeError("unexpected pipeline failure")

    async def emergency(*args: object, **kwargs: object) -> bool:
        reasons.append(str(kwargs["reason"]))
        return True

    monkeypatch.setattr("apps.worker.main._watchdog_timeout", watchdog)
    monkeypatch.setattr("apps.worker.main.run_task", broken_task)
    monkeypatch.setattr("apps.worker.main.emergency_finalize_task", emergency)
    context = cast(
        WorkerContext,
        SimpleNamespace(
            app_sessions=object(),
            projector_sessions=object(),
            gateway=None,
            tools=None,
            fulltext_fetcher=None,
            object_store=None,
        ),
    )

    assert await run_one(context, uuid4()) is None
    assert len(reasons) == 1
    assert "RuntimeError" in reasons[0]
    assert "unexpected pipeline failure" in reasons[0]


# --------------------------------------------------------------------------
# Wall-clock budget: now actually enforced
# --------------------------------------------------------------------------


def test_record_elapsed_raises_past_wall_clock_budget() -> None:
    tracker = BudgetTracker(
        ResearchBudget(
            wall_clock_minutes=1,
            model_cost_usd=Decimal("10"),
            tool_call_limit=10,
            source_limit=10,
        )
    )
    with pytest.raises(BudgetExhausted):
        tracker.record_elapsed(61.0)
    # Under budget, nothing raises and the whole-minute counter advances.
    tracker.record_elapsed(30.0)
    assert tracker.wall_clock_remaining == 1


@dataclass
class _FakeEventSink:
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def append(self, task_id: UUID, event_type: str, payload: dict[str, object], idempotency_key: str, **_: object) -> None:  # noqa: E501
        self.calls.append((event_type, idempotency_key, str(payload.get("phase", ""))))
        return None


async def test_orchestrator_stops_when_wall_clock_budget_is_zero() -> None:
    """A zero wall-clock budget stops the council between phases with an
    honest skip, instead of running all eight rounds regardless."""
    sink = _FakeEventSink()
    orchestrator = CouncilOrchestrator(
        ledger=sink,  # type: ignore[arg-type]
        budget=BudgetTracker(
            ResearchBudget(
                wall_clock_minutes=0,
                model_cost_usd=Decimal("1000"),
                tool_call_limit=1000,
                source_limit=1000,
            )
        ),
    )
    report = await orchestrator.run(task_id=uuid4(), question="Is X caused by Y?")
    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.final_status is TaskStatus.COMPLETED_WITH_GAPS
    # Every phase was skipped (none ran), and each skip is on the ledger.
    assert list(report.phases_skipped) == list(PHASE_SEQUENCE)
    assert any(key.endswith(":skipped") for _, key, _ in sink.calls)


# --------------------------------------------------------------------------
# Acquisition: per-query timeout, whole-pass deadline, bounded concurrency
# --------------------------------------------------------------------------


class _SlowSearchGateway:
    """A tool gateway that sleeps per search call, counting every call."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls: list[ToolRequest] = []

    async def execute(self, request: ToolRequest) -> ToolResult:
        self.calls.append(request)
        await asyncio.sleep(self.delay)
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict({"doi": None}),
            latency_ms=int(self.delay * 1000),
            retries=0,
            error_code=None,
        )


class _NullSession:
    """Minimal stand-in: acquisition only reads existing sources here."""

    async def scalar(self, *_: object, **__: object) -> None:
        return None


def _acquisition(gateway: _SlowSearchGateway) -> SourceAcquisition:
    return SourceAcquisition(
        session=_NullSession(),  # type: ignore[arg-type]
        gateway=gateway,
        task_id=uuid4(),
    )


@pytest.mark.parametrize("attribute", ["ACQUISITION_PER_QUERY_SECONDS", "ACQUISITION_TOTAL_SECONDS", "ACQUISITION_CONCURRENCY"])  # noqa: E501
def test_acquisition_ceiling_constants_are_sane(attribute: str) -> None:
    value = {
        "ACQUISITION_PER_QUERY_SECONDS": ACQUISITION_PER_QUERY_SECONDS,
        "ACQUISITION_TOTAL_SECONDS": ACQUISITION_TOTAL_SECONDS,
        "ACQUISITION_CONCURRENCY": ACQUISITION_CONCURRENCY,
    }[attribute]
    assert value > 0


async def test_acquisition_per_query_timeout_is_refused_not_hung(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query slower than the per-query ceiling is refused honestly, and the
    pass moves on instead of waiting forever."""
    monkeypatch.setattr("packages.papers.acquisition.ACQUISITION_PER_QUERY_SECONDS", 0.1)  # noqa: E501
    gateway = _SlowSearchGateway(delay=5.0)
    acquisition = _acquisition(gateway)

    result = await acquisition.acquire(
        [(Seat.THEORY_BUILDER, "a query with no DOI shape")]
    )

    assert result.acquired == ()
    assert result.unresolvable == ()
    assert [refused.reason for refused in result.refused] == ["acquisition timed out"]


async def test_acquisition_concurrent_queries_finish_in_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Several free-text queries fetch concurrently (Semaphore), so the pass
    finishes in one parallel burst instead of serially stretching."""
    monkeypatch.setattr("packages.papers.acquisition.ACQUISITION_CONCURRENCY", 3)
    gateway = _SlowSearchGateway(delay=0.2)
    acquisition = _acquisition(gateway)
    queries = [(Seat.THEORY_BUILDER, f"query number {index}") for index in range(6)]

    started = asyncio.get_running_loop().time()
    result = await acquisition.acquire(queries)
    elapsed = asyncio.get_running_loop().time() - started

    assert result.acquired == ()
    # Each query serially tries 3 providers (0.2s each = 0.6s per query).
    # Fully serial would be 6 × 0.6 = 3.6s; the 3-way query burst runs two
    # waves of 0.6s ≈ 1.2s. The 2.5s bound separates the two shapes robustly
    # on slow CI runners.
    assert elapsed < 2.5
    assert len(gateway.calls) == 18  # 6 queries × 3 providers


async def test_acquisition_whole_pass_deadline_refuses_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the whole-pass deadline has passed, queries are refused with an
    honest timeout reason instead of being fetched."""
    # A zero-second deadline is already expired at the first planning check,
    # so every query is refused deterministically (no timing race).
    monkeypatch.setattr("packages.papers.acquisition.ACQUISITION_TOTAL_SECONDS", 0.0)
    gateway = _SlowSearchGateway(delay=0.0)
    acquisition = _acquisition(gateway)

    result = await acquisition.acquire(
        [(Seat.THEORY_BUILDER, f"late query {index}") for index in range(5)]
    )

    assert [refused.reason for refused in result.refused] == [
        "acquisition timed out"
    ] * 5
    # Nothing was fetched at all.
    assert gateway.calls == []
