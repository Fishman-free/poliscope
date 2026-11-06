"""Skill lifecycle: download from GitHub, persist, serve the council.

``add_from_url`` owns the whole "a URL becomes a remembered skill" story:
parse -> fetch -> write the SKILL.md into the account's directory *and* the
row -> record the row. ``ensure_downloaded`` is the worker's side: a task
enabled this skill, so the council gets its text. The row's ``content`` is
the source of truth; the file is a per-container cache. Before 0027 the file
was the only copy, and because the API and worker have separate filesystems
the worker re-downloaded from GitHub on every run -- a GitHub outage then
aborted research before the first phase. Now a missing file is re-cached
from the row, and the network is only consulted for a row that predates the
column.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from packages.skills.github import SkillFetchError, fetch_skills_from_repo
from packages.skills.repository import (
    SkillAlreadyAdded,
    SkillsRepository,
    StoredSkill,
)

SKILLS_ROOT_ENV = "POLISCOPE_SKILLS_ROOT"
DEFAULT_SKILLS_ROOT = "/tmp/poliscope-skills"


class SkillsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        client: httpx.AsyncClient | None = None,
        root: str | None = None,
    ) -> None:
        self._session = session
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._root = Path(root or os.environ.get(SKILLS_ROOT_ENV, DEFAULT_SKILLS_ROOT))

    async def add_from_url(
        self,
        user_id: UUID,
        url: str,
        llm_assistant: Callable[..., Awaitable[object]] | None = None,
    ) -> tuple[StoredSkill, ...]:
        """Download a GitHub skill (or every skill of a collection) and
        remember them for the account.

        ``llm_assistant`` is the round-4 smart-install hook: when the repo has
        no SKILL.md at any conventional location or tree depth, the caller may
        provide an assistant (the API layer wires the account's model
        settings) that picks an existing markdown file or synthesises a skill
        summary instead of failing. A repo carrying several SKILL.md files
        installs **all** of them as separate skills (round-4 request: when
        the model cannot pick one, install everything).

        Returns the added skills; a skill already present for this account
        (same URL and name) is skipped rather than failing the batch. When
        every skill was already present, raises ``SkillAlreadyAdded``.
        """
        # Conditional keyword keeps the call compatible with test fakes that
        # monkeypatch fetch_skills_from_repo with the two-argument signature.
        if llm_assistant is None:
            fetched = await fetch_skills_from_repo(self._client, url)
        else:
            fetched = await fetch_skills_from_repo(
                self._client, url, llm_assistant=llm_assistant
            )
        repository = SkillsRepository(self._session)
        added: list[StoredSkill] = []
        # Distinct skills may share a frontmatter name; keep each directory
        # unique by suffixing collisions (name-2, name-3, ...).
        used: dict[str, int] = {}
        for name, markdown in fetched:
            base = _safe_name(name)
            count = used.get(base, 0)
            used[base] = count + 1
            directory_name = base if count == 0 else f"{base}-{count + 1}"
            directory = self._root / str(user_id) / directory_name
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / "SKILL.md"
            target.write_text(markdown, encoding="utf-8")
            try:
                added.append(
                    await repository.add(
                        user_id,
                        name=name,
                        github_url=url.strip(),
                        downloaded_path=str(target),
                        content=markdown,
                    )
                )
            except SkillAlreadyAdded:
                continue
        if not added:
            raise SkillAlreadyAdded(url.strip())
        return tuple(added)

    async def ensure_downloaded(self, skill: StoredSkill) -> str:
        """Return the skill's markdown from the most durable copy available.

        Order: the row's stored ``content`` -> the file cache -> GitHub. The
        first two are local and cannot fail because of the network, which is
        what makes a run start even when GitHub is unreachable from the
        server. A legacy row (content NULL) is backfilled from whichever
        source answered, so the next run does not need the network either.

        Only the path that actually calls GitHub can raise ``SkillFetchError``;
        the worker treats that as a degraded run (skill omitted) rather than a
        failed one, because a skill is researcher instruction, not evidence.
        """
        path = Path(skill.downloaded_path)
        if skill.content:
            self._write_cache(path, skill.content)
            return skill.content
        if path.is_file():
            try:
                markdown = path.read_text(encoding="utf-8")
            except OSError:
                pass  # Unreadable cache; fall through to the recorded URL.
            else:
                await SkillsRepository(self._session).set_content(
                    skill.id, markdown
                )
                return markdown
        try:
            # A collection repo carries several skills: re-download the one
            # whose name this row records, falling back to the first entry.
            fetched = await fetch_skills_from_repo(
                self._client, skill.github_url
            )
        except SkillFetchError:
            raise
        except httpx.HTTPError as error:
            raise SkillFetchError(
                f"无法从 GitHub 获取技能内容（{type(error).__name__}）：{error}"
            ) from error
        markdown = next(
            (body for name, body in fetched if name == skill.name),
            fetched[0][1],
        )
        self._write_cache(path, markdown)
        await SkillsRepository(self._session).set_content(skill.id, markdown)
        return markdown

    @staticmethod
    def _write_cache(path: Path, markdown: str) -> None:
        """Best-effort disk cache write; the row already holds the truth."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
        except OSError:
            # A read-only or full filesystem must not cost the run its
            # instructions -- the caller already has the markdown.
            pass


def _safe_name(name: str) -> str:
    """A directory-safe skill name: letters, digits, and a small separator set."""
    cleaned = "".join(
        char if char.isalnum() or char in "-_." else "_" for char in name
    )
    return cleaned.strip("._") or "skill"


__all__ = [
    "DEFAULT_SKILLS_ROOT",
    "SKILLS_ROOT_ENV",
    "SkillsService",
]
