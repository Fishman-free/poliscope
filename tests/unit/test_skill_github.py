"""GitHub skill URL parsing and SKILL.md download logic.

The download path is exercised against a stubbed transport (no network in
unit tests): parsing covers the four URL shapes, the frontmatter name is
extracted honestly, and every failure mode raises SkillFetchError with a
reason instead of fabricating a skill.

File downloads go through raw.githubusercontent.com (no API quota); the
recursive tree scan uses the API and transparently falls back to a whole-repo
tarball download when the API is rate limited (403).
"""

from __future__ import annotations

import io
import tarfile

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


def _text_response(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=content.encode("utf-8"))


def _tarball_response(skill_paths: list[str]) -> httpx.Response:
    """A real gzip tarball whose members end in SKILL.md, as codeload sends it."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for path in skill_paths:
            member = tarfile.TarInfo(f"repo-abc/{path}")
            if path.endswith("SKILL.md"):
                # Frontmatter name = the SKILL.md's parent directory, so the
                # installed name is checkable per skill.
                parent = path.rsplit("/", 2)[-2]
                body = (
                    f"---\nname: {parent}\n---\n# Skill\nInstructions."
                ).encode()
            else:
                body = b""
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
        archive.addfile(tarfile.TarInfo("repo-abc/README.md"))
    return httpx.Response(200, content=buf.getvalue())


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
        if request.url.path.endswith("/HEAD/SKILL.md"):
            return _text_response(MARKDOWN)
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        name, markdown = await fetch_skill_markdown(
            client, "https://github.com/owner/skill-name"
        )

    assert name == "my-skill"
    assert "Instructions for the council." in markdown
    # Root SKILL.md tried first via raw.githubusercontent.com; no fallback.
    assert calls[0].endswith("/HEAD/SKILL.md")


async def test_fetch_falls_back_to_skills_directory() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/HEAD/SKILL.md"):
            return _json_response({"message": "not found"}, status=404)
        if request.url.path.endswith("/HEAD/skills/SKILL.md"):
            return _text_response(MARKDOWN)
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        name, markdown = await fetch_skill_markdown(
            client, "https://github.com/owner/skill-name"
        )

    assert name == "my-skill"
    assert markdown.startswith("---")


async def test_fetch_uses_repo_name_without_frontmatter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response("# No frontmatter here")

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
        return _text_response(big)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SkillFetchError, match="2 MB"):
            await fetch_skill_markdown(
                client, "https://github.com/owner/skill-name"
            )


async def test_fetch_rejects_rate_limited_raw_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"message": "rate limit"}, status=403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SkillFetchError, match="POLISCOPE_GITHUB_TOKEN"):
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
        if path.endswith(
            "/HEAD/SKILL.md"
        ) or path.endswith("/HEAD/skills/SKILL.md"):
            return _json_response({"message": "not found"}, status=404)
        if "git/trees" in path:
            return _json_response(
                _tree_payload(["README.md", "academic/skills/SKILL.md"])
            )
        if path.endswith("/HEAD/academic/skills/SKILL.md"):
            return _text_response(MARKDOWN)
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
        if path.endswith("/HEAD/guides/research-methods.md"):
            return _text_response("# Research Methods\nFollow these.")
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
        if path.endswith("/HEAD/README.md"):
            return _text_response("# Academic Research\nTools and methods.")
        if "contents/" in path:
            return _json_response(_directory_payload(["README.md"]))
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
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SkillFetchError, match="no SKILL.md found"):
            await fetch_skill_markdown(
                client, "https://github.com/owner/skill-name"
            )


async def test_fetch_collection_returns_all_skills() -> None:
    """A skill *collection* (several SKILL.md files) installs every skill as
    its own entry -- round-4 request: when the model cannot pick one, all are
    downloaded instead of failing."""
    names = ["academic-paper-reviewer", "academic-paper", "deep-research"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # Only the conventional candidates miss; tree-found SKILL.md files
        # must reach the download branch below.
        if any(
            path.endswith(fixed)
            for fixed in (
                "/HEAD/SKILL.md",
                "/HEAD/skills/SKILL.md",
                "/HEAD/.claude/skills/SKILL.md",
            )
        ):
            return _json_response({"message": "not found"}, status=404)
        if "git/trees" in path:
            return _json_response(
                _tree_payload(
                    [f"{name}/SKILL.md" for name in names] + ["README.md"]
                )
            )
        for name in names:
            if path.endswith(f"/HEAD/{name}/SKILL.md"):
                return _text_response(
                    f"---\nname: {name}\n---\n# {name}\nInstructions."
                )
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        from packages.skills.github import fetch_skills_from_repo

        skills = await fetch_skills_from_repo(
            client, "https://github.com/owner/skill-collection"
        )

    assert [name for name, _markdown in skills] == names
    assert all("Instructions." in markdown for _name, markdown in skills)


# --------------------------------------------------------------------------
# Round-5 resilience: tarball fallback when the API is rate limited
# --------------------------------------------------------------------------


async def test_fetch_falls_back_to_tarball_when_api_rate_limited() -> None:
    """The API tree listing answers 403 (anonymous quota exhausted on the
    server's egress IP): the whole-repo tarball (codeload, no API quota) is
    scanned locally instead, so a rate-limited install does not fail."""
    names = ["academic-paper-reviewer", "academic-paper", "deep-research"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "raw.githubusercontent.com":
            # The conventional candidates miss (skills live in subdirs);
            # tree-found SKILL.md files are downloaded via raw, same
            # frontmatter shape as the tarball.
            if path.endswith(
                (
                    "/HEAD/SKILL.md",
                    "/HEAD/skills/SKILL.md",
                    "/HEAD/.claude/skills/SKILL.md",
                )
            ):
                return _json_response({"message": "not found"}, status=404)
            parent = path.rsplit("/", 2)[-2]
            return _text_response(
                f"---\nname: {parent}\n---\n# Skill\nInstructions."
            )
        if "git/trees" in path:
            return _json_response({"message": "rate limit"}, status=403)
        if "codeload.github.com" in request.url.host:
            return _tarball_response([f"{name}/SKILL.md" for name in names])
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        from packages.skills.github import fetch_skills_from_repo

        skills = await fetch_skills_from_repo(
            client, "https://github.com/owner/skill-collection"
        )

    assert [name for name, _markdown in skills] == names


async def test_fetch_tarball_ignores_non_skill_files() -> None:
    """The tarball scan picks only SKILL.md members; README and deep docs do
    not become skills."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "raw.githubusercontent.com":
            if path.endswith(
                (
                    "/HEAD/SKILL.md",
                    "/HEAD/skills/SKILL.md",
                    "/HEAD/.claude/skills/SKILL.md",
                )
            ):
                return _json_response({"message": "not found"}, status=404)
            parent = path.rsplit("/", 2)[-2]
            return _text_response(
                f"---\nname: {parent}\n---\n# Skill\nInstructions."
            )
        if "git/trees" in path:
            return _json_response({"message": "rate limit"}, status=403)
        if "codeload.github.com" in request.url.host:
            return _tarball_response(
                [
                    "skills/one/SKILL.md",
                    "docs/translations/SKILL.md",
                    "README.md",
                    "paper.md",
                ]
            )
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        from packages.skills.github import fetch_skills_from_repo

        skills = await fetch_skills_from_repo(
            client, "https://github.com/owner/skill-collection"
        )

    # Shallowest-first ordering: equal depth sorts lexically (docs < skills).
    assert [name for name, _markdown in skills] == ["translations", "one"]


async def test_fetch_tarball_download_error_is_honest() -> None:
    """A tarball that cannot be downloaded fails with the reason -- no
    fabricated skill."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("SKILL.md"):
            return _json_response({"message": "not found"}, status=404)
        if "git/trees" in path:
            return _json_response({"message": "rate limit"}, status=403)
        if "codeload.github.com" in request.url.host:
            return _json_response({"message": "server error"}, status=500)
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        from packages.skills.github import fetch_skills_from_repo

        with pytest.raises(SkillFetchError, match="tarball"):
            await fetch_skills_from_repo(
                client, "https://github.com/owner/skill-collection"
            )


async def test_fetch_sends_token_to_api_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POLISCOPE_GITHUB_TOKEN rides on API requests (tree listing, directory
    listing) as a bearer header -- it lifts the anonymous 60/hour quota."""
    monkeypatch.setenv("POLISCOPE_GITHUB_TOKEN", "ghp_test_token")
    sent_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "api.github.com" in request.url.host and "git/trees" in path:
            sent_auth.append(request.headers.get("authorization"))
            return _json_response(
                _tree_payload(["nested/SKILL.md", "README.md"])
            )
        if path.endswith("/HEAD/nested/SKILL.md"):
            return _text_response(MARKDOWN)
        if path.endswith("SKILL.md"):
            return _json_response({"message": "not found"}, status=404)
        return _json_response({"message": "not found"}, status=404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        from packages.skills.github import fetch_skills_from_repo

        skills = await fetch_skills_from_repo(
            client, "https://github.com/owner/skill-name"
        )

    assert [name for name, _markdown in skills] == ["my-skill"]
    assert sent_auth == ["Bearer ghp_test_token"]
