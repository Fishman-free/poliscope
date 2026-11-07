"""A follow-up stream is complete only when the vendor confirms completion.

2026-11 regression: an answer that reached ``max_tokens`` ended in an error
frame and the half-written reply was stranded. A ``length`` stop is now
continued automatically; only a cut connection (no ``[DONE]`` and no finish
reason) or an exhausted continuation budget is reported as incomplete.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from apps.api.routers import tasks

Line = dict[str, Any] | str


def _text(content: str, finish_reason: str | None = None) -> dict[str, Any]:
    choice: dict[str, Any] = {"delta": {"content": content}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice]}


class _FakeStream:
    def __init__(self, lines: list[Line], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def aread(self) -> bytes:
        return b""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.example.test/chat/completions")
            response = httpx.Response(
                self.status_code,
                json={"error": {"message": "max_tokens exceeds the model limit"}},
                request=request,
            )
            raise httpx.HTTPStatusError("bad", request=request, response=response)

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            if isinstance(line, str) and line.startswith(":"):
                yield line  # an SSE comment, e.g. a vendor keep-alive
                continue
            yield "data: " + (line if isinstance(line, str) else json.dumps(line))


class _FakeClient:
    """Serves one scripted stream per call and records every request body."""

    def __init__(self, scripts: list[_FakeStream]) -> None:
        self._scripts = scripts
        self.bodies: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, *_args: object, **kwargs: Any) -> _FakeStream:
        self.bodies.append(kwargs["json"])
        return self._scripts[len(self.bodies) - 1]


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


async def _run(
    monkeypatch: pytest.MonkeyPatch, scripts: list[_FakeStream]
) -> tuple[str, _FakeClient]:
    async def prepare(*_args: object) -> tuple[object, ...]:
        return ("https://api.example.test", "secret", "model", "system", "user", ())

    client = _FakeClient(scripts)
    monkeypatch.setattr(tasks, "_prepare_followup", prepare)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)
    response = await tasks.follow_up_stream(
        uuid4(), object(), None, None, None, _FakeRequest()  # type: ignore[arg-type]
    )
    received = [
        frame.decode() if isinstance(frame, bytes) else str(frame)
        async for frame in response.body_iterator
    ]
    return "".join(received), client


def _answer_text(frames: str) -> str:
    text = ""
    for frame in frames.split("\n\n"):
        if frame.startswith("data: {"):
            text += json.loads(frame[len("data: ") :]).get("text", "")
    return text


async def test_cut_connection_is_reported_as_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames, _ = await _run(monkeypatch, [_FakeStream([_text("partial")])])
    assert "event: error" in frames
    assert "data: [DONE]" not in frames


async def test_complete_stream_ends_with_done(monkeypatch: pytest.MonkeyPatch) -> None:
    frames, client = await _run(
        monkeypatch, [_FakeStream([_text("complete"), "[DONE]"])]
    )
    assert "event: error" not in frames
    assert "data: [DONE]" in frames
    assert client.bodies[0]["max_tokens"] == tasks.FOLLOWUP_MAX_TOKENS


async def test_length_stop_is_continued_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames, client = await _run(
        monkeypatch,
        [
            _FakeStream([_text("第一段", "length"), "[DONE]"]),
            _FakeStream([_text("第二段", "stop"), "[DONE]"]),
        ],
    )
    assert "event: error" not in frames
    assert "event: continued" in frames
    assert frames.rstrip().endswith("data: [DONE]")
    assert _answer_text(frames) == "第一段第二段"
    # The continuation call carries the partial answer and the instruction.
    second = client.bodies[1]["messages"]
    assert second[-2] == {"role": "assistant", "content": "第一段"}
    assert second[-1]["content"] == tasks.FOLLOWUP_CONTINUE_PROMPT


async def test_exhausted_continuations_say_truncated_without_erroring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = tasks.MAX_FOLLOWUP_CONTINUATIONS + 1
    frames, client = await _run(
        monkeypatch,
        [_FakeStream([_text(f"s{i}", "length"), "[DONE]"]) for i in range(calls)],
    )
    assert len(client.bodies) == calls
    assert "event: truncated" in frames
    assert "event: error" not in frames
    assert "data: [DONE]" in frames
    assert _answer_text(frames) == "".join(f"s{i}" for i in range(calls))


async def test_rejected_max_tokens_retries_without_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames, client = await _run(
        monkeypatch,
        [
            _FakeStream([], status_code=400),
            _FakeStream([_text("ok", "stop"), "[DONE]"]),
        ],
    )
    assert "event: error" not in frames
    assert "max_tokens" in client.bodies[0]
    assert "max_tokens" not in client.bodies[1]


async def test_reasoning_phase_relays_liveness_not_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks, "FOLLOWUP_PROGRESS_INTERVAL_SECONDS", 0.0)
    thinking = {"choices": [{"delta": {"reasoning_content": "私有推理"}}]}
    frames, _ = await _run(
        monkeypatch,
        [_FakeStream([thinking, ": keep-alive", _text("答", "stop"), "[DONE]"])],
    )
    assert "event: thinking" in frames
    assert '"chars": 4' in frames
    # The reasoning text itself never reaches the browser (CLAUDE.md 11).
    assert "私有推理" not in frames
    assert _answer_text(frames) == "答"
