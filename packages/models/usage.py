"""Real-time budget and cost aggregation for one task (D12).

The audit tables ``model_calls`` and ``tool_calls`` already record every
gateway call -- tokens, cost, latency, retries, error code -- one row per call
(packages/models/audit.py). Nothing read them back as a running budget, so a
researcher could only discover the cost after the run finished. This module is
the read side: it aggregates those rows by purpose/tool and compares them to
the task's declared budget.

It is strictly a read model. It never estimates a price the gateway did not
record (a NULL/zero cost is reported as zero), never turns model confidence
into a number (CLAUDE.md 16), and degrades honestly when a limit is unset
(``limit == 0`` means "no cap", so remaining is reported as None rather than a
negative number).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.models.models import ModelCallModel
from packages.tools.models import ToolCallModel


def _money(value: Decimal | float | int | None) -> float:
    if value is None:
        return 0.0
    return float(Decimal(value))


@dataclass(frozen=True, slots=True)
class TaskBudget:
    """The caps declared on the task row (0 means "uncapped")."""

    model_cost_usd: Decimal
    tool_call_limit: int
    source_limit: int


async def aggregate_task_usage(
    session: AsyncSession,
    task_id: UUID,
    budget: TaskBudget,
) -> dict[str, object]:
    """Aggregate model/tool audit rows for ``task_id`` into a JSON-able dict.

    Shape::

        {
          "model": {"calls", "input_tokens", "output_tokens", "cost_usd",
                    "latency_ms", "retries", "errors", "by_purpose": {...}},
          "tools": {"calls", "cost_usd", "latency_ms", "retries", "errors",
                    "by_tool": {...}},
          "budget": {"model_cost_limit_usd", "model_cost_remaining_usd",
                     "tool_call_limit", "tool_calls_remaining",
                     "source_limit"}
        }
    """
    model_rows = await session.execute(
        select(
            ModelCallModel.purpose,
            func.count().label("calls"),
            func.coalesce(func.sum(ModelCallModel.input_tokens), 0),
            func.coalesce(func.sum(ModelCallModel.output_tokens), 0),
            func.coalesce(func.sum(ModelCallModel.cost_usd), 0),
            func.coalesce(func.sum(ModelCallModel.latency_ms), 0),
            func.coalesce(func.sum(ModelCallModel.retries), 0),
            func.sum(
                func.cast(ModelCallModel.error_code.is_not(None), Integer)
            ),
        )
        .where(ModelCallModel.task_id == task_id)
        .group_by(ModelCallModel.purpose)
    )
    by_purpose: dict[str, dict[str, float]] = {}
    model_tot: dict[str, float] = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": 0,
        "retries": 0,
        "errors": 0,
    }
    for purpose, calls, in_tok, out_tok, cost, latency, retries, errors in model_rows:
        entry: dict[str, float] = {
            "calls": int(calls),
            "input_tokens": int(in_tok),
            "output_tokens": int(out_tok),
            "cost_usd": _money(cost),
            "latency_ms": int(latency),
            "retries": int(retries),
            "errors": int(errors or 0),
        }
        by_purpose[str(purpose)] = entry
        for key in ("calls", "input_tokens", "output_tokens", "latency_ms",
                    "retries", "errors"):
            model_tot[key] += entry[key]
        model_tot["cost_usd"] = round(model_tot["cost_usd"] + entry["cost_usd"], 6)

    tool_rows = await session.execute(
        select(
            ToolCallModel.tool_name,
            func.count().label("calls"),
            func.coalesce(func.sum(ToolCallModel.cost_usd), 0),
            func.coalesce(func.sum(ToolCallModel.latency_ms), 0),
            func.coalesce(func.sum(ToolCallModel.retries), 0),
            func.sum(func.cast(ToolCallModel.error_code.is_not(None), Integer)),
        )
        .where(ToolCallModel.task_id == task_id)
        .group_by(ToolCallModel.tool_name)
    )
    by_tool: dict[str, dict[str, float]] = {}
    tool_tot: dict[str, float] = {
        "calls": 0,
        "cost_usd": 0.0,
        "latency_ms": 0,
        "retries": 0,
        "errors": 0,
    }
    for tool, calls, cost, latency, retries, errors in tool_rows:
        tool_entry: dict[str, float] = {
            "calls": int(calls),
            "cost_usd": _money(cost),
            "latency_ms": int(latency),
            "retries": int(retries),
            "errors": int(errors or 0),
        }
        by_tool[str(tool)] = tool_entry
        for key in ("calls", "latency_ms", "retries", "errors"):
            tool_tot[key] += tool_entry[key]
        tool_tot["cost_usd"] = round(
            tool_tot["cost_usd"] + tool_entry["cost_usd"], 6
        )

    cost_limit = _money(budget.model_cost_usd)
    tool_limit = int(budget.tool_call_limit)
    return {
        "model": {**model_tot, "by_purpose": by_purpose},
        "tools": {**tool_tot, "by_tool": by_tool},
        "budget": {
            "model_cost_limit_usd": cost_limit or None,
            "model_cost_remaining_usd": (
                round(cost_limit - model_tot["cost_usd"], 6)
                if cost_limit > 0
                else None
            ),
            "tool_call_limit": tool_limit or None,
            "tool_calls_remaining": (
                max(tool_limit - tool_tot["calls"], 0)
                if tool_limit > 0
                else None
            ),
            "source_limit": int(budget.source_limit) or None,
        },
    }


__all__ = ["TaskBudget", "aggregate_task_usage"]
