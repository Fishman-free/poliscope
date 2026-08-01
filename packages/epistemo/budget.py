from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


class BudgetExhausted(Exception):
    """Raised when a budget dimension is fully consumed."""


@dataclass(frozen=True, slots=True)
class ResearchBudget:
    wall_clock_minutes: int
    model_cost_usd: Decimal
    tool_call_limit: int
    source_limit: int


@dataclass
class BudgetTracker:
    limits: ResearchBudget
    _wall_clock_used: int = 0
    _model_cost_used: Decimal = Decimal("0")
    _tool_calls_used: int = 0
    _sources_used: int = 0
    _unfilled_slots: list[str] = field(default_factory=list)

    @property
    def wall_clock_remaining(self) -> int:
        return max(0, self.limits.wall_clock_minutes - self._wall_clock_used)

    @property
    def model_budget_remaining(self) -> Decimal:
        return max(
            Decimal("0"),
            self.limits.model_cost_usd - self._model_cost_used,
        )

    @property
    def tool_calls_remaining(self) -> int:
        return max(0, self.limits.tool_call_limit - self._tool_calls_used)

    @property
    def sources_remaining(self) -> int:
        return max(0, self.limits.source_limit - self._sources_used)

    @property
    def unfilled_evidence_slots(self) -> list[str]:
        return list(self._unfilled_slots)

    def consume_wall_clock(self, minutes: int) -> None:
        self._wall_clock_used += minutes
        if self.wall_clock_remaining <= 0:
            raise BudgetExhausted("wall clock budget exhausted")

    def consume_model_cost(self, cost_usd: Decimal) -> None:
        self._model_cost_used += cost_usd
        if self.model_budget_remaining <= 0:
            raise BudgetExhausted("model cost budget exhausted")

    def consume_tool_call(self) -> None:
        self._tool_calls_used += 1
        if self.tool_calls_remaining <= 0:
            raise BudgetExhausted("tool call budget exhausted")

    def consume_source(self) -> None:
        self._sources_used += 1
        if self.sources_remaining <= 0:
            raise BudgetExhausted("source budget exhausted")

    def mark_unfilled_slot(self, slot: str) -> None:
        self._unfilled_slots.append(slot)

    @property
    def stop_reason(self) -> str | None:
        if self.wall_clock_remaining <= 0:
            return "BUDGET_EXHAUSTED"
        if self.model_budget_remaining <= 0:
            return "BUDGET_EXHAUSTED"
        if self.tool_calls_remaining <= 0:
            return "BUDGET_EXHAUSTED"
        if self.sources_remaining <= 0:
            return "BUDGET_EXHAUSTED"
        return None
