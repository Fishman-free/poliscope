"""Round-9 follow-up question endpoint (补充提问): a completed task can be
asked about its research, and the answer stays grounded in the brief.

The endpoint (POST /api/tasks/{task_id}/followup) rejects unfinished tasks,
answers from the Research Brief + confirmed claims via the task's own model
endpoint, and never leaks the API key. The model call is mocked here -- the
HTTP path (routing, ownership, status codes, response shape) is what we cover.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.research.models import ResearchTaskModel

FOLLOWUP_PATH = "/api/tasks/{task_id}/followup"


class _FakePost:
    """A fake response object for the mocked httpx client's .post()."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient in the follow-up endpoint."""

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(
        self, path: str, json: dict[str, object]
    ) -> _FakePost:
        self.posts.append((path, json))
        return _FakePost(
            {
                "choices": [
                    {"message": {"content": self._answer}},
                ]
            }
        )


async def _seed_completed_task(
    sessions: async_sessionmaker[AsyncSession],
    user_id: UUID,
) -> UUID:
    """A terminal task owned by the caller, with a model config so the
    follow-up can resolve its own endpoint."""
    task_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question="社交媒体使用是否会降低青少年心理健康水平？",
                status="COMPLETED_WITH_GAPS",
                created_by="followup_test",
                user_id=user_id,
                wall_clock_minutes=60,
                model_cost_usd=0,
                tool_call_limit=100,
                source_limit=50,
                user_evidence={},
                model_config={
                    "base_url": "https://api.example.com",
                    "api_key": "sk-followup-test",
                    "model_name": "qwen3.8-max",
                },
            )
        )
        await session.commit()
    return task_id


async def _seed_queued_task(
    sessions: async_sessionmaker[AsyncSession],
    user_id: UUID,
) -> UUID:
    task_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question="尚未完成的问题",
                status="QUEUED",
                created_by="followup_test",
                user_id=user_id,
                wall_clock_minutes=60,
                model_cost_usd=0,
                tool_call_limit=100,
                source_limit=50,
                user_evidence={},
            )
        )
        await session.commit()
    return task_id


async def test_followup_rejects_unfinished_task(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    response = await api_client.post(
        FOLLOWUP_PATH.format(task_id=task_id), json={"question": "结论是什么？"}
    )
    assert response.status_code == 409
    assert "尚未完成" in response.json()["detail"]


async def test_followup_answers_from_the_tasks_own_endpoint(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = await _seed_completed_task(app_sessions, UUID(account["id"]))
    fake = _FakeAsyncClient("证据指向弱正相关，但因果方向未决。")
    monkeypatch.setattr(
        "apps.api.routers.tasks.httpx.AsyncClient", lambda **kwargs: fake
    )

    response = await api_client.post(
        FOLLOWUP_PATH.format(task_id=task_id), json={"question": "结论是什么？"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert "因果方向未决" in body["answer"]
    # The request carried the task's own model name and endpoint.
    assert len(fake.posts) == 1
    _path, request_body = fake.posts[0]
    messages = request_body["messages"]
    assert isinstance(messages, list)
    assert request_body["model"] == "qwen3.8-max"
    assert messages[0]["role"] == "system"
    assert "社交媒体使用" in messages[1]["content"]


async def test_followup_rejects_empty_question(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    task_id = await _seed_completed_task(app_sessions, UUID(account["id"]))
    response = await api_client.post(
        FOLLOWUP_PATH.format(task_id=task_id), json={"question": "   "}
    )
    assert response.status_code == 422


async def test_followup_stream_returns_sse_deltas(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-10 streaming follow-up: the same grounding, delivered as deltas."""
    task_id = await _seed_completed_task(app_sessions, UUID(account["id"]))

    class _FakeStream:
        def __init__(self) -> None:
            self._lines = [
                ("data: " + '{"choices":[{"delta":{"content":"第"}}]}').encode("utf-8"),
                (
                    "data: " + '{"choices":[{"delta":{"content":"一段"}}]}'
                ).encode("utf-8"),
                b"data: [DONE]",
            ]

        def raise_for_status(self) -> None:
            return None

        async def __aenter__(self) -> _FakeStream:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def aiter_lines(self) -> Any:
            async def _gen() -> Any:
                for line in self._lines:
                    yield line.decode("utf-8")

            return _gen()

    class _FakeStreamClient:
        def __init__(self) -> None:
            self.posts: list[tuple[str, dict[str, object]]] = []

        async def __aenter__(self) -> _FakeStreamClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def stream(self, method: str, path: str, json: dict[str, object]) -> Any:
            self.posts.append((path, json))
            return _FakeStream()

    fake = _FakeStreamClient()
    monkeypatch.setattr(
        "apps.api.routers.tasks.httpx.AsyncClient", lambda **kwargs: fake
    )

    response = await api_client.post(
        f"/api/tasks/{task_id}/followup/stream", json={"question": "结论？"}
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body = response.text
    assert "第" in body
    assert "一段" in body
    assert "[DONE]" in body
    # The request streamed and carried the task's own model name.
    assert fake.posts
    _path, request_body = fake.posts[0]
    assert request_body["stream"] is True
    assert request_body["model"] == "qwen3.8-max"


async def test_followup_stream_rejects_unfinished_task(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    task_id = await _seed_queued_task(app_sessions, UUID(account["id"]))
    response = await api_client.post(
        f"/api/tasks/{task_id}/followup/stream", json={"question": "结论？"}
    )
    assert response.status_code == 409
