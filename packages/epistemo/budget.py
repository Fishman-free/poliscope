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

    # Each consumer checks *before* spending. Checking after meant a limit of N
    # permitted N-1 uses and a limit of 1 permitted none, because the spend that
    # brought the remainder to zero was itself rejected. A budget of one source
    # must buy one source.

    def consume_wall_clock(self, minutes: int) -> None:
        if self.wall_clock_remaining < minutes:
            raise BudgetExhausted("wall clock budget exhausted")
        self._wall_clock_used += minutes

    def record_elapsed(self, seconds: float) -> None:
        """Record wall-clock time that has actually elapsed since the run began.

        Called between phases -- a natural cancellation point -- so a run that
        outlives its wall-clock budget stops with an honest unfilled-slot
        report instead of keeping the council running forever (CLAUDE.md 10:
        budget exhaustion is reported, never papered over). The
        ``_wall_clock_used`` counter stays in whole minutes to match the
        existing ``consume_wall_clock`` semantics; the exhaustion check itself
        compares seconds so a zero-minute budget stops the run immediately.
        """
        if seconds >= self.limits.wall_clock_minutes * 60:
            raise BudgetExhausted("wall clock budget exhausted")
        minutes = int(seconds // 60)
        if minutes > self._wall_clock_used:
            self._wall_clock_used = minutes

    def consume_model_cost(self, cost_usd: Decimal) -> None:
        if self.model_budget_remaining < cost_usd:
            raise BudgetExhausted("model cost budget exhausted")
        self._model_cost_used += cost_usd

    def consume_tool_call(self) -> None:
        if self.tool_calls_remaining <= 0:
            raise BudgetExhausted("tool call budget exhausted")
        self._tool_calls_used += 1

    def consume_source(self) -> None:
        if self.sources_remaining <= 0:
            raise BudgetExhausted("source budget exhausted")
        self._sources_used += 1

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
