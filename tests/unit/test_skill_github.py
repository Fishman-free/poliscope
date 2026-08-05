"""GitHub skill URL parsing and SKILL.md download logic.

The download path is exercised against a stubbed transport (no network in
unit tests): parsing covers the four URL shapes, the frontmatter name is
extracted honestly, and every failure mode raises SkillFetchError with a
reason instead of fabricating a skill.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from packages.skills.github import (
    SkillFetchError,
    fetch_skill_markdown,
    parse_skill_url,
)

MARKDOWN = """---
name: my-skill
description: A test skill
---
# My Skill
Instructions for the council.
"""


def _json_response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _file_payload(content: str) -> dict[str, object]:
    return {
        "name": "SKILL.md",
        "path": "SKILL.md",
        "encoding": "base64",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }


def test_parse_plain_repo_url() -> None:
    location = parse_skill_url("https://github.com/owner/skill-name")
    assert (location.owner, location.repo) == ("owner", "skill-name")
    assert location.branch == "HEAD"
    assert location.subpath == ""


def test_parse_git_suffix() -> None:
    location = parse_skill_url("https://github.com/owner/skill-name.git")
    assert location.repo == "skill-name"


def test_parse_tree_url_with_branch_and_subpath() -> None:
    location = parse_skill_url(
        "https://github.com/owner/skill-name/tree/main/skills/foo"
    )
    assert location.branch == "main"
    assert location.subpath == "skills/foo"


def test_parse_blob_url() -> None:
    location = parse_skill_url(
        "https://github.com/owner/skill-name/blob/main/SKILL.md"
    )
    assert location.branch == "main"
    assert location.subpath == "SKILL.md"


def test_parse_rejects_non_github_urls() -> None:
    for bad in (
        "https://gitlab.com/owner/skill",
        "not a url",
        "https://github.com/onlyowner",
    ):
        with pytest.raises(SkillFetchError, match="GitHub repository URL"):
            parse_skill_url(bad)


async def test_fetch_uses_frontmatter_name_from_root_skill() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("contents/SKILL.md"):
            return _json_response(_file_payload(MARKDOWN))
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        name, markdown = await fetch_skill_markdown(
            client, "https://github.com/owner/skill-name"
        )

    assert name == "my-skill"
    assert "Instructions for the council." in markdown
    # Root SKILL.md tried first; no fallback needed.
    assert calls[0].endswith("contents/SKILL.md")


async def test_fetch_falls_back_to_skills_directory() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("contents/SKILL.md"):
            return _json_response({"message": "not found"}, status=404)
        if request.url.path.endswith("contents/skills/SKILL.md"):
            return _json_response(_file_payload(MARKDOWN))
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        name, markdown = await fetch_skill_markdown(
            client, "https://github.com/owner/skill-name"
        )

    assert name == "my-skill"
    assert markdown.startswith("---")


async def test_fetch_uses_repo_name_without_frontmatter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(_file_payload("# No frontmatter here"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        name, _markdown = await fetch_skill_markdown(
            client, "https://github.com/owner/skill-name"
        )

    assert name == "skill-name"


async def test_fetch_fails_honestly_when_no_skill_exists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SkillFetchError, match="no SKILL.md found"):
            await fetch_skill_markdown(
                client, "https://github.com/owner/skill-name"
            )


async def test_fetch_rejects_oversized_skill() -> None:
    big = "x" * (2 * 1024 * 1024 + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(_file_payload(big))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SkillFetchError, match="2 MB"):
            await fetch_skill_markdown(
                client, "https://github.com/owner/skill-name"
            )


async def test_fetch_rejects_github_api_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"message": "rate limit"}, status=403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SkillFetchError, match="rate limit"):
            await fetch_skill_markdown(
                client, "https://github.com/owner/skill-name"
            )
