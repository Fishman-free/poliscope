"""Tests for the streaming path of the OpenAI-compatible gateway.

Same discipline as the non-streaming tests: no live credentials, every call
goes through ``httpx.MockTransport``. The streaming path is the live view's
wire -- relayed deltas must match what the vendor actually sent, in order,
and the returned ``ModelResult`` must be identical in shape to a plain
``invoke`` result so callers can treat streaming as a pure optimisation.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from packages.models.contracts import (
    ModelClass,
    ModelMessage,
    ModelRequest,
    SchemaStatus,
    StreamEvent,
)
from packages.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleModelGateway,
)
from packages.models.phase_schemas import PHASE_OUTPUT_JSON_SCHEMAS

FULL_ENV = {
    "POLISCOPE_MODEL_API_KEY": "test-key",
    "POLISCOPE_MODEL_BASE_URL": "https://api.example.test/v1",
    "POLISCOPE_MODEL_NAME": "deepseek-chat",
}


def _request(output_schema: str = "FinalJudgment") -> ModelRequest:
    return ModelRequest(
        task_id=uuid4(),
        actor="theory_builder",
        purpose="final_rejudgment",
        model_class=ModelClass.STRONG_REASONING,
        messages=(ModelMessage(role="user", content="give your final judgment"),),
        output_schema=output_schema,
        evidence_refs=(),
    )


def _sse_gateway(
    chunks: list[dict[str, object]],
) -> OpenAICompatibleModelGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        body += "data: [DONE]\n\n"
        return httpx.Response(200, text=body)

    config = OpenAICompatibleConfig.from_env(FULL_ENV)
    gateway = OpenAICompatibleModelGateway(
        config,
        client=httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(handler),
        ),
    )
    return gateway


async def test_stream_invoke_relays_reasoning_then_content_in_order() -> None:
    chunks: list[dict[str, object]] = [
        {"choices": [{"delta": {"reasoning_content": "先检查反向因果，"}}]},
        {"choices": [{"delta": {"reasoning_content": "再看样本代表性。"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "content": '{"final_judgment": "条件化支持"}'
                    }
                }
            ]
        },
        {
            "choices": [{"delta": {}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    ]
    gateway = _sse_gateway(chunks)

    received: list[StreamEvent] = []

    async def collect(event: StreamEvent) -> None:
        received.append(event)

    result = await gateway.stream_invoke(_request(), collect)

    assert [event.kind for event in received] == [
        "reasoning",
        "reasoning",
        "token",
        "done",
    ]
    assert received[0].text == "先检查反向因果，"
    assert received[1].text == "再看样本代表性。"
    assert "final_judgment" in received[2].text

    assert result.schema_status is SchemaStatus.OK
    assert result.reasoning == "先检查反向因果，再看样本代表性。"
    assert result.payload["final_judgment"] == "条件化支持"
    assert result.input_tokens == 10
    assert result.output_tokens == 20


async def test_stream_invoke_raises_on_transport_error() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    config = OpenAICompatibleConfig.from_env(FULL_ENV)
    gateway = OpenAICompatibleModelGateway(
        config,
        client=httpx.AsyncClient(
            base_url=config.base_url,
            transport=httpx.MockTransport(failing),
        ),
    )
    async def noop(_event: StreamEvent) -> None:
        return None

    with pytest.raises(httpx.HTTPStatusError):
        await gateway.stream_invoke(_request(), noop)


async def test_stream_invoke_raises_on_schema_rejection() -> None:
    """Garbage content must raise so the caller can fall back to invoke's
    schema-repair path -- the live view keeps the deltas, the retry owns the
    correction."""
    chunks: list[dict[str, object]] = [
        {"choices": [{"delta": {"content": "not json at all"}}]},
    ]
    gateway = _sse_gateway(chunks)

    async def noop(_event: StreamEvent) -> None:
        return None

    with pytest.raises(ValueError):
        await gateway.stream_invoke(_request(), noop)


def test_streamed_schema_is_registered() -> None:
    """The streamed output schema must be a registered phase schema, same as
    the non-streaming path's."""
    assert "FinalJudgment" in PHASE_OUTPUT_JSON_SCHEMAS
