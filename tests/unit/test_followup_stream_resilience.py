"""A follow-up stream is complete only when the vendor confirms completion."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from apps.api.routers import tasks


@pytest.mark.parametrize(
    ("vendor_lines", "expected"),
    [
        ([{"choices": [{"delta": {"content": "partial"}}]}], "error"),
        (
            [
                {
                    "choices": [
                        {"delta": {"content": "partial"}, "finish_reason": "length"}
                    ]
                },
                "[DONE]",
            ],
            "error",
        ),
        ([{"choices": [{"delta": {"content": "complete"}}]}, "[DONE]"], "[DONE]"),
    ],
)
async def test_followup_stream_completion_contract(
    monkeypatch: pytest.MonkeyPatch,
    vendor_lines: list[dict[str, Any] | str],
    expected: str,
) -> None:
    async def prepare(*_args: object) -> tuple[object, ...]:
        return ("https://api.example.test", "secret", "model", "system", "user", ())

    class FakeStream:
        async def __aenter__(self) -> FakeStream:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self) -> AsyncIterator[str]:
            for line in vendor_lines:
                yield "data: " + (line if isinstance(line, str) else json.dumps(line))

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def stream(self, *_args: object, **_kwargs: object) -> FakeStream:
            return FakeStream()

    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    monkeypatch.setattr(tasks, "_prepare_followup", prepare)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    response = await tasks.follow_up_stream(
        uuid4(), object(), None, None, None, FakeRequest()  # type: ignore[arg-type]
    )
    received = [
        frame.decode() if isinstance(frame, bytes) else str(frame)
        async for frame in response.body_iterator
    ]
    frames = "".join(received)
    if expected == "error":
        assert "event: error" in frames
        assert "data: [DONE]" not in frames
    else:
        assert "event: error" not in frames
        assert "data: [DONE]" in frames
