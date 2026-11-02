"""续跑后专家记得之前的讨论（私有记忆 + 集体认知前沿）。

此前 ``CouncilMemory.restore`` 写好了却从未被调用：人类检查点之后、部署
之后、崩溃或断线之后的每一次续跑，七位专家都从一张白纸开始联合建模与
最终复判；集体前沿也只含本次续跑新产生的事件。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import PhaseContext
from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.epistemo.contracts import CouncilCheckpoint, TaskPhase, TaskStatus
from packages.epistemo.orchestrator import ORDERED_SEATS, CouncilOrchestrator
from packages.kernel.contracts import thaw_for_serialization
from packages.memory.adapter import create_memory_adapter
from packages.memory.council_memory import CouncilMemory


class _Sink:
    async def append(self, *args: object, **kwargs: object) -> None:
        return None


class _Deliberator:
    """Every seat commits to a position; later phases are left empty."""

    def __init__(self) -> None:
        self.last_error: str | None = None
        self.recalls: dict[TaskPhase, dict[Seat, str]] = {}

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        self.recalls.setdefault(phase, {})[seat] = context.recall.get(seat, "")
        if phase is TaskPhase.PRECOMMITMENT:
            return {
                "initial_judgment": f"{seat.value} 认为自评测量偏差是关键",
                "confidence": 0.6,
                "blindspots": [],
                "update_condition": "客观日志",
            }
        return {}


def _budget() -> BudgetTracker:
    return BudgetTracker(
        ResearchBudget(
            wall_clock_minutes=1000,
            model_cost_usd=Decimal("1000"),
            tool_call_limit=1000,
            source_limit=1000,
        )
    )


async def test_a_resumed_run_restores_what_the_seats_remembered() -> None:
    task_id = uuid4()
    first_memory = CouncilMemory(create_memory_adapter(None), task_id)
    first = await CouncilOrchestrator(
        ledger=_Sink(),
        budget=_budget(),
        deliberator=_Deliberator(),
        memory=first_memory,
    ).run(task_id=task_id, question="q", stop_before=TaskPhase.JOINT_MODELING)
    assert first.final_status is TaskStatus.AWAITING_COUNCIL_INPUT
    assert first.checkpoint is not None

    # What the worker stores: the checkpoint plus the seats' memory.
    stored = first.checkpoint.model_dump(mode="json")
    stored["memory_snapshot"] = thaw_for_serialization(
        await first_memory.snapshot(ORDERED_SEATS)
    )
    stored["gate_reached"] = True
    checkpoint = CouncilCheckpoint.model_validate(stored)

    # A brand-new process: fresh adapter, fresh memory.
    deliberator = _Deliberator()
    await CouncilOrchestrator(
        ledger=_Sink(),
        budget=_budget(),
        deliberator=deliberator,
        memory=CouncilMemory(create_memory_adapter(None), task_id),
    ).run(task_id=task_id, question="q", resume_from=checkpoint)

    recall = deliberator.recalls[TaskPhase.JOINT_MODELING][Seat.CAUSAL_SCIENTIST]
    assert "自评测量偏差" in recall


async def test_a_resumed_run_rebuilds_the_shared_frontier() -> None:
    orchestrator = CouncilOrchestrator(ledger=_Sink(), budget=_budget())
    orchestrator.absorb_history(
        (("DissentCertificate", {"seat": "adversarial_falsifier",
                                  "statement": "因果主张不成立"}),)
    )
    assert "因果主张不成立" in orchestrator._collective.view().summary


async def test_an_unreadable_memory_snapshot_never_stops_the_run() -> None:
    checkpoint = CouncilCheckpoint.model_validate(
        {
            "run_phases": [],
            "gate_reached": True,
            "memory_snapshot": {"causal_scientist": {"nodes": "not a list"}},
        }
    )
    task_id = uuid4()
    report = await CouncilOrchestrator(
        ledger=_Sink(),
        budget=_budget(),
        deliberator=_Deliberator(),
        memory=CouncilMemory(create_memory_adapter(None), task_id),
    ).run(task_id=task_id, question="q", resume_from=checkpoint)
    assert report.final_status in (
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED_WITH_GAPS,
    )
