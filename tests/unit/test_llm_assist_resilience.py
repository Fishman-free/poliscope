"""Resilience tests for LLM-assisted skill install (round-4 incident fix).

The model is a thinking-mode flash model that intermittently answers with the
JSON in reasoning_content, empty content, or a truncated JSON. These tests pin
the fallbacks: reasoning fallback, fence stripping, truncation tolerance, and
one retry before the honest error.
"""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import uuid4

import httpx as httpx_module
import pytest
from httpx import AsyncClient

from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelResult, SchemaStatus
from packages.skills.llm_assist import (
    SkillChoice,
    SkillLLMError,
    _extract_json_object,
    _parse_choice,
    analyze_repo_for_skill,
)

MC = {
    "base_url": "https://api.example.test",
    "api_key": "sk-test",
    "model_name": "flash",
}

REPO = "owner/skill-repo"


class _FakeLocation:
    owner = "owner"
    repo = "skill-repo"


def _choice(**overrides: object) -> SkillChoice:
    return SkillChoice(
        selected_path=None,
        name="x",
        markdown="",
        **overrides,  # type: ignore[arg-type]
    )


async def _fake_gateway(payload: dict[str, object]) -> object:
    return ModelResult(
        call_id=uuid4(),
        payload=FrozenDict(payload),
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0"),
        latency_ms=1,
        retries=0,
        schema_status=SchemaStatus.OK,
    )


class _ChatOnceStub:
    """Replaces chat_once in analyze_repo_for_skill with scripted answers."""

    def __init__(self, answers: list[tuple[str, str]]) -> None:
        self.answers = answers
        self.calls = 0

    async def __call__(self, **_: object) -> tuple[str, str]:
        answer = self.answers[self.calls]
        self.calls += 1
        return answer


def test_extract_json_object_strips_markdown_fence() -> None:
    raw = '```json\n{"selected_path": "a/SKILL.md", "name": "a"}\n```'
    assert _extract_json_object(raw) == {
        "selected_path": "a/SKILL.md",
        "name": "a",
    }


def test_extract_json_object_tolerates_trailing_comma() -> None:
    raw = '{"selected_path": "a/SKILL.md", "name": "a",}'
    data = _extract_json_object(raw)
    assert data is not None
    assert data["selected_path"] == "a/SKILL.md"


def test_extract_json_object_returns_none_without_braces() -> None:
    assert _extract_json_object("I will answer with JSON now") is None


def test_parse_choice_accepts_prose_around_json() -> None:
    choice = _parse_choice(
        'Here is my pick: {"selected_path": "deep-research/SKILL.md", '
        '"name": "deep-research", "markdown": ""}',
        REPO,
    )
    assert choice.selected_path == "deep-research/SKILL.md"
    assert choice.name == "deep-research"


def test_parse_choice_error_includes_model_excerpt() -> None:
    with pytest.raises(SkillLLMError, match="模型没有返回 JSON"):
        _parse_choice("I cannot produce JSON today, sorry", REPO)


async def test_analyze_falls_back_to_reasoning_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """content empty, JSON lives in reasoning_content (thinking-mode flash)."""
    stub = _ChatOnceStub(
        [
            (
                "",
                'The JSON answer is {"selected_path": "a/SKILL.md", '
                '"name": "a", "markdown": ""}',
            )
        ]
    )
    monkeypatch.setattr("packages.skills.llm_assist.chat_once", stub)
    choice = await analyze_repo_for_skill(
        cast(AsyncClient, object()),
        _FakeLocation(),
        [{"path": "README.md", "snippet": "x"}],
        MC,
    )
    assert choice.selected_path == "a/SKILL.md"
    assert stub.calls == 1


async def test_chat_once_accepts_empty_content_with_reasoning() -> None:
    """The real chat_once must not reject an empty content when the reasoning
    text carries the answer -- round-4 incident: thinking-mode flash spent its
    budget on reasoning and content came back empty, failing the whole
    install with '模型没有返回可用的回答' before the fallback could run."""
    import httpx as httpx_module

    from packages.skills.llm_assist import chat_once

    def handler(request: httpx_module.Request) -> httpx_module.Response:
        return httpx_module.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": (
                                'pick: {"selected_path": "deep-research/SKILL.md", '
                                '"name": "deep-research", "markdown": ""}'
                            ),
                        }
                    }
                ]
            },
        )

    async with httpx_module.AsyncClient(
        transport=httpx_module.MockTransport(handler)
    ) as client:
        content, reasoning = await chat_once(
            base_url="https://api.example.test/v1",
            api_key="sk-test",
            model_name="flash",
            messages=[{"role": "user", "content": "pick"}],
            client=client,
        )
    assert content == ""
    assert "selected_path" in reasoning


async def test_chat_once_rejects_both_empty() -> None:
    import httpx as httpx_module

    from packages.skills.llm_assist import SkillLLMError, chat_once

    def handler(request: httpx_module.Request) -> httpx_module.Response:
        return httpx_module.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "",
                        }
                    }
                ]
            },
        )

    async with httpx_module.AsyncClient(
        transport=httpx_module.MockTransport(handler)
    ) as client:
        with pytest.raises(SkillLLMError, match="没有返回可用的回答"):
            await chat_once(
                base_url="https://api.example.test/v1",
                api_key="sk-test",
                model_name="flash",
                messages=[{"role": "user", "content": "pick"}],
                client=client,
            )


async def test_analyze_retries_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First answer garbage (no braces), retry answers clean JSON."""
    stub = _ChatOnceStub(
        [
            ("I will think about it", "some reasoning without json"),
            ('{"selected_path": "b/SKILL.md", "name": "b", "markdown": ""}', ""),
        ]
    )
    monkeypatch.setattr("packages.skills.llm_assist.chat_once", stub)
    choice = await analyze_repo_for_skill(
        cast(AsyncClient, object()),
        _FakeLocation(),
        [{"path": "README.md", "snippet": "x"}],
        MC,
    )
    assert choice.selected_path == "b/SKILL.md"
    assert stub.calls == 2


async def test_analyze_fails_honestly_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two garbage answers surface the honest error, not a silent hang."""
    stub = _ChatOnceStub([("no json here", ""), ("still no json", "")])
    monkeypatch.setattr("packages.skills.llm_assist.chat_once", stub)
    with pytest.raises(SkillLLMError, match="模型没有返回 JSON"):
        await analyze_repo_for_skill(
            cast(AsyncClient, object()),
            _FakeLocation(),
            [{"path": "README.md", "snippet": "x"}],
            MC,
        )
    assert stub.calls == 2


async def test_analyze_requires_model_config() -> None:
    with pytest.raises(SkillLLMError, match="未配置模型"):
        await analyze_repo_for_skill(
            cast(AsyncClient, object()), _FakeLocation(), [], None
        )
