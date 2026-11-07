"""Model cost accounting: list prices, cache-hit billing, honest unknowns.

2026-11 regression: a run of 1,930,172 input / 203,253 output tokens showed a
cost of 0.0000 USD, because no deployment sets ``POLISCOPE_MODEL_PRICE_*``
and a researcher's own endpoint never had a price at all. A known model is
now billed at its list price; an unknown one stays at zero *recorded* cost
and is reported as "price unknown", never as free.
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from packages.models.contracts import (
    ModelClass,
    ModelMessage,
    ModelRequest,
    StreamEvent,
)
from packages.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleModelGateway,
)
from packages.models.pricing import (
    DEEPSEEK_FLASH_PRICE,
    DEEPSEEK_PRO_PRICE,
    ModelPrice,
    cached_prompt_tokens,
    configured_price,
    list_price,
    task_price,
)
from packages.models.usage import estimate_unpriced


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("deepseek-flash", DEEPSEEK_FLASH_PRICE),
        ("deepseek-v4-flash", DEEPSEEK_FLASH_PRICE),
        ("DeepSeek-V4-Flash", DEEPSEEK_FLASH_PRICE),
        ("deepseek/deepseek-flash", DEEPSEEK_FLASH_PRICE),
        ("deepseek-v4-pro", DEEPSEEK_PRO_PRICE),
        ("deepseek-chat", None),
        ("gpt-4o", None),
        ("", None),
        (None, None),
    ],
)
def test_list_price_matches_by_family(
    name: str | None, expected: ModelPrice | None
) -> None:
    assert list_price(name) == expected


def test_cost_bills_cache_hits_at_the_cache_rate() -> None:
    # 1M input of which 400k hit the cache, 100k output, Flash list price.
    cost = DEEPSEEK_FLASH_PRICE.cost(1_000_000, 100_000, cached_input_tokens=400_000)
    assert cost == (
        Decimal("600000") * Decimal("0.30")
        + Decimal("400000") * Decimal("0.006")
        + Decimal("100000") * Decimal("1.20")
    ) / Decimal(1_000_000)


def test_reported_run_is_no_longer_free() -> None:
    # The exact figures from the report, priced as an upper bound.
    estimate = estimate_unpriced(DEEPSEEK_FLASH_PRICE, 1_930_172, 203_253)
    assert estimate == pytest.approx(1_930_172 * 0.30 / 1e6 + 203_253 * 1.20 / 1e6)
    assert estimate is not None and estimate > 0.5


def test_unknown_price_estimates_nothing() -> None:
    assert estimate_unpriced(None, 1_000, 1_000) is None


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_cache_hit_tokens": 300}, 300),
        ({"prompt_tokens_details": {"cached_tokens": 120}}, 120),
        ({"prompt_tokens": 500}, 0),
        ({"prompt_cache_hit_tokens": "garbage"}, 0),
    ],
)
def test_cached_prompt_tokens_reads_both_dialects(
    usage: dict[str, object], expected: int
) -> None:
    assert cached_prompt_tokens(usage) == expected


def test_configured_price_requires_a_rate() -> None:
    assert configured_price({}) is None
    price = configured_price({"POLISCOPE_MODEL_PRICE_INPUT_PER_1M_USD": "2"})
    assert price is not None and price.source == "configured"
    assert price.output_per_1m_usd == Decimal("0")


def test_task_price_own_endpoint_uses_list_price_not_operator_price() -> None:
    env = {
        "POLISCOPE_MODEL_NAME": "operator-model",
        "POLISCOPE_MODEL_PRICE_INPUT_PER_1M_USD": "9",
    }
    config = {
        "base_url": "https://relay.example/v1",
        "api_key": "k",
        "model_name": "deepseek-flash",
    }
    assert task_price(config, env) == ("deepseek-flash", DEEPSEEK_FLASH_PRICE)
    unknown = {**config, "model_name": "some-relay-model"}
    assert task_price(unknown, env) == ("some-relay-model", None)


def test_task_price_deployment_default_prefers_operator_price() -> None:
    env = {
        "POLISCOPE_MODEL_NAME": "deepseek-v4-flash",
        "POLISCOPE_MODEL_PRICE_INPUT_PER_1M_USD": "1",
        "POLISCOPE_MODEL_PRICE_OUTPUT_PER_1M_USD": "2",
    }
    name, price = task_price(None, env)
    assert name == "deepseek-v4-flash"
    assert price is not None and price.source == "configured"
    name, price = task_price(None, {"POLISCOPE_MODEL_NAME": "deepseek-v4-flash"})
    assert price == DEEPSEEK_FLASH_PRICE


def _request() -> ModelRequest:
    return ModelRequest(
        task_id=uuid4(),
        actor="theory_builder",
        purpose="final_rejudgment",
        model_class=ModelClass.STRONG_REASONING,
        messages=(ModelMessage(role="user", content="judge"),),
        output_schema="FinalJudgment",
        evidence_refs=(),
    )


def _gateway(
    handler: object, model_name: str
) -> OpenAICompatibleModelGateway:
    config = OpenAICompatibleConfig(
        api_key="k",
        base_url="https://relay.example/v1",
        model_names={cls: model_name for cls in ModelClass},
    )
    return OpenAICompatibleModelGateway(
        config,
        client=httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        ),
    )


_USAGE = {
    "prompt_tokens": 1_000_000,
    "completion_tokens": 100_000,
    "prompt_cache_hit_tokens": 400_000,
}


async def test_invoke_records_list_price_with_cache_split() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"final_judgment": "x"}'}}],
                "usage": _USAGE,
            },
        )

    result = await _gateway(handler, "deepseek-flash").invoke(_request())
    assert result.cost_usd == DEEPSEEK_FLASH_PRICE.cost(1_000_000, 100_000, 400_000)
    assert result.cost_usd > 0


async def test_stream_records_list_price() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        chunks = [
            {"choices": [{"delta": {"content": '{"final_judgment": "x"}'}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": _USAGE},
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        return httpx.Response(200, text=body + "data: [DONE]\n\n")

    async def relay(_: StreamEvent) -> None:
        return None

    result = await _gateway(handler, "deepseek-v4-flash").stream_invoke(
        _request(), relay
    )
    assert result.cost_usd == DEEPSEEK_FLASH_PRICE.cost(1_000_000, 100_000, 400_000)


async def test_unknown_model_records_zero_not_a_guess() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"final_judgment": "x"}'}}],
                "usage": _USAGE,
            },
        )

    result = await _gateway(handler, "some-relay-model").invoke(_request())
    assert result.cost_usd == Decimal("0")
