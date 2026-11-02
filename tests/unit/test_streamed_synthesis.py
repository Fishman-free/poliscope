"""结论整合流式输出：只要还在写就不判超时，停住了才判；失败回退一次。

生产里 16 次结论整合有 5 次撞上 300 s 的一次性上限、退回机械归档，其实
模型一直在写。现在整合走流式：实时进展里能看到结论被写出来，超时只看
「多久没有新内容」，答案不合格式时再走一次带修复的非流式调用。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from packages.kernel.contracts import FrozenDict
from packages.models.contracts import (
    ModelClass,
    ModelMessage,
    ModelRequest,
    ModelResult,
    SchemaStatus,
    StreamEvent,
)
from packages.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleModelGateway,
    StreamIdle,
)
from packages.reports.synthesis import (
    SYNTHESIS_IDLE_SECONDS,
    SYNTHESIS_REASONING,
    SYNTHESIS_TOKEN,
    _call_synthesizer,
    _SynthesisProgress,
)

_ANSWER = json.dumps(
    {"final_judgment": "主张不成立", "stance": "negative"}, ensure_ascii=False
)


def _request(**overrides: object) -> ModelRequest:
    fields: dict[str, object] = {
        "task_id": uuid4(),
        "actor": "report_synthesizer",
        "purpose": "FINAL_SYNTHESIS",
        "model_class": ModelClass.STRONG_REASONING,
        "messages": (ModelMessage(role="user", content="q"),),
        "output_schema": "FinalJudgment",
        "evidence_refs": (),
    }
    fields.update(overrides)
    return ModelRequest(**fields)  # type: ignore[arg-type]


class _Stream(httpx.AsyncByteStream):
    def __init__(self, lines: list[tuple[float, str]]) -> None:
        self._lines = lines

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for delay, line in self._lines:
            await asyncio.sleep(delay)
            yield (line + "\n\n").encode()


def _gateway(lines: list[tuple[float, str]]) -> OpenAICompatibleModelGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_Stream(lines),
        )

    config = OpenAICompatibleConfig(
        api_key="k",
        base_url="https://api.example.test/v1",
        model_names={model: "m" for model in ModelClass},
        stream_total_timeout_seconds=0.2,
    )
    return OpenAICompatibleModelGateway(
        config,
        client=httpx.AsyncClient(
            base_url=config.base_url, transport=httpx.MockTransport(handler)
        ),
    )


def _delta(**delta: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


async def _noop(_event: StreamEvent) -> None:
    return None


async def test_a_stream_that_keeps_writing_outlives_the_default_deadline() -> None:
    """Twelve deltas over ~0.36 s: past the 0.2 s default total deadline, but
    never idle. A request-level deadline and idle window let it finish."""
    lines = [(0.03, _delta(reasoning_content="思考")) for _ in range(12)]
    lines += [(0.0, _delta(content=_ANSWER)), (0.0, "data: [DONE]")]
    result = await _gateway(lines).stream_invoke(
        _request(deadline_seconds=5.0, stream_idle_seconds=0.15), _noop
    )
    assert result.payload["final_judgment"] == "主张不成立"


async def test_a_stream_that_stops_writing_is_cut_by_the_idle_window() -> None:
    """Keep-alive lines are not progress: a stalled model is noticed after
    the idle window, not after the whole deadline."""
    lines = [(0.0, _delta(reasoning_content="开始"))]
    lines += [(0.03, ": keep-alive") for _ in range(40)]
    with pytest.raises(StreamIdle):
        await _gateway(lines).stream_invoke(
            _request(deadline_seconds=5.0, stream_idle_seconds=0.1), _noop
        )


class _FakeAudited:
    def __init__(self, *, stream_fails: bool) -> None:
        self.stream_fails = stream_fails
        self.calls: list[str] = []
        self.stream_request: ModelRequest | None = None

    async def stream_invoke(
        self, request: ModelRequest, on_event: object
    ) -> ModelResult:
        self.calls.append("stream")
        self.stream_request = request
        if self.stream_fails:
            raise ValueError("streamed schema errors: $.steps: missing")
        await on_event(StreamEvent(kind="reasoning", text="先看证据"))  # type: ignore[operator]
        await on_event(StreamEvent(kind="token", text='{"bottom_line"'))  # type: ignore[operator]
        return _result()

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.calls.append("invoke")
        return _result()


def _result() -> ModelResult:
    return ModelResult(
        call_id=uuid4(),
        payload=FrozenDict({"bottom_line": "x"}),
        input_tokens=1,
        output_tokens=1,
        cost_usd=Decimal("0"),
        latency_ms=1,
        retries=0,
        schema_status=SchemaStatus.OK,
    )


async def test_the_synthesis_streams_to_the_live_view() -> None:
    rows: list[tuple[str, dict[str, object]]] = []
    progress = _SynthesisProgress(
        lambda kind, payload: rows.append((kind, payload)), None
    )
    gateway = _FakeAudited(stream_fails=False)

    await _call_synthesizer(gateway, _request(), progress.relay)  # type: ignore[arg-type]

    assert gateway.calls == ["stream"]
    assert gateway.stream_request is not None
    assert gateway.stream_request.stream_idle_seconds == SYNTHESIS_IDLE_SECONDS
    assert rows == [
        (SYNTHESIS_REASONING, {"text": "先看证据"}),
        (SYNTHESIS_TOKEN, {"text": '{"bottom_line"'}),
    ]


async def test_a_failed_stream_falls_back_to_the_repairing_call() -> None:
    gateway = _FakeAudited(stream_fails=True)
    result = await _call_synthesizer(gateway, _request(), None)  # type: ignore[arg-type]
    assert gateway.calls == ["stream", "invoke"]
    assert result.payload["bottom_line"] == "x"
