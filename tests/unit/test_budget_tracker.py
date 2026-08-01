"""Budget accounting: a limit of N must buy N.

CLAUDE.md 10 requires an exhausted budget to produce a report of unfilled
evidence slots rather than a fabricated complete result. That only works if the
accounting is right at the boundary -- the tracker previously checked *after*
spending, so it rejected the very use that brought the remainder to zero and a
`source_limit` of 1 bought no sources at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from packages.epistemo.budget import (
    BudgetExhausted,
    BudgetTracker,
    ResearchBudget,
)


def _tracker(
    wall_clock_minutes: int = 60,
    model_cost_usd: str = "10",
    tool_call_limit: int = 10,
    source_limit: int = 10,
) -> BudgetTracker:
    return BudgetTracker(
        limits=ResearchBudget(
            wall_clock_minutes=wall_clock_minutes,
            model_cost_usd=Decimal(model_cost_usd),
            tool_call_limit=tool_call_limit,
            source_limit=source_limit,
        )
    )


def test_a_source_budget_of_one_buys_exactly_one_source() -> None:
    tracker = _tracker(source_limit=1)
    tracker.consume_source()
    assert tracker.sources_remaining == 0
    with pytest.raises(BudgetExhausted):
        tracker.consume_source()


def test_a_tool_budget_of_one_buys_exactly_one_call() -> None:
    tracker = _tracker(tool_call_limit=1)
    tracker.consume_tool_call()
    with pytest.raises(BudgetExhausted):
        tracker.consume_tool_call()


def test_a_zero_budget_buys_nothing() -> None:
    tracker = _tracker(tool_call_limit=0, source_limit=0)
    with pytest.raises(BudgetExhausted):
        tracker.consume_tool_call()
    with pytest.raises(BudgetExhausted):
        tracker.consume_source()


def test_spending_the_exact_model_budget_is_allowed() -> None:
    tracker = _tracker(model_cost_usd="2.50")
    tracker.consume_model_cost(Decimal("2.50"))
    assert tracker.model_budget_remaining == Decimal("0")
    with pytest.raises(BudgetExhausted):
        tracker.consume_model_cost(Decimal("0.01"))


def test_a_rejected_spend_does_not_change_the_remainder() -> None:
    """Otherwise a refused call would still be charged for."""
    tracker = _tracker(source_limit=1)
    tracker.consume_source()
    with pytest.raises(BudgetExhausted):
        tracker.consume_source()
    assert tracker.sources_remaining == 0


def test_the_stop_reason_appears_only_once_a_dimension_is_spent() -> None:
    tracker = _tracker(tool_call_limit=1)
    assert tracker.stop_reason is None
    tracker.consume_tool_call()
    assert tracker.stop_reason == "BUDGET_EXHAUSTED"


def test_unfilled_slots_are_recorded_not_summarised() -> None:
    """The report names what is missing; a count would not be actionable."""
    tracker = _tracker()
    tracker.mark_unfilled_slot("ACQUISITION:10.1234/x")
    tracker.mark_unfilled_slot("CROSS_EXAMINATION:causal_scientist")
    assert tracker.unfilled_evidence_slots == [
        "ACQUISITION:10.1234/x",
        "CROSS_EXAMINATION:causal_scientist",
    ]
