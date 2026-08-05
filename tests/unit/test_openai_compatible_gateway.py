"""Tests for the OpenAI-compatible Model Gateway.

No live credentials or network are used anywhere here -- every request goes
through ``httpx.MockTransport``, per the user's explicit choice to build and
verify the pluggable gateway before wiring in a real DeepSeek/LongCat/relay
key. These tests exist to prove the gateway's own logic (schema repair,
tool-call vs. raw-content parsing, cost accounting, config isolation) rather
than any vendor's behaviour.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from packages.models.contracts import (
    ModelClass,
    ModelMessage,
    ModelRequest,
    SchemaStatus,
)
from packages.models.openai_compatible import (
    API_KEY_ENV,
    BASE_URL_ENV,
    ModelGatewayConfigError,
    OpenAICompatibleConfig,
    OpenAICompatibleModelGateway,
    gateway_from_env,
)


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the retry backoff sleep so transport-retry tests stay fast.

    The gateway's own backoff timing is not what these tests verify -- they
    verify that a retry happens and is counted. A real ``asyncio.sleep`` here
    would make the suite's "about a second" promise (see README) a lie.
    """

    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr("packages.kernel.http_retry.asyncio.sleep", _instant)

FULL_ENV = {
    "POLISCOPE_MODEL_API_KEY": "test-key",
    "POLISCOPE_MODEL_BASE_URL": "https://api.example.test/v1",
    "POLISCOPE_MODEL_NAME": "deepseek-chat",
}


def _request(
    output_schema: str = "FinalJudgment",
    model_class: ModelClass = ModelClass.STRONG_REASONING,
) -> ModelRequest:
    return ModelRequest(
        task_id=uuid4(),
        actor="theory_builder",
        purpose="final_rejudgment",
        model_class=model_class,
        messages=(ModelMessage(role="user", content="give your final judgment"),),
        output_schema=output_schema,
        evidence_refs=(),
    )


def _gateway(
    handler: Callable[[httpx.Request], httpx.Response],
    **config_overrides: object,
) -> OpenAICompatibleModelGateway:
    config = OpenAICompatibleConfig.from_env(FULL_ENV)
    if config_overrides:
        config = replace(config, **config_overrides)  # type: ignore[arg-type]
    client = httpx.AsyncClient(
        base_url=config.base_url,
        transport=httpx.MockTransport(handler),
    )
    return OpenAICompatibleModelGateway(config, client=client)


def _tool_call_response(arguments: dict[str, object], **usage: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "FinalJudgment",
                                    "arguments": json.dumps(arguments),
                                }
                            }
                        ]
                    }
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 100),
                "completion_tokens": usage.get("completion_tokens", 40),
            },
        },
    )


def _content_response(content: str, **usage: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 100),
                "completion_tokens": usage.get("completion_tokens", 40),
            },
        },
    )


async def test_parses_forced_tool_call_structured_output() -> None:
    """Extraction tiers (MEDIUM/LIGHTWEIGHT) keep the forced tool_choice."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tool_choice"] == {
            "type": "function",
            "function": {"name": "FinalJudgment"},
        }
        assert body["thinking"] == {"type": "disabled"}
        return _tool_call_response({"final_judgment": "narrowed, not withdrawn"})

    gateway = _gateway(handler)
    result = await gateway.invoke(_request(model_class=ModelClass.MEDIUM))
    assert dict(result.payload) == {"final_judgment": "narrowed, not withdrawn"}
    assert result.schema_status is SchemaStatus.OK
    assert result.retries == 0


async def test_extraction_calls_keep_thinking_mode_off() -> None:
    """Regression test for a real production incident (V3.2 era).

    DeepSeek's reasoning models (e.g. ``deepseek-v4-pro``) default to
    "thinking mode", and thinking mode rejects any forced ``tool_choice`` with
    a 400 ``"Thinking mode does not support this tool_choice"`` -- confirmed
    directly against the live DeepSeek API. Extraction phases force a specific
    function via ``tool_choice`` (see the assertion above), so those calls
    must keep thinking disabled; every phase of this tier failed identically
    in production once: 14 ``SEAT_UNAVAILABLE`` events on one real task, each
    looking like a plain vendor outage but actually being this
    tool_choice/thinking-mode conflict. ``thinking: disabled`` on these
    requests is the fix; this asserts it never regresses back off.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        return _tool_call_response({"final_judgment": "thinking mode off"})

    gateway = _gateway(handler)
    result = await gateway.invoke(_request(model_class=ModelClass.LIGHTWEIGHT))
    assert dict(result.payload) == {"final_judgment": "thinking mode off"}


async def test_thinking_phases_enable_thinking_without_forced_tool_choice() -> None:
    """The four thinking-heavy phases run in thinking mode.

    That is what makes the chain of thought capturable at all -- DeepSeek
    returns it in ``reasoning_content`` only when thinking is on -- and DeepSeek
    V4 also *requires* it, since it rejects any forced ``tool_choice`` with a
    400. The tools stay defined so the model may still call them (the
    tool_calls parse path handles that); nothing forces the choice.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "enabled"}
        assert "tool_choice" not in body
        assert "tools" in body
        return _tool_call_response({"final_judgment": "thinking mode on"})

    gateway = _gateway(handler)
    result = await gateway.invoke(_request())
    assert dict(result.payload) == {"final_judgment": "thinking mode on"}
    assert result.schema_status is SchemaStatus.OK


async def test_captures_reasoning_content_on_thinking_phases() -> None:
    """DeepSeek's ``reasoning_content`` (or OpenAI-style ``reasoning``) is
    captured verbatim on the result, never summarised or dropped."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "reasoning_content": "The claim assumes exposure "
                            "precedes outcome, but every cited design is "
                            "cross-sectional.",
                            "content": '{"final_judgment": "holds"}',
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            },
        )

    gateway = _gateway(handler)
    result = await gateway.invoke(_request())
    assert "cross-sectional" in (result.reasoning or "")
    assert dict(result.payload) == {"final_judgment": "holds"}


async def test_reasoning_is_none_when_vendor_returns_none() -> None:
    """A thinking-off extraction call that returns no reasoning stays None."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response({"final_judgment": "no thinking returned"})

    gateway = _gateway(handler)
    result = await gateway.invoke(_request(model_class=ModelClass.MEDIUM))
    assert result.reasoning is None


async def test_degrades_to_thinking_mode_on_tool_choice_400() -> None:
    """DeepSeek V4 answers a forced tool_choice with a 400; the gateway
    retries the same attempt once in thinking mode without the forced choice,
    instead of surfacing a vendor outage the seat would report as absent."""

    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Thinking mode does not support this tool_choice",
                        "type": "invalid_request_error",
                    }
                },
            )
        assert body["thinking"] == {"type": "enabled"}
        assert "tool_choice" not in body
        return _tool_call_response({"final_judgment": "degraded and recovered"})

    gateway = _gateway(handler)
    result = await gateway.invoke(_request(model_class=ModelClass.MEDIUM))
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "FinalJudgment"},
    }
    assert dict(result.payload) == {"final_judgment": "degraded and recovered"}
    assert result.schema_status is SchemaStatus.OK
    assert result.retries == 1


async def test_unrelated_400_still_raises() -> None:
    """Only the tool_choice/thinking conflict degrades; any other 400 is a
    broken request and must surface as an exception, not be masked."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "model not found", "type": "invalid_request_error"}}
        )

    gateway = _gateway(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await gateway.invoke(_request(model_class=ModelClass.MEDIUM))


async def test_falls_back_to_raw_json_content_when_no_tool_call() -> None:
    """Some relays proxy models that ignore tool_choice and just answer."""

    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response(
            '```json\n{"final_judgment": "holds"}\n```',
        )

    gateway = _gateway(handler)
    result = await gateway.invoke(_request())
    assert dict(result.payload) == {"final_judgment": "holds"}
    assert result.schema_status is SchemaStatus.OK


async def test_repairs_once_then_succeeds() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _content_response("not json at all")
        return _tool_call_response({"final_judgment": "recovered"})

    gateway = _gateway(handler)
    result = await gateway.invoke(_request())
    assert calls == 2
    assert dict(result.payload) == {"final_judgment": "recovered"}
    assert result.schema_status is SchemaStatus.REPAIRED
    assert result.retries == 1


async def test_quarantines_after_repair_attempt_is_exhausted() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response("still not json")

    gateway = _gateway(handler)
    result = await gateway.invoke(_request())
    assert dict(result.payload) == {}
    assert result.schema_status is SchemaStatus.QUARANTINED


async def test_quarantines_on_missing_required_field() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _tool_call_response({"wrong_field": "x"})

    gateway = _gateway(handler)
    result = await gateway.invoke(_request())
    assert result.schema_status is SchemaStatus.QUARANTINED
    assert dict(result.payload) == {}


async def test_computes_cost_from_usage_and_configured_pricing() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _tool_call_response(
            {"final_judgment": "x"}, prompt_tokens=1_000_000, completion_tokens=500_000
        )

    gateway = _gateway(
        handler,
        price_input_per_1m_usd=Decimal("1.00"),
        price_output_per_1m_usd=Decimal("2.00"),
    )
    result = await gateway.invoke(_request())
    assert result.cost_usd == Decimal("2.00")  # 1*1.00 + 0.5*2.00


async def test_cost_defaults_to_zero_when_pricing_unconfigured() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _tool_call_response({"final_judgment": "x"}, prompt_tokens=999)

    gateway = _gateway(handler)
    result = await gateway.invoke(_request())
    assert result.cost_usd == Decimal("0")


async def test_transport_error_propagates_after_exhausting_retries() -> None:
    """A dead upstream must surface as an exception, not a fabricated result.

    ``GatewayDeliberator`` treats any exception from ``invoke`` as an absent
    seat -- the correct honest-gap behaviour when the vendor cannot be
    reached at all, as opposed to a schema failure on a real response.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    gateway = _gateway(handler)
    with pytest.raises(httpx.ConnectError):
        await gateway.invoke(_request())


async def test_retries_on_5xx_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="upstream overloaded")
        return _tool_call_response({"final_judgment": "recovered after 503"})

    gateway = _gateway(handler)
    result = await gateway.invoke(_request())
    assert calls == 2
    assert result.retries == 1
    assert dict(result.payload) == {"final_judgment": "recovered after 503"}


def test_missing_api_key_fails_construction_loudly() -> None:
    with pytest.raises(ModelGatewayConfigError, match=API_KEY_ENV):
        OpenAICompatibleConfig.from_env({})


def test_config_never_falls_back_to_anthropic_session_credentials() -> None:
    """The gateway must not read this process's own Claude Code credentials."""
    environ = {
        "ANTHROPIC_AUTH_TOKEN": "sk-session-token-not-a-poliscope-credential",
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    }
    with pytest.raises(ModelGatewayConfigError, match=API_KEY_ENV):
        OpenAICompatibleConfig.from_env(environ)


def test_missing_base_url_fails_construction_loudly() -> None:
    with pytest.raises(ModelGatewayConfigError, match=BASE_URL_ENV):
        OpenAICompatibleConfig.from_env({"POLISCOPE_MODEL_API_KEY": "k"})


def test_gateway_from_env_returns_none_when_unconfigured() -> None:
    """An operator who has not connected a vendor yet gets an honest gap."""
    assert gateway_from_env({}) is None


def test_gateway_from_env_raises_when_partially_configured() -> None:
    """A present key with missing base URL is a broken config, not a gap."""
    with pytest.raises(ModelGatewayConfigError):
        gateway_from_env({"POLISCOPE_MODEL_API_KEY": "k"})


def test_per_tier_model_names_fall_back_to_default() -> None:
    config = OpenAICompatibleConfig.from_env(
        {**FULL_ENV, "POLISCOPE_MODEL_NAME_MEDIUM": "deepseek-lite"}
    )
    assert config.model_names[ModelClass.MEDIUM] == "deepseek-lite"
    assert config.model_names[ModelClass.STRONG_REASONING] == "deepseek-chat"
    assert config.model_names[ModelClass.LIGHTWEIGHT] == "deepseek-chat"
