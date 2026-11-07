"""The cost panel never reports a token-consuming call as free (2026-11).

Calls recorded before the gateway learned list prices carry ``cost_usd = 0``
although they consumed tokens. The read model keeps the recorded figure
untouched and prices those rows separately, as a labelled upper-bound
estimate -- or reports the price as unknown when the model has none.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.models.models import ModelCallModel
from packages.models.pricing import DEEPSEEK_FLASH_PRICE
from packages.models.usage import TaskBudget, aggregate_task_usage

BUDGET = TaskBudget(model_cost_usd=Decimal("10"), tool_call_limit=0, source_limit=0)


def _call(
    task_id: UUID, *, input_tokens: int, output_tokens: int, cost: str, purpose: str
) -> ModelCallModel:
    return ModelCallModel(
        task_id=task_id,
        status="SUCCEEDED",
        created_by="usage_pricing_test",
        actor="theory_builder",
        purpose=purpose,
        model_class="strong_reasoning",
        output_schema="FinalJudgment",
        input_hash="0" * 64,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=Decimal(cost),
        latency_ms=10,
        schema_status="OK",
    )


@pytest.fixture
async def mixed_calls(seeded_task: UUID, app_session: AsyncSession) -> UUID:
    app_session.add_all(
        [
            # Recorded before list prices existed: tokens, zero cost.
            _call(
                seeded_task,
                input_tokens=1_000_000,
                output_tokens=100_000,
                cost="0",
                purpose="precommitment",
            ),
            # Recorded with a real price.
            _call(
                seeded_task,
                input_tokens=10,
                output_tokens=10,
                cost="0.5",
                purpose="final_rejudgment",
            ),
            # A failed call that consumed nothing is not "unpriced".
            _call(
                seeded_task,
                input_tokens=0,
                output_tokens=0,
                cost="0",
                purpose="final_rejudgment",
            ),
        ]
    )
    await app_session.flush()
    return seeded_task


async def test_unpriced_rows_are_estimated_apart_from_recorded_cost(
    mixed_calls: UUID, app_session: AsyncSession
) -> None:
    usage = await aggregate_task_usage(
        app_session,
        mixed_calls,
        BUDGET,
        model_name="deepseek-flash",
        price=DEEPSEEK_FLASH_PRICE,
    )
    model = usage["model"]
    pricing = usage["pricing"]
    assert isinstance(model, dict) and isinstance(pricing, dict)
    # The recorded figure is exactly what the gateway wrote.
    assert model["cost_usd"] == pytest.approx(0.5)
    assert pricing["source"] == "list"
    assert pricing["model_name"] == "deepseek-flash"
    assert pricing["unpriced_calls"] == 1
    # 1M input at the cache-miss rate + 100k output: an upper bound.
    assert pricing["estimated_unpriced_usd"] == pytest.approx(0.30 + 0.12)


async def test_unknown_price_is_reported_as_unknown_not_free(
    mixed_calls: UUID, app_session: AsyncSession
) -> None:
    usage = await aggregate_task_usage(
        app_session, mixed_calls, BUDGET, model_name="some-relay-model", price=None
    )
    pricing = usage["pricing"]
    assert isinstance(pricing, dict)
    assert pricing["source"] is None
    assert pricing["unpriced_calls"] == 1
    assert pricing["estimated_unpriced_usd"] is None
