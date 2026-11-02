"""「研究不能中断」：生产里每条让研究中途停下的路径，各一条回归测试。

每条都对应生产库里查到的证据或代码中确认的路径：

* 交叉质询的质询 91% 指向不存在的主张 —— 提示词只给主张原文、不给 id；
* 工具调用预算用完会跳过其后全部议会阶段（交叉质询、最终复判都没了）；
* 墙钟预算用完时最终复判也被跳过，结论失去各席最终立场；
* 看门狗缓冲（300 s）比单个阶段的合法上限（900 s）还短；
* 结论整合 300 s 超时，16 次中 5 次退回机械归档；
* 同一阶段第二次以不同错误失败 → 幂等键冲突 → 整个任务 FAILED；
* 盲点悬赏末尾按下的「停止」被人类检查点吞掉，回答检查点后才生效。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from apps.worker.main import WATCHDOG_BUFFER_SECONDS
from packages.council.contracts import Seat
from packages.council.deliberation import _user_prompt
from packages.council.rounds.registry import (
    _COLLECT_DEADLINE_SECONDS,
    PhaseContext,
    PhaseOutcome,
)
from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.epistemo.contracts import PHASE_SEQUENCE, TaskPhase, TaskStatus
from packages.epistemo.orchestrator import PHASE_FAILED, CouncilOrchestrator
from packages.models.contracts import ModelClass, ModelMessage, ModelRequest
from packages.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleModelGateway,
)
from packages.reports.synthesis import SYNTHESIS_DEADLINE_SECONDS


class _Sink:
    def __init__(self) -> None:
        self.keys: list[tuple[str, str]] = []

    async def append(
        self,
        task_id: object,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
        status: str = "pending",
        **_: object,
    ) -> None:
        self.keys.append((event_type, idempotency_key))


def _budget(*, wall_clock: int = 1000, tools: int = 1000) -> BudgetTracker:
    return BudgetTracker(
        ResearchBudget(
            wall_clock_minutes=wall_clock,
            model_cost_usd=Decimal("1000"),
            tool_call_limit=tools,
            source_limit=1000,
        )
    )


# --- 主张 id 进入提示词 -------------------------------------------------------


def test_seats_are_shown_the_claim_ids_they_must_cite() -> None:
    claim = uuid4()
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.CROSS_EXAMINATION,
        seats=(Seat.CAUSAL_SCIENTIST,),
        question="“女士优先”有助于还是无助于性别平权？",
        confirmed_claims=(claim,),
        claim_statements={claim: "女士优先有助于性别平权"},
        deliberator=None,  # type: ignore[arg-type]
    )
    prompt = _user_prompt(Seat.CAUSAL_SCIENTIST, context)
    assert f"- {claim}: 女士优先有助于性别平权" in prompt


# --- 预算不再中断议会 -----------------------------------------------------------


async def test_a_spent_tool_budget_runs_every_deliberation_phase() -> None:
    orchestrator = CouncilOrchestrator(ledger=_Sink(), budget=_budget(tools=0))
    report = await orchestrator.run(task_id=uuid4(), question="q")
    assert report.phases_skipped == ()
    assert report.phases_run == PHASE_SEQUENCE


async def test_a_spent_wall_clock_still_closes_with_the_final_rejudgment() -> None:
    orchestrator = CouncilOrchestrator(ledger=_Sink(), budget=_budget(wall_clock=0))
    report = await orchestrator.run(task_id=uuid4(), question="q")
    assert TaskPhase.FINAL_REJUDGMENT in report.phases_run
    assert TaskPhase.CROSS_EXAMINATION in report.phases_skipped
    # The skipped phases are still reported as gaps, never hidden.
    assert report.final_status is TaskStatus.COMPLETED_WITH_GAPS
    assert f"{TaskPhase.CROSS_EXAMINATION.value}:not_reached" in report.unfilled_slots


async def test_a_stop_still_ends_the_run_before_the_closing_phase() -> None:
    async def stop_requested() -> bool:
        return True

    orchestrator = CouncilOrchestrator(
        ledger=_Sink(), budget=_budget(wall_clock=0), cancel_check=stop_requested
    )
    report = await orchestrator.run(task_id=uuid4(), question="q")
    assert report.final_status is TaskStatus.CANCELLED
    assert TaskPhase.FINAL_REJUDGMENT not in report.phases_run


# --- 停止请求不再被检查点吞掉 ---------------------------------------------------


async def test_a_stop_before_the_checkpoint_cancels_instead_of_parking() -> None:
    seen: list[int] = []

    async def stop_after_bounty() -> bool:
        seen.append(1)
        # PRECOMMITMENT..BLINDSPOT_BOUNTY are five boundaries; the stop lands
        # at the sixth, the one the checkpoint also guards.
        return len(seen) > 5

    orchestrator = CouncilOrchestrator(
        ledger=_Sink(), budget=_budget(), cancel_check=stop_after_bounty
    )
    report = await orchestrator.run(
        task_id=uuid4(), question="q", stop_before=TaskPhase.JOINT_MODELING
    )
    assert report.final_status is TaskStatus.CANCELLED


# --- 同一阶段再次失败不会撞键 ---------------------------------------------------


async def test_a_phase_failing_twice_with_different_errors_records_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = iter([RuntimeError("first failure"), RuntimeError("second failure")])

    def runner_for(phase: TaskPhase) -> object:
        async def run(context: PhaseContext) -> PhaseOutcome:
            if phase is TaskPhase.PRECOMMITMENT:
                raise next(errors)
            return PhaseOutcome()

        return run

    monkeypatch.setattr("packages.epistemo.orchestrator.runner_for", runner_for)
    sink = _Sink()
    for _ in range(2):
        await CouncilOrchestrator(ledger=sink, budget=_budget()).run(
            task_id=uuid4(), question="q"
        )
    failed = [key for event, key in sink.keys if event == PHASE_FAILED]
    assert len(failed) == 2 and failed[0] != failed[1]
    assert all(key.startswith("PRECOMMITMENT:failed:") for key in failed)


# --- 时间上限彼此一致 -----------------------------------------------------------


def test_the_watchdog_outlasts_everything_a_healthy_run_may_still_do() -> None:
    """After the last budget check a healthy run may finish the phase in flight,
    run the closing rejudgment and the synthesis; the watchdog must not fire
    first (the old 300 s buffer was shorter than one phase)."""
    assert WATCHDOG_BUFFER_SECONDS >= (
        2 * _COLLECT_DEADLINE_SECONDS + SYNTHESIS_DEADLINE_SECONDS
    )


async def test_a_request_deadline_overrides_the_gateway_default() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return httpx.Response(500)

    gateway = OpenAICompatibleModelGateway(
        OpenAICompatibleConfig(
            api_key="k",
            base_url="https://api.example.test/v1",
            model_names={model: "m" for model in ModelClass},
            invoke_total_timeout_seconds=300.0,
        ),
        client=httpx.AsyncClient(
            base_url="https://api.example.test/v1",
            transport=httpx.MockTransport(slow),
        ),
    )
    request = ModelRequest(
        task_id=uuid4(),
        actor="report_synthesizer",
        purpose="FINAL_SYNTHESIS",
        model_class=ModelClass.STRONG_REASONING,
        messages=(ModelMessage(role="user", content="q"),),
        output_schema="ReasoningMap",
        evidence_refs=(),
        deadline_seconds=0.05,
    )
    with pytest.raises(TimeoutError, match="0.05s total deadline"):
        await gateway.invoke(request)


def test_synthesis_gets_more_time_than_a_seat_call() -> None:
    assert SYNTHESIS_DEADLINE_SECONDS > 300.0

