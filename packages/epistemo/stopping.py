from __future__ import annotations

from enum import StrEnum


class StopReason(StrEnum):
    CONTINUE = "CONTINUE"
    EVIDENCE_SATURATION = "EVIDENCE_SATURATION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    # The researcher requested a stop (round-10 「停止研究」). Read between
    # phases from task_cancel_requests, so a stop lands at a phase boundary
    # rather than mid-round.
    CANCELLED = "CANCELLED"


class StopDecision:
    def __init__(self, reason: StopReason, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail


def decide_stop(
    no_new_information_rounds: int,
    budget_remaining: int,
) -> StopDecision:
    if budget_remaining <= 0:
        return StopDecision(
            StopReason.BUDGET_EXHAUSTED,
            "budget exhausted before evidence saturation",
        )
    if no_new_information_rounds >= 1:
        return StopDecision(
            StopReason.EVIDENCE_SATURATION,
            "no new information in recent round",
        )
    return StopDecision(StopReason.CONTINUE)


