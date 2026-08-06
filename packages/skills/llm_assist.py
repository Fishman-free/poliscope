"""LLM-assisted skill installation: when a GitHub repo has no SKILL.md, the
configured model reads the repo's markdown files and either picks the one
that carries the skill or synthesises a skill summary from them.

This is the "让大模型来安装" path the researcher asked for (round 4): the
system must not fail on a repo that simply structures its skill differently.
Two hard rules keep it honest:

* The model's answer is a **choice or a summary**, never a fabrication of a
  "real" SKILL.md -- a synthesized skill is stored with the repo's actual
  content as its source, and the UI/API error path still reports failure
  when the repo has nothing installable.
* The API key never appears in any error message (CLAUDE.md 16), mirroring
  ``packages/models/endpoint_config.probe_endpoint``'s error classification.

The call is a single small non-streaming chat/completions request (max_tokens
~800, 30s timeout) -- deliberately not routed through the Model Gateway,
which lives in the worker process; the API process already talks to the model
directly for connection probing, and this keeps the same bounded shape.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

LLM_TIMEOUT_SECONDS = 30.0
LLM_MAX_TOKENS = 800
SNIPPET_LIMIT_CHARS = 8000

_REPO_INTRO = (
    "You are installing a research skill from a GitHub repository that has no "
    "SKILL.md file at any conventional location. You are given the repository's "
    "markdown files (path + first ~2 KB each)."
    "\n\n"
    "Respond with JSON only:\n"
    '{"selected_path": "<path of the file that carries the skill, or null>", '
    '"name": "<skill name, e.g. academic-research>", '
    '"markdown": "<the skill content to install if selected_path is null; '
    'otherwise empty string>"}'
    "\n\n"
    "Rules: prefer selecting an existing file whose content is actual "
    "instructions/methodology over synthesizing. Only synthesize when no file "
    "is a suitable skill, and base the synthesized markdown strictly on the "
    "snippets given. Never invent content. The skill content should be a "
    "concise, actionable markdown document a researcher agent can follow."
)


class SkillLLMError(Exception):
    """Raised when the model call itself fails; never contains the API key."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SkillChoice:
    """The LLM's answer for a repo with no SKILL.md.

    ``selected_path`` names an existing markdown file to install as the skill;
    ``markdown`` carries synthesized content when no file was selected. Exactly
    one of the two is non-empty.
    """

    selected_path: str | None
    name: str
    markdown: str = ""
    usage_note: str = ""


def _classify_error(error: Exception, model_name: str) -> str:
    """Classify a chat call failure without ever echoing the API key."""
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return "API Key 无效或无权访问"
        if status == 404:
            return "模型名不存在，或 Base URL 路径不对"
        return f"模型服务返回 HTTP {status}"
    if isinstance(error, httpx.TimeoutException):
        return "模型调用超时"
    return "无法连接模型服务"


async def chat_once(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    messages: list[dict[str, str]],
    client: httpx.AsyncClient | None = None,
    max_tokens: int = LLM_MAX_TOKENS,
) -> str:
    """One small non-streaming chat/completions call, returning the answer text.

    Errors are classified and raised as :class:`SkillLLMError`; the API key is
    never part of any message.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model_name,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = (
            (payload.get("choices") or [{}])[0]
            .get("message", {})
            .get("content")
        )
        if not isinstance(content, str) or not content.strip():
            raise SkillLLMError("模型没有返回可用的回答")
        return content
    except SkillLLMError:
        raise
    except Exception as error:
        raise SkillLLMError(_classify_error(error, model_name)) from error
    finally:
        if owns_client:
            await client.aclose()


def _parse_choice(raw: str, repo: str) -> SkillChoice:
    """Parse the model's JSON answer, tolerating stray prose around it."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise SkillLLMError("模型没有返回 JSON 格式的选择结果")
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError as error:
        raise SkillLLMError("模型返回的 JSON 无法解析") from error
    selected = data.get("selected_path")
    if isinstance(selected, str) and selected:
        return SkillChoice(
            selected_path=selected.strip("/"),
            name=str(data.get("name") or repo).strip() or repo,
            usage_note="LLM 从仓库现有文件中选择了该文件作为技能",
        )
    markdown = data.get("markdown")
    if isinstance(markdown, str) and markdown.strip():
        return SkillChoice(
            selected_path=None,
            name=str(data.get("name") or repo).strip() or repo,
            markdown=markdown,
            usage_note="仓库没有 SKILL.md，由模型基于仓库内容生成的技能摘要",
        )
    raise SkillLLMError("模型既没有选择文件也没有生成内容")


def _build_messages(repo: str, files: list[dict[str, str]]) -> list[dict[str, str]]:
    parts = [f"Repository: {repo}"]
    total = 0
    for item in files:
        path = str(item.get("path", "?"))
        snippet = str(item.get("snippet", ""))
        budget = SNIPPET_LIMIT_CHARS - total
        if budget <= 0:
            break
        parts.append(f"\n--- {path} ---\n{snippet[:budget]}")
        total += min(len(snippet), budget)
    return [
        {"role": "system", "content": _REPO_INTRO},
        {"role": "user", "content": "\n".join(parts)},
    ]


async def analyze_repo_for_skill(
    client: httpx.AsyncClient,
    location: Any,
    files: list[dict[str, str]],
    model_config: Mapping[str, object] | None = None,
) -> SkillChoice:
    """Have the configured model pick or synthesize the skill for a repo.

    ``model_config`` is ``{"base_url", "api_key", "model_name"}`` as read from
    the account's model settings; ``None`` (no model configured) raises a
    honest :class:`SkillFetchError`-style message the API layer surfaces as
    the 422 detail.
    """
    if model_config is None:
        raise SkillLLMError(
            "该仓库没有标准的 SKILL.md，且未配置模型，无法智能解析仓库结构；"
            "请先在模型设置中配置模型，或换一个带 SKILL.md 的 skill 仓库"
        )
    base_url = str(model_config.get("base_url") or "")
    api_key = str(model_config.get("api_key") or "")
    model_name = str(model_config.get("model_name") or "")
    if not base_url or not api_key or not model_name:
        raise SkillLLMError(
            "该仓库没有标准的 SKILL.md，且模型设置不完整，无法智能解析；"
            "请先在模型设置中配置模型"
        )
    repo = f"{location.owner}/{location.repo}"
    raw = await chat_once(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        messages=_build_messages(repo, files),
        client=client,
    )
    return _parse_choice(raw, repo)


__all__ = ["SkillChoice", "SkillLLMError", "analyze_repo_for_skill", "chat_once"]
