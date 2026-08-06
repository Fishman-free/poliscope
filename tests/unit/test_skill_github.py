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


# --------------------------------------------------------------------------
# Round-4 smart install: recursive tree scan + LLM assistant
# --------------------------------------------------------------------------


def _tree_payload(paths: list[str]) -> dict[str, object]:
    return {
        "sha": "abc",
        "truncated": False,
        "tree": [{"path": path, "type": "blob"} for path in paths],
    }


def _directory_payload(paths: list[str]) -> dict[str, object]:
    return {
        "entries": [{"path": path, "type": "file"} for path in paths],
    }


async def test_fetch_finds_skill_md_at_arbitrary_tree_depth() -> None:
    """A repo that nests its SKILL.md under an unusual directory still
    installs: the recursive tree listing is the second resolution tier."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "contents/SKILL.md" in path or "contents/skills/SKILL.md" in path:
            return _json_response({"message": "not found"}, status=404)
        if "git/trees" in path:
            return _json_response(
                _tree_payload(["README.md", "academic/skills/SKILL.md"])
            )
        if path.endswith("contents/academic/skills/SKILL.md"):
            return _json_response(_file_payload(MARKDOWN))
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        name, markdown = await fetch_skill_markdown(
            client, "https://github.com/owner/skill-name"
        )

    assert name == "my-skill"
    assert "Instructions for the council." in markdown


async def test_fetch_uses_llm_assistant_to_pick_a_file() -> None:
    """No SKILL.md anywhere: the LLM assistant selects an existing markdown
    file (e.g. the repo's real instruction document) and it is installed."""

    async def assistant(
        client: httpx.AsyncClient, location: object, files: list[dict[str, str]]
    ) -> object:
        assert files  # the candidate markdown files were collected
        return type(
            "Choice", (), {"selected_path": "guides/research-methods.md", "name": "methods"}  # noqa: E501
        )()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("SKILL.md") or "git/trees" in path:
            return _json_response({"message": "not found"}, status=404)
        if "contents/" in path and path.endswith("research-methods.md"):
            return _json_response(_file_payload("# Research Methods\nFollow these."))
        if "contents/" in path:
            # Root listing: the candidate markdown files for the assistant.
            return _json_response(_directory_payload(["README.md", "guides/research-methods.md"]))  # noqa: E501
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        name, markdown = await fetch_skill_markdown(
            client,
            "https://github.com/owner/skill-name",
            llm_assistant=assistant,
        )

    # A picked file keeps its own honest name: no frontmatter, so the repo
    # name stands in (the assistant's suggested name applies to synthesis).
    assert name == "skill-name"
    assert "Follow these." in markdown


async def test_fetch_uses_llm_assistant_generated_summary() -> None:
    """No SKILL.md and no installable file: the assistant synthesises a skill
    summary from the repo's README, clearly not pretending it found one."""

    async def assistant(
        client: httpx.AsyncClient, location: object, files: list[dict[str, str]]
    ) -> object:
        return type(
            "Choice",
            (),
            {
                "selected_path": None,
                "name": "academic-research",
                "markdown": "# Academic Research Skill\nSummarised from README.",
            },
        )()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("SKILL.md") or "git/trees" in path:
            return _json_response({"message": "not found"}, status=404)
        if "contents/" in path:
            return _json_response(
                _directory_payload(["README.md"])
                if not path.endswith("README.md")
                else _file_payload("# Academic Research\nTools and methods.")
            )
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        name, markdown = await fetch_skill_markdown(
            client,
            "https://github.com/owner/skill-name",
            llm_assistant=assistant,
        )

    assert name == "academic-research"
    assert "Summarised from README." in markdown


async def test_fetch_still_fails_honestly_without_assistant() -> None:
    """A repo with no SKILL.md anywhere and no LLM assistant configured still
    fails with the honest reason -- never a fabricated skill."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("SKILL.md"):
            return _json_response({"message": "not found"}, status=404)
        if "git/trees" in path:
            return _json_response(_tree_payload(["README.md", "docs/notes.md"]))
        if "contents/" in path:
            return _json_response(_directory_payload(["README.md"]))
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SkillFetchError, match="no SKILL.md found"):
            await fetch_skill_markdown(
                client, "https://github.com/owner/skill-name"
            )
